"""
生成招聘工作流 HTML 报告。
Usage: python generate_report.py <session_id>
"""
import sys
import json
import asyncio
from datetime import datetime
from db.sqlite_db import connect, get_session, get_session_calls

import os
from pathlib import Path

SESSION_ID = sys.argv[1] if len(sys.argv) > 1 else None

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<title>SmartHire 运行日志 — {session_id}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
          background: #0f1117; color: #e2e8f0; padding: 32px; }}
  h1   {{ font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 4px; }}
  .sub {{ font-size: 13px; color: #64748b; margin-bottom: 24px; }}

  .tabs {{ display: flex; gap: 4px; margin-bottom: 20px; border-bottom: 1px solid #2d3148; padding-bottom: 0; }}
  .tab-btn {{
    background: none; border: none; color: #64748b; font-size: 13px; font-weight: 600;
    padding: 8px 18px; cursor: pointer; border-radius: 6px 6px 0 0;
    border: 1px solid transparent; border-bottom: none; position: relative; bottom: -1px;
    letter-spacing: .04em;
  }}
  .tab-btn:hover {{ color: #e2e8f0; }}
  .tab-btn.active {{ color: #e2e8f0; background: #1e2130; border-color: #2d3148; }}
  .tab-pane {{ display: none; }}
  .tab-pane.active {{ display: block; }}

  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
  .card {{ background: #1e2130; border: 1px solid #2d3148; border-radius: 10px; padding: 20px; }}
  .card h2 {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
              color: #64748b; margin-bottom: 14px; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 99px;
            font-size: 12px; font-weight: 600; }}
  .green  {{ background: #14532d44; color: #4ade80; border: 1px solid #166534; }}
  .blue   {{ background: #1e3a5f44; color: #60a5fa; border: 1px solid #1e40af; }}
  .gray   {{ background: #1f293744; color: #94a3b8; border: 1px solid #334155; }}
  .red    {{ background: #3b0f0f44; color: #f87171; border: 1px solid #7f1d1d; }}
  .kv     {{ display: flex; justify-content: space-between; align-items: flex-start;
             padding: 8px 0; border-bottom: 1px solid #2d3148; font-size: 13px; }}
  .kv:last-child {{ border-bottom: none; }}
  .kv .k  {{ color: #94a3b8; flex-shrink: 0; margin-right: 12px; }}
  .kv .v  {{ color: #e2e8f0; text-align: right; word-break: break-word; }}
  .skill-list {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
  .skill {{ background: #1e3a5f44; border: 1px solid #1e40af44; color: #93c5fd;
            font-size: 11px; padding: 2px 8px; border-radius: 4px; }}
  .timeline {{ list-style: none; position: relative; padding-left: 24px; }}
  .timeline::before {{ content:''; position:absolute; left:7px; top:4px; bottom:4px;
                       width:2px; background:#2d3148; }}
  .timeline li {{ position: relative; margin-bottom: 18px; font-size: 13px; }}
  .timeline li::before {{ content:''; position:absolute; left:-20px; top:4px;
                           width:10px; height:10px; border-radius:50%;
                           background:#4ade80; border:2px solid #14532d; }}
  .timeline .ts  {{ font-size: 11px; color: #64748b; margin-bottom: 2px; }}
  .timeline .msg {{ color: #e2e8f0; }}

  .data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .data-table th {{
    text-align: left; padding: 8px 12px; font-size: 11px; text-transform: uppercase;
    letter-spacing: .06em; color: #64748b; border-bottom: 1px solid #2d3148;
    white-space: nowrap;
  }}
  .data-table td {{ padding: 10px 12px; border-bottom: 1px solid #1e293766; vertical-align: top; }}
  .data-table tr:last-child td {{ border-bottom: none; }}
  .data-table tr:hover td {{ background: #ffffff08; }}
  .score-pill {{ font-size: 16px; font-weight: 800; color: #4ade80; }}
  .reason-cell {{ color: #94a3b8; font-style: italic; max-width: 260px; }}
  .name-cell {{ font-weight: 600; color: #e2e8f0; }}
  .meta-cell {{ color: #94a3b8; font-size: 12px; }}

  .full {{ grid-column: 1 / -1; }}
</style>
</head>
<body>
<h1>SmartHire 运行日志</h1>
<div class="sub">会话: {session_id} &nbsp;·&nbsp; 生成时间: {generated_at}</div>

<div class="tabs">
  <button class="tab-btn active" onclick="showTab('overview', this)">概览</button>
  <button class="tab-btn"       onclick="showTab('transcript', this)">通话记录</button>
</div>

<div id="tab-overview" class="tab-pane active">
  <div class="grid">
    <div class="card">
      <h2>工作流状态</h2>
      {status_rows}
    </div>
    <div class="card">
      <h2>工作流时间线</h2>
      <ul class="timeline">{timeline_html}</ul>
    </div>
  </div>

  <div class="card full" style="margin-bottom:16px;">
    <h2>候选人短名单 ({candidate_count})</h2>
    {candidates_table}
  </div>

  <div class="card full" style="margin-bottom:16px;">
    <h2>预筛选结果 ({screening_count})</h2>
    {screening_table}
  </div>
</div>

<div id="tab-transcript" class="tab-pane">
  {all_transcripts}
</div>

<script>
function showTab(name, btn) {{
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}}
</script>
</body>
</html>"""


# ── 状态翻译映射 ────────────────────────────────────────────────────────────

STEP_MAP = {
    "initialized": "已初始化",
    "parse_uploads": "解析简历中",
    "shortlist_resumes": "生成短名单中",
    "shortlist_approved": "短名单已批准",
    "pre_screening": "电话预筛选中",
    "pre_screening_approved": "预筛选已批准",
    "pre_screening_complete": "预筛选完成",
    "emails_sent": "面试邮件已发送",
    "onboarding_pending": "待入职选择",
    "onboarding_approved": "入职已批准",
    "onboarding_complete": "流程完成",
    "error": "错误",
}

STATUS_MAP = {
    "pending": "待审批",
    "approved": "已批准",
    "rejected": "已拒绝",
}

CALL_STATUS_MAP = {
    "completed": "已完成",
    "failed": "失败",
    "in_progress": "通话中",
    "no_answer": "未接听",
    "no-answer": "未接听",
    "busy": "忙线",
    "initiated": "呼叫中",
    "not_initiated": "未呼叫",
    "simulated": "模拟数据",
}


def badge(text, color="green"):
    return f'<span class="badge {color}">{text}</span>'


def kv(key, value):
    return f'<div class="kv"><span class="k">{key}</span><span class="v">{value}</span></div>'


def _call_status_badge(status: str) -> str:
    color = "green" if status == "completed" else ("red" if status == "failed" else "gray")
    label = CALL_STATUS_MAP.get(status, status or "—")
    return badge(label, color)


def _build_candidates_table(candidates: list[dict]) -> str:
    if not candidates:
        return '<p style="color:#64748b;font-size:13px;padding:8px 0;">暂无候选人数据。</p>'
    rows = ""
    for c in candidates:
        skills_html = "".join(f'<span class="skill">{s[:30]}</span>' for s in c.get("skills", [])[:12])
        score = c.get("match_score", 0)
        score_color = "#4ade80" if score >= 7 else ("#fbbf24" if score >= 5 else "#f87171")
        rows += (
            f'<tr><td class="name-cell">{c.get("name","—")}</td>'
            f'<td class="meta-cell">{c.get("current_role","—")}</td>'
            f'<td><span style="font-size:18px;font-weight:800;color:{score_color}">{score}</span>'
            f'<span style="color:#64748b;font-size:11px">/10</span></td>'
            f'<td><div class="skill-list">{skills_html}</div></td>'
            f'<td class="reason-cell">{c.get("selection_reason","—")}</td></tr>'
        )
    return (
        '<table class="data-table"><thead><tr>'
        '<th>姓名</th><th>当前职位</th><th>匹配分数</th><th>技能</th><th>筛选理由</th>'
        '</tr></thead>' f'<tbody>{rows}</tbody></table>'
    )


def _build_screening_table(results: list[dict]) -> str:
    if not results:
        return '<p style="color:#64748b;font-size:13px;padding:8px 0;">暂无预筛选结果。</p>'
    rows = ""
    for r in results:
        looking = r.get("looking_for_change")
        looking_badge = badge("是", "green") if looking is True else (badge("否", "gray") if looking is False else badge("—", "gray"))
        exp = r.get("experience_years")
        exp_str = f"{exp} 年" if exp else "—"
        rows += (
            f'<tr><td class="name-cell">{r.get("name","—")}</td>'
            f'<td>{looking_badge}</td>'
            f'<td class="reason-cell">{r.get("reason_for_change") or "—"}</td>'
            f'<td>{r.get("current_ctc") or "—"}</td>'
            f'<td>{r.get("expected_ctc") or "—"}</td>'
            f'<td>{"<br>".join(r.get("interview_slots") or []) or "—"}</td>'
            f'<td>{exp_str}</td>'
            f'<td>{_call_status_badge(r.get("call_status",""))}</td></tr>'
        )
    return (
        '<table class="data-table"><thead><tr>'
        '<th>姓名</th><th>求职意向</th><th>换工作原因</th>'
        '<th>当前薪资</th><th>期望薪资</th>'
        '<th>可用面试时间</th><th>工作经验</th><th>通话状态</th>'
        '</tr></thead>' f'<tbody>{rows}</tbody></table>'
    )


def _build_all_transcripts(calls: list[dict], results: list[dict]) -> str:
    if not calls:
        return '<div class="card"><p style="color:#64748b;font-style:italic;">暂无通话记录。</p></div>'
    name_by_candidate = {r.get("candidate_id", ""): r.get("name", "") for r in results}
    html = ""
    for call_doc in calls:
        cid = call_doc.get("candidate_id", "")
        name = name_by_candidate.get(cid) or cid or "未知候选人"
        status = call_doc.get("status", "—")
        turns = call_doc.get("conversation", [])
        convo_html = ""
        if turns:
            for turn in turns:
                role = turn.get("role", "")
                who_label = "AI 助手" if role == "agent" else "候选人"
                convo_html += (
                    f'<div style="display:flex;gap:10px;margin-bottom:12px;">'
                    f'<div style="font-size:11px;font-weight:700;min-width:72px;color:{"#818cf8" if role=="agent" else "#34d399"}">{who_label}</div>'
                    f'<div style="background:#111827;border:1px solid #2d3148;border-radius:8px;padding:8px 12px;color:#e2e8f0;flex:1">{turn.get("text","")}</div>'
                    f'</div>'
                )
        else:
            convo_html = '<p style="color:#64748b;font-style:italic;">无通话记录。</p>'
        html += (
            f'<div class="card" style="margin-bottom:16px;">'
            f'<div style="display:flex;align-items:center;gap:12px;font-size:13px;font-weight:700;color:#e2e8f0;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #2d3148;">'
            f'{name} &nbsp; {_call_status_badge(status)} &nbsp; '
            f'<span style="color:#64748b;font-size:12px;font-weight:400">{len(turns)} 轮对话</span></div>'
            f'{convo_html}</div>'
        )
    return html


async def build_report(session_id: str) -> str:
    await connect()
    session = await get_session(session_id)
    if not session:
        print(f"会话 {session_id} 未找到")
        sys.exit(1)

    snap = session.get("state_snapshot", {})
    calls = await get_session_calls(session_id)

    step = snap.get("current_step", "—")
    sl_status = snap.get("shortlist_approval_status", "—")
    ps_status = snap.get("pre_screening_approval_status", "—")

    step_label = STEP_MAP.get(step, step)
    sl_label = STATUS_MAP.get(sl_status, sl_status)
    ps_label = STATUS_MAP.get(ps_status, ps_status)

    status_rows = (
        kv("当前步骤", badge(step_label, "green" if "approved" in step or "complete" in step else "blue")) +
        kv("短名单审批", badge(sl_label, "green" if sl_status == "approved" else "gray")) +
        kv("预筛选审批", badge(ps_label, "green" if ps_status == "approved" else "gray")) +
        kv("错误", snap.get("error") or badge("无", "green"))
    )

    candidates = snap.get("shortlisted_candidates", [])
    results = snap.get("pre_screening_results", [])

    timeline_html = ""
    for h in snap.get("workflow_history", []):
        ts = h.get("timestamp", "")[:19].replace("T", " ")
        timeline_html += f'<li><div class="ts">{ts}</div><div class="msg">{h.get("summary","")}</div></li>'

    html = HTML_TEMPLATE.format(
        session_id=session_id,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        status_rows=status_rows,
        candidate_count=len(candidates),
        candidates_table=_build_candidates_table(candidates),
        screening_count=len(results),
        screening_table=_build_screening_table(results),
        timeline_html=timeline_html,
        all_transcripts=_build_all_transcripts(calls, results),
    )
    return html


async def save_report(session_id: str) -> str:
    html = await build_report(session_id)
    out = f"run_log_{session_id[:8]}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已保存: {out}")
    return out


if __name__ == "__main__":
    if not SESSION_ID:
        print("用法: python generate_report.py <session_id>")
        sys.exit(1)
    asyncio.run(save_report(SESSION_ID))
