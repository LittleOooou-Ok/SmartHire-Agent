import json
import time
import re
from typing import Any
from openai import OpenAI
from config.settings import get_settings
from core.logging import get_logger
from core.exceptions import LLMError
from tools.base import tool_call, with_retry

logger = get_logger("tools.llm")
_settings = get_settings()

_client: OpenAI | None = None


def get_llm_client() -> OpenAI:
    """获取 OpenAI 兼容客户端（支持 DeepSeek / Qwen / GPT 等）。"""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=_settings.llm_api_key,
            base_url=_settings.llm_base_url,
        )
        logger.info("llm_client_initialized", model=_settings.llm_model, base_url=_settings.llm_base_url)
    return _client


@tool_call("llm_chat")
@with_retry()
def llm_chat(system_prompt: str, user_prompt: str) -> dict:
    """调用 LLM（OpenAI 兼容接口），返回 {content, tokens_in, tokens_out, latency_ms}。"""
    start = time.perf_counter()
    try:
        response = get_llm_client().chat.completions.create(
            model=_settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        latency_ms = (time.perf_counter() - start) * 1000
        content = response.choices[0].message.content or ""
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0
        logger.info("llm_response", model=_settings.llm_model, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=round(latency_ms, 2))
        return {"content": content, "tokens_in": tokens_in, "tokens_out": tokens_out, "latency_ms": round(latency_ms, 2)}
    except Exception as exc:
        raise LLMError(f"LLM call failed ({_settings.llm_model}): {exc}") from exc


@tool_call("llm_extract_json")
@with_retry()
def llm_extract_json(system_prompt: str, user_prompt: str) -> dict:
    """调用 LLM 并解析 JSON 响应。"""
    result = llm_chat(system_prompt, user_prompt)
    content: str = result["content"]
    clean = content.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
        if match:
            data = json.loads(match.group(0))
        else:
            raise LLMError("LLM did not return valid JSON", details={"raw_response": content[:500]})
    return {"data": data, "tokens_in": result["tokens_in"], "tokens_out": result["tokens_out"], "latency_ms": result["latency_ms"]}


# ═══════════════════════════════════════════════════════════════════════
# 1. 简历解析 Agent（原版提示词）
# ═══════════════════════════════════════════════════════════════════════

@tool_call("llm_parse_resume")
@with_retry()
def llm_parse_resume(resume_text: str) -> dict:
    """从简历原文中提取结构化候选人信息。"""
    system = (
        "You are an expert HR resume parser. Extract structured information from the resume text. "
        "Return ONLY valid JSON with these exact keys: "
        "name, email, phone, skills (array of strings), "
        "current_role, education, summary. "
        "If a field is missing, use empty string. Do not add any explanation."
    )
    user = f"Parse this resume:\n\n{resume_text[:4000]}"
    return llm_extract_json(system, user)


# ═══════════════════════════════════════════════════════════════════════
# 2. 候选人评估 Agent（原版提示词）
# ═══════════════════════════════════════════════════════════════════════

@tool_call("llm_shortlist_candidates")
@with_retry()
def llm_shortlist_candidates(job_description: str, resumes: list[dict], top_n: int = 5) -> dict:
    """根据 JD 对简历评分排序，返回 top_n 候选人及理由。"""
    resumes_text = json.dumps(
        [{"id": i, **{k: v for k, v in r.items() if k != "raw_text"}} for i, r in enumerate(resumes)],
        indent=2,
    )[:6000]

    system = (
        "You are a senior technical recruiter. Analyze the resumes against the job description. "
        f"Select the top {top_n} best-fit candidates. "
        "Return ONLY valid JSON with this structure: "
        '{"candidates": [{"index": <int>, "name": <str>, "selection_reason": <str>, "match_score": <float 0-10>}], '
        '"overall_rationale": <str>}. '
        "Be specific about why each candidate was selected."
    )
    user = (
        f"Job Description:\n{job_description[:2000]}\n\n"
        f"Resumes:\n{resumes_text}"
    )
    return llm_extract_json(system, user)


# ═══════════════════════════════════════════════════════════════════════
# 3. 电话预筛选对话 Agent (Twilio)
# ═══════════════════════════════════════════════════════════════════════

@tool_call("llm_generate_call_response")
@with_retry()
def llm_generate_call_response(
    candidate_name: str,
    conversation_history: list[dict],
    screening_data: dict,
    company_name: str = "our company",
) -> dict:
    """生成电话预筛选对话的下一轮回复。"""
    import datetime
    today = datetime.date.today()
    week_days = []
    for i in range(7):
        d = today + datetime.timedelta(days=i)
        if d.weekday() < 5:
            week_days.append(f"{d.month}月{d.day}日({d.strftime('%A')})")
    week_days_str = "、".join(week_days) if week_days else "本周"

    history_text = "\n".join(
        f"{turn['role'].upper()}: {turn['text']}" for turn in conversation_history[-10:]
    )
    collected = json.dumps(screening_data, indent=2, ensure_ascii=False)

    system = (
        f"你是{company_name}的AI招聘助手，给{candidate_name}做电话预筛选。"
        "收集：意向、原因、薪资、经验、面试时间。"
        f"本周：{week_days_str}。时间格式：M月D日 HH:MM-HH:MM。"
        "回复简洁，不超过3句。返回JSON："
        '{"reply":"","is_complete":false,"screening_data":{"looking_for_change":null,"reason_for_change":null,"current_ctc":null,"expected_ctc":null,"experience_years":null,"interview_slots":null}}'
    )
    user = f"对话：\n{history_text}\n\n已收集：\n{collected}\n\n生成下一条回复。"
    result = llm_extract_json(system, user)
    result["data"]["tokens_in"] = result["tokens_in"]
    result["data"]["tokens_out"] = result["tokens_out"]
    result["data"]["latency_ms"] = result["latency_ms"]
    return result["data"]


# ═══════════════════════════════════════════════════════════════════════
# 4. 预筛选数据模拟 Agent
# ═══════════════════════════════════════════════════════════════════════

@tool_call("llm_simulate_screening")
@with_retry()
def llm_simulate_screening(
    candidate_name: str,
    candidate_role: str,
    candidate_skills: str,
) -> dict:
    """模拟生成候选人的电话预筛选数据。"""
    import datetime
    today = datetime.date.today()

    system = (
        f"模拟候选人预筛选数据。今天{today.strftime('%Y年%m月%d日')}。"
        "生成与候选人背景一致的数据，薪资合理，换工作原因自然。"
        "面试时间：未来7天内工作日，09:00-12:00或14:00-18:00，时长60-120分钟。"
        '返回JSON：{"simulated":true,"looking_for_change":true,"reason_for_change":"","current_ctc":"X万/年","expected_ctc":"X万/年","experience_years":"X年","interview_slots":["M月D日 HH:MM-HH:MM"]}'
        "只返回JSON。"
    )
    user = f"候选人：{candidate_name}\n职位：{candidate_role}\n技能：{candidate_skills}"
    return llm_extract_json(system, user)
