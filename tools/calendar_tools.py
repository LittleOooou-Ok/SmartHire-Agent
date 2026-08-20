"""
飞书日历集成（直接调用飞书开放平台 API）。

功能：
1. 查询面试官日历空闲状态
2. 创建日历事件

如果飞书未配置，降级为跳过日历操作。
"""
import datetime
import time as _time
import httpx
from config.settings import get_settings
from core.logging import get_logger

logger = get_logger("tools.calendar")

# Token 缓存
_tenant_token: str = ""
_token_expires_at: float = 0


def _get_tenant_access_token() -> str:
    """获取飞书 tenant_access_token（带缓存）。"""
    global _tenant_token, _token_expires_at

    if _tenant_token and _time.time() < _token_expires_at:
        return _tenant_token

    settings = get_settings()
    resp = httpx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={
            "app_id": settings.feishu_app_id,
            "app_secret": settings.feishu_app_secret,
        },
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")

    _tenant_token = data["tenant_access_token"]
    _token_expires_at = _time.time() + data.get("expire", 7200) - 300
    return _tenant_token


def _is_feishu_configured() -> bool:
    """检查飞书是否已配置。"""
    settings = get_settings()
    return bool(settings.feishu_app_id and settings.feishu_app_secret and settings.feishu_user_id)


def check_slot_free(start_dt: datetime.datetime, end_dt: datetime.datetime) -> bool:
    """
    查询飞书日历在 [start_dt, end_dt] 是否空闲。
    未配置飞书时默认返回 True（假设有空）。
    """
    if not _is_feishu_configured():
        logger.warning("feishu_not_configured_assuming_free")
        return True

    try:
        token = _get_tenant_access_token()
        settings = get_settings()

        resp = httpx.post(
            "https://open.feishu.cn/open-apis/calendar/v4/freebusy/list",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "time_min": start_dt.isoformat(),
                "time_max": end_dt.isoformat(),
                "user_id_list": [settings.feishu_user_id],
            },
            timeout=10,
        )
        data = resp.json()
        busy_list = data.get("data", {}).get("busy_list", [])
        is_free = len(busy_list) == 0

        logger.info(
            "feishu_calendar_checked",
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            is_free=is_free,
            busy_count=len(busy_list),
        )
        return is_free
    except Exception as exc:
        logger.error("feishu_calendar_check_failed", error=str(exc))
        return False


def create_calendar_event(
    candidate_name: str,
    candidate_email: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
) -> str:
    """
    在飞书日历创建面试事件。
    返回事件链接，失败时返回空字符串。
    未配置飞书时返回空字符串。
    """
    if not _is_feishu_configured():
        logger.warning("feishu_not_configured_skipping_event_creation")
        return ""

    try:
        token = _get_tenant_access_token()
        settings = get_settings()

        resp = httpx.post(
            "https://open.feishu.cn/open-apis/calendar/v4/calendars/primary/events",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "summary": f"面试: {candidate_name}",
                "description": (
                    f"候选人面试安排\n"
                    f"候选人: {candidate_name}\n"
                    f"邮箱: {candidate_email}\n"
                    f"由 SmartHire 自动创建"
                ),
                "start_time": {
                    "timestamp": str(int(start_dt.timestamp())),
                    "timezone": "Asia/Shanghai",
                },
                "end_time": {
                    "timestamp": str(int(end_dt.timestamp())),
                    "timezone": "Asia/Shanghai",
                },
                "attendees": [
                    {
                        "type": "user",
                        "user_id": settings.feishu_user_id,
                    }
                ],
                "need_meeting": True,
            },
            timeout=10,
        )
        data = resp.json()

        if data.get("code") != 0:
            logger.error("feishu_event_creation_failed", response=data)
            return ""

        event_data = data.get("data", {}).get("event", {})
        meeting_link = event_data.get("meeting_link", "")
        event_link = event_data.get("html_link", "")
        link = meeting_link or event_link

        logger.info("feishu_event_created", candidate=candidate_name, link=link)
        return link

    except Exception as exc:
        logger.error("feishu_calendar_event_creation_failed", error=str(exc))
        return ""
