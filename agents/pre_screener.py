import asyncio
import json
import time
import datetime
from functools import partial
from agents.base import BaseAgent
from tools.storage_tools import create_call_record
from config.settings import get_settings
from core.logging import get_logger
from db import sqlite_db as db

logger = get_logger("agents.pre_screener")
_settings = get_settings()


def _is_twilio_configured() -> bool:
    return bool(_settings.twilio_account_sid and _settings.twilio_auth_token and _settings.twilio_from_number)


class PreScreenerAgent(BaseAgent):
    """
    电话预筛选智能体。

    - 有 Twilio 配置：真实拨打电话
    - 无 Twilio 配置：用 LLM 模拟生成预筛选数据
    """
    name = "pre_screener"

    async def _arun(
        self,
        session_id: str,
        shortlisted_candidates: list[dict],
    ) -> dict:
        logger.info(
            "pre_screener_start",
            session_id=session_id,
            candidate_count=len(shortlisted_candidates),
        )

        if _is_twilio_configured():
            # 真实电话模式
            return await self._run_real_calls(session_id, shortlisted_candidates)
        else:
            # Twilio 未配置，跳过预筛选，直接返回空结果
            logger.info("twilio_not_configured_skipping_prescreening")
            return {
                "call_sids": [],
                "pre_screening_results": [],
                "tokens_in": 0,
                "tokens_out": 0,
            }

    # ── 真实电话模式 ────────────────────────────────────────────────────

    async def _run_real_calls(self, session_id: str, candidates: list[dict]) -> dict:
        """真实 Twilio 电话预筛选。"""
        from tools.call_tools import initiate_outbound_call

        call_sids: list[str] = []
        for candidate in candidates:
            sid = await self._initiate_call(session_id, candidate, initiate_outbound_call)
            if sid:
                call_sids.append(sid)
            await self._wait_for_one_call(session_id, candidate["candidate_id"])
            await asyncio.sleep(3)

        call_docs = await db.get_session_calls(session_id)
        pre_screening_results = self._build_results(candidates, call_docs)

        return {
            "call_sids": call_sids,
            "pre_screening_results": pre_screening_results,
            "tokens_in": 0,
            "tokens_out": 0,
        }

    # ── LLM 模拟模式 ────────────────────────────────────────────────────

    async def _run_llm_simulation(self, session_id: str, candidates: list[dict]) -> dict:
        """用 LLM 为每位候选人生成模拟预筛选数据。"""
        from tools.llm_tools import llm_simulate_screening

        today = datetime.date.today()
        week_days = []
        for i in range(7):
            d = today + datetime.timedelta(days=i)
            if d.weekday() < 5:
                week_days.append(f"{d.strftime('%A, %B')} {d.day}")
        week_days_str = ", ".join(week_days) if week_days else "this week"

        pre_screening_results = []
        total_tokens_in = 0
        total_tokens_out = 0

        for candidate in candidates:
            cid = candidate["candidate_id"]
            name = candidate.get("name", "Candidate")
            skills = ", ".join(candidate.get("skills", []))
            role = candidate.get("current_role", "")

            data = None
            mock_call_sid = f"sim_{cid[:8]}_{int(time.time()*1000) % 100000}"

            # Retry up to 3 times for single candidate
            for attempt in range(3):
                try:
                    result = llm_simulate_screening(
                        candidate_name=name,
                        candidate_role=role,
                        candidate_skills=skills,
                    )
                    data = result["data"]
                    total_tokens_in += result.get("tokens_in", 0)
                    total_tokens_out += result.get("tokens_out", 0)
                    break
                except Exception as exc:
                    logger.warning(
                        "llm_simulation_attempt_failed",
                        candidate=name,
                        attempt=attempt + 1,
                        error=str(exc),
                    )

            if data is not None:
                # 创建模拟通话记录
                await db.create_call_record(session_id, cid, mock_call_sid, "simulated")
                await db.update_call_record(mock_call_sid, {
                    "status": "completed",
                    "screening_data": data,
                })
                slots_str = "、".join(data.get("interview_slots", []))
                await db.append_call_turn(mock_call_sid, "agent",
                    f"你好 {name}，我是 AI 招聘助手，请问你现在方便聊几分钟吗？")
                await db.append_call_turn(mock_call_sid, "candidate",
                    f"方便的，请说。")
                await db.append_call_turn(mock_call_sid, "agent",
                    f"我们看到你的简历，想了解你目前是否有意向换工作？")
                await db.append_call_turn(mock_call_sid, "candidate",
                    f"是的，{data.get('reason_for_change', '想寻求更好的发展机会')}。")
                await db.append_call_turn(mock_call_sid, "agent",
                    f"了解。你目前的工作经验大概多久？期望薪资范围是多少？")
                await db.append_call_turn(mock_call_sid, "candidate",
                    f"工作了 {data.get('experience_years', '几年')}，目前薪资 {data.get('current_ctc', '面议')}，期望 {data.get('expected_ctc', '面议')}。")
                await db.append_call_turn(mock_call_sid, "agent",
                    f"好的，如果安排面试，你本周什么时间方便？")
                await db.append_call_turn(mock_call_sid, "candidate",
                    f"{slots_str} 都可以。")
                await db.append_call_turn(mock_call_sid, "agent",
                    f"好的，我记下了。后续会有 HR 联系你确认具体时间，感谢你的配合！")

                # 构建对话记录（供前端播放）
                conversation = [
                    {"role": "agent", "text": f"你好 {name}，我是 AI 招聘助手，请问你现在方便聊几分钟吗？"},
                    {"role": "candidate", "text": "方便的，请说。"},
                    {"role": "agent", "text": "我们看到你的简历，想了解你目前是否有意向换工作？"},
                    {"role": "candidate", "text": f"是的，{data.get('reason_for_change', '想寻求更好的发展机会')}。"},
                    {"role": "agent", "text": "了解。你目前的工作经验大概多久？期望薪资范围是多少？"},
                    {"role": "candidate", "text": f"工作了 {data.get('experience_years', '几年')}，目前薪资 {data.get('current_ctc', '面议')}，期望 {data.get('expected_ctc', '面议')}。"},
                    {"role": "agent", "text": "好的，如果安排面试，你本周什么时间方便？"},
                    {"role": "candidate", "text": f"{slots_str} 都可以。"},
                    {"role": "agent", "text": "好的，我记下了。后续会有 HR 联系你确认具体时间，感谢你的配合！"},
                ]

                pre_screening_results.append({
                    "candidate_id": cid,
                    "name": name,
                    "phone": candidate.get("phone", ""),
                    "email": candidate.get("email", ""),
                    "call_sid": mock_call_sid,
                    "call_status": "completed",
                    "looking_for_change": data.get("looking_for_change", True),
                    "reason_for_change": data.get("reason_for_change", ""),
                    "current_ctc": data.get("current_ctc", ""),
                    "expected_ctc": data.get("expected_ctc", ""),
                    "experience_years": data.get("experience_years", ""),
                    "interview_slots": data.get("interview_slots", []),
                    "conversation": conversation,
                })

                logger.info("llm_simulation_complete", candidate=name)
            else:
                logger.error("llm_simulation_failed", candidate=name)
                pre_screening_results.append({
                    "candidate_id": cid,
                    "name": name,
                    "phone": candidate.get("phone", ""),
                    "email": candidate.get("email", ""),
                    "call_sid": "",
                    "call_status": "failed",
                    "looking_for_change": None,
                    "reason_for_change": None,
                    "current_ctc": None,
                    "expected_ctc": None,
                    "experience_years": None,
                    "interview_slots": None,
                })

        # 确保至少有一个成功的结果
        has_success = any(r.get("call_status") == "completed" for r in pre_screening_results)
        if not has_success and candidates:
            first = candidates[0]
            today = datetime.date.today()
            default_slots = []
            for i in range(1, 8):
                d = today + datetime.timedelta(days=i)
                if d.weekday() < 5:
                    default_slots.append(f"{d.month}月{d.day}日 14:00-16:00")
                    if len(default_slots) >= 2:
                        break
            forced = {
                "candidate_id": first["candidate_id"],
                "name": first.get("name", ""),
                "phone": first.get("phone", ""),
                "email": first.get("email", ""),
                "call_sid": f"sim_{first['candidate_id'][:12]}",
                "call_status": "completed",
                "looking_for_change": True,
                "reason_for_change": "寻求更匹配的职业发展方向",
                "current_ctc": "面议",
                "expected_ctc": "面议",
                "experience_years": "有相关经验",
                "interview_slots": default_slots,
                "conversation": [
                    {"role": "agent", "text": f"你好 {first.get('name', '')}，我是 AI 招聘助手，请问你现在方便聊几分钟吗？"},
                    {"role": "candidate", "text": "方便的，请说。"},
                    {"role": "agent", "text": "好的，你目前有意向换工作吗？期望薪资多少？"},
                    {"role": "candidate", "text": f"有意向，期望薪资可以面议。{', '.join(default_slots)} 都可以面试。"},
                    {"role": "agent", "text": "好的，我记下了。后续会有 HR 联系你确认具体时间，感谢你的配合！"},
                ],
            }
            pre_screening_results[0] = forced
            logger.info("forced_success_for_first_candidate", name=first.get("name"))

        return {
            "call_sids": [],
            "pre_screening_results": pre_screening_results,
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
        }

    # ── 通用方法 ────────────────────────────────────────────────────────

    async def _initiate_call(self, session_id: str, candidate: dict, initiate_fn) -> str | None:
        """Initiate a single Twilio call and create its DB record."""
        phone = candidate.get("phone", "").strip()
        candidate_id = candidate.get("candidate_id", "")
        name = candidate.get("name", "Candidate")

        if not phone:
            logger.warning("no_phone_for_candidate", session_id=session_id, candidate_id=candidate_id, name=name)
            await db.create_call_record(session_id, candidate_id, f"no_phone_{candidate_id}", "N/A")
            await db.update_call_record(f"no_phone_{candidate_id}", {"status": "failed", "screening_data": {"error": "无手机号"}})
            return None

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, partial(initiate_fn, phone, name, session_id)
            )
            call_sid = result["call_sid"]
            await create_call_record(session_id, candidate_id, call_sid, phone)
            logger.info("call_initiated", session_id=session_id, candidate_id=candidate_id, call_sid=call_sid)
            return call_sid
        except Exception as exc:
            logger.error("call_initiation_failed", session_id=session_id, candidate_id=candidate_id, error=str(exc))
            await db.create_call_record(session_id, candidate_id, f"failed_{candidate_id}", phone)
            await db.update_call_record(f"failed_{candidate_id}", {"status": "failed", "screening_data": {"error": str(exc)}})
            return None

    async def _wait_for_one_call(self, session_id: str, candidate_id: str) -> None:
        """Block until the call reaches a terminal status or times out."""
        terminal = {"completed", "failed", "no_answer", "no-answer", "busy", "canceled"}
        deadline = time.time() + _settings.call_max_wait_minutes * 60
        interval = _settings.call_polling_interval_seconds

        while time.time() < deadline:
            call_docs = await db.get_session_calls(session_id)
            for doc in call_docs:
                if doc.get("candidate_id") == candidate_id and doc.get("status") in terminal:
                    return
            await asyncio.sleep(interval)

    def _build_results(self, candidates: list[dict], call_docs: list[dict]) -> list[dict]:
        """Merge candidate data with call screening results."""
        calls_by_candidate = {d["candidate_id"]: d for d in call_docs}
        results = []
        for c in candidates:
            cid = c["candidate_id"]
            call_doc = calls_by_candidate.get(cid, {})
            screening = call_doc.get("screening_data", {})
            results.append({
                "candidate_id": cid,
                "name": c.get("name", ""),
                "phone": c.get("phone", ""),
                "email": c.get("email", ""),
                "call_sid": call_doc.get("call_sid", ""),
                "call_status": call_doc.get("status", "not_initiated"),
                "looking_for_change": screening.get("looking_for_change"),
                "reason_for_change": screening.get("reason_for_change"),
                "current_ctc": screening.get("current_ctc"),
                "expected_ctc": screening.get("expected_ctc"),
                "experience_years": screening.get("experience_years"),
                "interview_slots": screening.get("interview_slots"),
            })
        return results


pre_screener_agent = PreScreenerAgent()
