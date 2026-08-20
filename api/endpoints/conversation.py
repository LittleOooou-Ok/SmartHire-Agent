"""
预筛选模拟 API — 生成模拟数据。
"""
from fastapi import APIRouter
from pydantic import BaseModel
from config.settings import get_settings
from core.logging import get_logger
from db import sqlite_db as db

logger = get_logger("api.conversation")
router = APIRouter(prefix="/conversation", tags=["预筛选模拟"])


class SkipRequest(BaseModel):
    session_id: str
    candidates: list[dict]


@router.post("/skip")
async def skip_conversation(req: SkipRequest):
    """用 LLM 直接为所有候选人生成模拟预筛选数据。"""
    from agents.pre_screener import PreScreenerAgent

    logger.info("conversation_skip", session=req.session_id, count=len(req.candidates))

    agent = PreScreenerAgent()
    result = await agent._run_llm_simulation(req.session_id, req.candidates)

    await db.update_session(req.session_id, {
        "pre_screening_results": result["pre_screening_results"],
        "current_step": "pre_screening_complete",
    })

    return {
        "pre_screening_results": result["pre_screening_results"],
        "mode": "simulated",
    }
