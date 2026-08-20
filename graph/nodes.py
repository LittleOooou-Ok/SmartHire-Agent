"""
LangGraph node functions. Each node receives the full HRWorkflowState
and returns a partial dict that LangGraph merges back into the state.
"""
import time
from datetime import datetime, timezone
from langchain_core.messages import AIMessage
from models.state import HRWorkflowState
from agents.resume_shortlister import resume_shortlister_agent
from agents.pre_screener import pre_screener_agent
from agents.email_interview_scheduler import email_interview_scheduler_agent
from agents.onboarding_agent import onboarding_agent
from tools.storage_tools import save_session_state, save_shortlisted_candidates
from core.logging import get_logger
from core.observability import build_metric
from core.exceptions import HRWorkflowError

logger = get_logger("graph.nodes")

STATUS_MAP = {"pending": "待审批", "approved": "已批准", "rejected": "已拒绝"}


def _history_entry(step: str, summary: str, extra: dict | None = None) -> dict:
    return {
        "step": step,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        **(extra or {}),
    }


def _agent_metric(name: str, latency_ms: float, tokens_in: int = 0, tokens_out: int = 0) -> dict:
    return {
        "name": name,
        "kind": "agent",
        "latency_ms": round(latency_ms, 2),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Node: parse_uploads ────────────────────────────────────────────────────────

async def parse_uploads_node(state: HRWorkflowState) -> dict:
    """
    Validates that resumes and JD are present in state (already stored in GridFS).
    This node is a checkpoint — actual file storage happens via the API before
    the graph is invoked.
    """
    logger.info("node_parse_uploads", session_id=state["session_id"])
    start = time.perf_counter()

    resume_count = len(state.get("resume_file_ids", []))
    jd_present = bool(state.get("job_description", "").strip())

    if resume_count == 0:
        raise HRWorkflowError("未上传简历", details={"session_id": state["session_id"]})
    if not jd_present:
        raise HRWorkflowError("职位描述为空", details={"session_id": state["session_id"]})

    latency_ms = (time.perf_counter() - start) * 1000
    summary = f"已验证 {resume_count} 份简历和职位描述"
    logger.info("parse_uploads_complete", resume_count=resume_count, latency_ms=round(latency_ms, 2))

    return {
        "current_step": "uploads_validated",
        "workflow_history": [_history_entry("parse_uploads", summary, {"resume_count": resume_count})],
        "agent_metrics": [_agent_metric("parse_uploads_node", latency_ms)],
        "messages": [AIMessage(content=summary)],
    }


# ── Node: shortlist_resumes ────────────────────────────────────────────────────

async def shortlist_resumes_node(state: HRWorkflowState) -> dict:
    """Invoke the ResumeShortlisterAgent and save results to state + MongoDB."""
    logger.info("node_shortlist_resumes", session_id=state["session_id"])
    start = time.perf_counter()

    # Build filename list (stored in state when files were uploaded)
    filenames = state.get("resume_filenames", [])
    file_ids = state.get("resume_file_ids", [])
    if len(filenames) != len(file_ids):
        filenames = [f"resume_{i}.pdf" for i in range(len(file_ids))]

    result = await resume_shortlister_agent.arun(
        job_description=state["job_description"],
        resume_file_ids=file_ids,
        resume_filenames=filenames,
    )

    latency_ms = (time.perf_counter() - start) * 1000
    shortlisted = result["shortlisted_candidates"]
    summary = f"从 {len(file_ids)} 份简历中筛选出 {len(shortlisted)} 位候选人"

    await save_shortlisted_candidates(state["session_id"], shortlisted)

    logger.info(
        "shortlisting_complete",
        count=len(shortlisted),
        tokens_in=result.get("tokens_in"),
        tokens_out=result.get("tokens_out"),
    )

    return {
        "parsed_resumes": result["parsed_resumes"],
        "shortlisted_candidates": shortlisted,
        "shortlisting_rationale": result["shortlisting_rationale"],
        "current_step": "resumes_shortlisted",
        "shortlist_approval_status": "pending",
        "workflow_history": [
            _history_entry("shortlist_resumes", summary, {"count": len(shortlisted)})
        ],
        "agent_metrics": [
            _agent_metric(
                "resume_shortlister",
                latency_ms,
                result.get("tokens_in", 0),
                result.get("tokens_out", 0),
            )
        ],
        "messages": [AIMessage(content=summary)],
    }


# ── Node: hitl_shortlist ───────────────────────────────────────────────────────

async def hitl_shortlist_node(state: HRWorkflowState) -> dict:
    """
    Read the recruiter's shortlist approval decision (set via API before resume).
    Routes are determined by graph edges based on shortlist_approval_status.
    """
    status = state.get("shortlist_approval_status", "pending")
    feedback = state.get("shortlist_approval_feedback", "")
    logger.info("node_hitl_shortlist", status=status, session_id=state["session_id"])

    summary = f"短名单审核：招聘官决策 = {STATUS_MAP.get(status, status)}"
    if feedback:
        summary += f" | 反馈: {feedback}"

    return {
        "current_step": f"shortlist_{status}",
        "workflow_history": [
            _history_entry("hitl_shortlist", summary, {"decision": status, "feedback": feedback})
        ],
        "messages": [AIMessage(content=summary)],
    }


# ── Node: pre_screening ────────────────────────────────────────────────────────

async def pre_screening_node(state: HRWorkflowState) -> dict:
    """预筛选节点 — 如果已有结果（来自网页对话）则跳过，否则运行预筛选智能体。"""
    logger.info("node_pre_screening", session_id=state["session_id"])

    # 检查是否已有预筛选结果（来自网页对话）
    existing_results = state.get("pre_screening_results", [])
    if existing_results:
        logger.info("pre_screening_skipped", reason="results_already_exist", count=len(existing_results))
        summary = f"已有 {len(existing_results)} 位候选人的预筛选结果（来自网页对话），跳过预筛选"
        return {
            "pre_screening_approval_status": "pending",
            "current_step": "pre_screening_complete",
            "workflow_history": [
                _history_entry("pre_screening", summary, {"results_count": len(existing_results), "skipped": True})
            ],
            "messages": [AIMessage(content=summary)],
        }

    start = time.perf_counter()
    result = await pre_screener_agent.arun(
        session_id=state["session_id"],
        shortlisted_candidates=state["shortlisted_candidates"],
    )

    latency_ms = (time.perf_counter() - start) * 1000
    results = result["pre_screening_results"]
    summary = f"已完成 {len(results)} 位候选人的预筛选"

    logger.info("pre_screening_complete", results_count=len(results))

    return {
        "call_sids": result["call_sids"],
        "pre_screening_results": results,
        "pre_screening_approval_status": "pending",
        "current_step": "pre_screening_complete",
        "workflow_history": [
            _history_entry("pre_screening", summary, {"results_count": len(results)})
        ],
        "agent_metrics": [
            _agent_metric("pre_screener", latency_ms)
        ],
        "messages": [AIMessage(content=summary)],
    }


# ── Node: email_interview_scheduler ───────────────────────────────────────────

async def email_interview_scheduler_node(state: HRWorkflowState) -> dict:
    """安排面试并通过邮件发送确认通知。"""
    logger.info("node_email_interview_scheduler", session_id=state["session_id"])
    start = time.perf_counter()

    result = await email_interview_scheduler_agent.arun(
        session_id=state["session_id"],
        pre_screening_results=state.get("pre_screening_results", []),
    )

    latency_ms = (time.perf_counter() - start) * 1000
    scheduling_results = result["email_scheduling_results"]
    scheduled_count = sum(1 for r in scheduling_results if r["status"] == "scheduled")
    summary = (
        f"面试安排完成：{scheduled_count}/{len(scheduling_results)} 位候选人已安排，"
        "确认邮件已发送。"
    )

    logger.info(
        "email_interview_scheduler_complete",
        session_id=state["session_id"],
        scheduled=scheduled_count,
        total=len(scheduling_results),
    )

    return {
        "email_scheduling_results": scheduling_results,
        "current_step": "emails_sent",
        "workflow_history": [
            _history_entry(
                "email_interview_scheduler",
                summary,
                {"scheduled_count": scheduled_count, "total": len(scheduling_results)},
            )
        ],
        "agent_metrics": [_agent_metric("email_interview_scheduler", latency_ms)],
        "messages": [AIMessage(content=summary)],
    }


# ── Node: hitl_pre_screening ───────────────────────────────────────────────────

async def hitl_pre_screening_node(state: HRWorkflowState) -> dict:
    """Read the recruiter's pre-screening approval decision."""
    status = state.get("pre_screening_approval_status", "pending")
    feedback = state.get("pre_screening_approval_feedback", "")
    logger.info("node_hitl_pre_screening", status=status, session_id=state["session_id"])

    summary = f"预筛选审核：招聘官决策 = {STATUS_MAP.get(status, status)}"
    if feedback:
        summary += f" | 反馈: {feedback}"

    return {
        "current_step": f"pre_screening_{status}",
        "workflow_history": [
            _history_entry("hitl_pre_screening", summary, {"decision": status, "feedback": feedback})
        ],
        "messages": [AIMessage(content=summary)],
    }


# ── Node: hitl_onboarding ──────────────────────────────────────────────────────

async def hitl_onboarding_node(state: HRWorkflowState) -> dict:
    """Read the recruiter's onboarding selection (set via API before resume)."""
    status = state.get("onboarding_approval_status", "pending")
    selected = state.get("onboarding_selected_ids", [])
    logger.info("node_hitl_onboarding", status=status, selected_count=len(selected), session_id=state["session_id"])

    summary = f"入职审核：状态 = {STATUS_MAP.get(status, status)}，已选 {len(selected)} 位候选人"
    return {
        "current_step": f"onboarding_{status}",
        "workflow_history": [
            _history_entry("hitl_onboarding", summary, {"status": status, "selected_count": len(selected)})
        ],
        "messages": [AIMessage(content=summary)],
    }


# ── Node: onboarding ───────────────────────────────────────────────────────────

async def onboarding_node(state: HRWorkflowState) -> dict:
    """Send onboarding emails to candidates selected by HR."""
    logger.info("node_onboarding", session_id=state["session_id"])
    start = time.perf_counter()

    selected_ids = set(state.get("onboarding_selected_ids", []))
    scheduling_results = state.get("email_scheduling_results", [])

    selected_candidates = [
        r for r in scheduling_results
        if r.get("candidate_id") in selected_ids
    ]

    result = await onboarding_agent.arun(
        session_id=state["session_id"],
        selected_candidates=selected_candidates,
    )

    latency_ms = (time.perf_counter() - start) * 1000
    onboarding_results = result["onboarding_results"]
    sent_count = sum(1 for r in onboarding_results if r["status"] == "sent")
    summary = f"已向 {sent_count}/{len(onboarding_results)} 位选定的候选人发送入职邮件"

    logger.info("onboarding_node_complete", session_id=state["session_id"], sent=sent_count)

    return {
        "onboarding_results": onboarding_results,
        "current_step": "onboarding_complete",
        "workflow_history": [
            _history_entry("onboarding", summary, {"sent_count": sent_count, "total": len(onboarding_results)})
        ],
        "agent_metrics": [_agent_metric("onboarding", latency_ms)],
        "messages": [AIMessage(content=summary)],
    }
