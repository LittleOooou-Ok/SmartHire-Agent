"""
面试安排 Agent。

为每位预筛选通过的候选人：
  1. 解析可用时间段
  2. 查询飞书日历空闲状态
  3. 匹配最佳面试时段
  4. 创建飞书日历事件
  5. 通过 QQ 邮箱发送确认邮件
"""
import asyncio
import datetime
import re
from functools import partial

from agents.base import BaseAgent
from tools.calendar_tools import check_slot_free, create_calendar_event
from tools.email_tools import send_interview_emails
from core.logging import get_logger

logger = get_logger("agents.email_interview_scheduler")

# 中国标准时间 (UTC+8)
_CST = datetime.timezone(datetime.timedelta(hours=8))


def _parse_time(raw: str) -> datetime.time:
    """
    解析时间字符串，支持多种格式：
    - "8 AM", "12 PM", "8:30 AM" (12 小时制)
    - "14:00", "10:30" (24 小时制)
    """
    t = raw.strip()
    # 12 小时制: "8 AM" → "08 AM"
    t_padded = re.sub(r"^(\d) ", r"0\1 ", t)
    for fmt in ("%I:%M %p", "%I %p"):
        try:
            return datetime.datetime.strptime(t_padded, fmt).time()
        except ValueError:
            continue
    # 24 小时制: "14:00", "10:30"
    for fmt in ("%H:%M", "%H"):
        try:
            return datetime.datetime.strptime(t, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"无法解析时间: '{raw}'")


# 中文月份映射
_MONTH_MAP = {
    "一月": 1, "二月": 2, "三月": 3, "四月": 4, "五月": 5, "六月": 6,
    "七月": 7, "八月": 8, "九月": 9, "十月": 10, "十一月": 11, "十二月": 12,
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_month(s: str) -> int | None:
    """解析月份名称或数字。"""
    s = s.strip()
    if s in _MONTH_MAP:
        return _MONTH_MAP[s]
    try:
        return int(s)
    except ValueError:
        return None


def _parse_slot(slot_str: str) -> tuple[datetime.datetime, datetime.datetime] | None:
    """
    解析面试时间段字符串，支持多种格式：
    - 英文: "Available on Monday, August 18 from 10 AM to 12 PM"
    - 英文 24h: "Available on Tuesday, August 18 from 14:00 to 16:00"
    - 中文: "8月18日 14:00-16:00" 或 "8月18日下午2点到4点"
    """
    s = slot_str.strip()
    year = datetime.date.today().year

    # 模式 1: 英文格式 "Available on Monday, August 18 from 14:00 to 16:00"
    #         或 "August 18 from 10 AM to 12 PM"
    pattern_en = (
        r"(?:Available on\s+)?"
        r"(?:\w+,\s+)?"
        r"(\w+)\s+(\d+)"              # month day
        r"\s+from\s+"
        r"([\d:]+\s*(?:[APap][Mm])?)"  # start time (AM/PM optional)
        r"\s+to\s+"
        r"([\d:]+\s*(?:[APap][Mm])?)"  # end time
    )
    m = re.search(pattern_en, s, re.IGNORECASE)
    if m:
        month_str, day_str, start_raw, end_raw = m.group(1), m.group(2), m.group(3), m.group(4)
        month = _parse_month(month_str)
        if month:
            try:
                base_date = datetime.date(year, month, int(day_str))
                start_t = _parse_time(start_raw)
                end_t = _parse_time(end_raw)
                start_dt = datetime.datetime.combine(base_date, start_t, tzinfo=_CST)
                end_dt = datetime.datetime.combine(base_date, end_t, tzinfo=_CST)
                if end_dt > start_dt:
                    return start_dt, end_dt
            except (ValueError, TypeError) as exc:
                logger.warning("slot_parse_failed", slot=slot_str, error=str(exc))

    # 模式 2: 中文格式 "8月18日 14:00-16:00" 或 "8月18日 14:00 到 16:00"
    pattern_cn = r"(\d{1,2})月(\d{1,2})日?\s+(\d{1,2}:\d{2})\s*[-到~]\s*(\d{1,2}:\d{2})"
    m = re.search(pattern_cn, s)
    if m:
        month, day, start_raw, end_raw = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        try:
            base_date = datetime.date(year, month, day)
            start_t = _parse_time(start_raw)
            end_t = _parse_time(end_raw)
            start_dt = datetime.datetime.combine(base_date, start_t, tzinfo=_CST)
            end_dt = datetime.datetime.combine(base_date, end_t, tzinfo=_CST)
            if end_dt > start_dt:
                return start_dt, end_dt
        except (ValueError, TypeError) as exc:
            logger.warning("slot_parse_failed", slot=slot_str, error=str(exc))

    # 模式 3: 纯数字日期 "2026-08-18 14:00-16:00"
    pattern_iso = r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*[-到~]\s*(\d{1,2}:\d{2})"
    m = re.search(pattern_iso, s)
    if m:
        date_str, start_raw, end_raw = m.group(1), m.group(2), m.group(3)
        try:
            base_date = datetime.date.fromisoformat(date_str)
            start_t = _parse_time(start_raw)
            end_t = _parse_time(end_raw)
            start_dt = datetime.datetime.combine(base_date, start_t, tzinfo=_CST)
            end_dt = datetime.datetime.combine(base_date, end_t, tzinfo=_CST)
            if end_dt > start_dt:
                return start_dt, end_dt
        except (ValueError, TypeError) as exc:
            logger.warning("slot_parse_failed", slot=slot_str, error=str(exc))

    logger.warning("slot_parse_no_match", slot=slot_str)
    return None


class EmailInterviewSchedulerAgent(BaseAgent):
    """
    Schedules interviews for all pre-screened candidates.
    Checks Google Calendar free/busy, creates events, and sends emails.
    """

    name = "email_interview_scheduler"

    async def _arun(
        self,
        session_id: str,
        pre_screening_results: list[dict],
    ) -> dict:
        logger.info(
            "scheduler_start",
            session_id=session_id,
            candidate_count=len(pre_screening_results),
        )

        loop = asyncio.get_event_loop()
        scheduling_results: list[dict] = []

        for candidate in pre_screening_results:
            cid    = candidate.get("candidate_id", "")
            name   = candidate.get("name", "Candidate")
            email  = candidate.get("email", "")
            slots  = candidate.get("interview_slots") or []

            if not slots:
                logger.warning("no_slots_for_candidate", candidate_id=cid, name=name)
                scheduling_results.append(self._result(cid, name, email, "no_slots"))
                continue

            INTERVIEW_DURATION = datetime.timedelta(hours=1)

            scheduled = False
            for slot_str in slots:
                parsed = _parse_slot(slot_str)
                if not parsed:
                    continue

                window_start, window_end = parsed

                # Scan 1-hour blocks within the candidate's availability window
                block_start = window_start
                while block_start + INTERVIEW_DURATION <= window_end:
                    block_end = block_start + INTERVIEW_DURATION

                    is_free = await loop.run_in_executor(
                        None, partial(check_slot_free, block_start, block_end)
                    )
                    if not is_free:
                        logger.info("slot_busy", candidate_id=cid, block_start=block_start.isoformat())
                        block_start += INTERVIEW_DURATION
                        continue

                    # Found a free 1-hour block — create calendar event
                    booked_slot = (
                        f"{block_start.strftime('%A, %B %d')} "
                        f"{block_start.strftime('%I:%M %p').lstrip('0')}–"
                        f"{block_end.strftime('%I:%M %p').lstrip('0')} CST"
                    )
                    try:
                        cal_link = await loop.run_in_executor(
                            None,
                            partial(create_calendar_event, name, email, block_start, block_end),
                        )

                        await loop.run_in_executor(
                            None,
                            partial(send_interview_emails, name, email, booked_slot, cal_link),
                        )

                        scheduling_results.append(
                            self._result(
                                cid, name, email, "scheduled",
                                scheduled_slot=booked_slot,
                                scheduled_at=block_start.isoformat(),
                                calendar_link=cal_link,
                            )
                        )
                        logger.info(
                            "interview_scheduled",
                            candidate_id=cid,
                            name=name,
                            slot=booked_slot,
                            calendar_link=cal_link,
                            session_id=session_id,
                        )
                        scheduled = True
                        break

                    except Exception as exc:
                        logger.error(
                            "scheduling_error",
                            candidate_id=cid,
                            slot=booked_slot,
                            error=str(exc),
                        )
                        block_start += INTERVIEW_DURATION
                        continue

                if scheduled:
                    break

            if not scheduled:
                scheduling_results.append(self._result(cid, name, email, "no_free_slot"))
                logger.warning("no_free_slot_found", candidate_id=cid, name=name)

        scheduled_count = sum(
            1 for r in scheduling_results if r["status"] == "scheduled"
        )
        logger.info(
            "scheduler_complete",
            session_id=session_id,
            total=len(scheduling_results),
            scheduled=scheduled_count,
        )

        return {
            "email_scheduling_results": scheduling_results,
            "tokens_in": 0,
            "tokens_out": 0,
        }

    @staticmethod
    def _result(
        candidate_id: str,
        name: str,
        email: str,
        status: str,
        scheduled_slot: str = "",
        scheduled_at: str = "",
        calendar_link: str = "",
    ) -> dict:
        return {
            "candidate_id":  candidate_id,
            "name":          name,
            "email":         email,
            "status":        status,        # scheduled | no_slots | no_free_slot
            "scheduled_slot": scheduled_slot,
            "scheduled_at":  scheduled_at,
            "calendar_link": calendar_link,
        }


email_interview_scheduler_agent = EmailInterviewSchedulerAgent()
