"""
邮件发送模块 — QQ 邮箱 SMTP（替代原 Gmail API + OAuth2）。

支持 QQ 邮箱、163 邮箱、Gmail 等任何 SMTP 服务。
只需配置 SMTP_HOST / SMTP_PORT / SMTP_SENDER_EMAIL / SMTP_AUTH_CODE。
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config.settings import get_settings
from core.logging import get_logger

logger = get_logger("tools.email")


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    """通过 SMTP 发送 HTML 邮件。"""
    settings = get_settings()

    if not settings.smtp_sender_email or not settings.smtp_auth_code:
        logger.warning("smtp_not_configured_skipping_email")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = settings.smtp_sender_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
            server.login(settings.smtp_sender_email, settings.smtp_auth_code)
            server.sendmail(settings.smtp_sender_email, to_email, msg.as_string())
        logger.info("email_sent", to=to_email, subject=subject)
        return True
    except Exception as exc:
        logger.error("email_send_failed", to=to_email, error=str(exc))
        return False


def send_interview_emails(
    candidate_name: str,
    candidate_email: str,
    interview_time: str,
    meeting_link: str = "",
) -> bool:
    """
    发送面试确认邮件给候选人和 HR。
    返回 True 表示两封邮件都发送成功。
    """
    settings = get_settings()

    meet_line = f'<li>会议链接：<a href="{meeting_link}">{meeting_link}</a></li>' if meeting_link else ""

    # 给候选人
    candidate_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #1a73e8;">📅 面试通知</h2>
        <p>尊敬的 {candidate_name}，您好！</p>
        <p>我们很高兴通知您，您的面试已安排：</p>
        <div style="background: #f8f9fa; padding: 16px; border-radius: 8px; margin: 16px 0;">
            <ul style="list-style: none; padding: 0;">
                <li>🕐 <strong>时间：</strong>{interview_time}</li>
                <li>⏱️ <strong>时长：</strong>1 小时</li>
                <li>💻 <strong>形式：</strong>线上面试</li>
                {meet_line}
            </ul>
        </div>
        <p>请准时参加，建议提前 5-10 分钟进入会议室。祝您面试顺利！</p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
        <p style="color: #666; font-size: 12px;">此邮件由 SmartHire 自动发送，请勿直接回复。</p>
    </div>
    """
    ok1 = _send_email(candidate_email, f"面试通知 - {candidate_name}", candidate_html)

    # 给 HR
    hr_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #1a73e8;">📋 面试安排确认</h2>
        <div style="background: #f8f9fa; padding: 16px; border-radius: 8px; margin: 16px 0;">
            <ul style="list-style: none; padding: 0;">
                <li>👤 <strong>候选人：</strong>{candidate_name}</li>
                <li>📧 <strong>邮箱：</strong>{candidate_email}</li>
                <li>🕐 <strong>时间：</strong>{interview_time}</li>
                {meet_line}
            </ul>
        </div>
        <p style="color: #666; font-size: 12px;">此邮件由 SmartHire 自动发送。</p>
    </div>
    """
    ok2 = _send_email(settings.hr_email, f"面试安排 - {candidate_name}", hr_html) if settings.hr_email else True

    return ok1 and ok2


def send_onboarding_email(candidate_name: str, candidate_email: str) -> bool:
    """发送入职祝贺邮件。"""
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #34a853;">🎉 恭喜您通过面试！</h2>
        <p>尊敬的 {candidate_name}，您好！</p>
        <p>我们很高兴通知您，您已通过我们的面试流程，成功获得录用！</p>
        <div style="background: #e8f5e9; padding: 16px; border-radius: 8px; margin: 16px 0;">
            <h3 style="margin-top: 0;">📌 后续流程</h3>
            <ul>
                <li>📄 Offer Letter 将在 2 个工作日内发送</li>
                <li>📁 请提前准备入职材料（身份证、学历证书等）</li>
                <li>📞 HR 将尽快与您联系确认入职日期</li>
            </ul>
        </div>
        <p>期待与您共事！</p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
        <p style="color: #666; font-size: 12px;">此邮件由 SmartHire 自动发送，请勿直接回复。</p>
    </div>
    """
    return _send_email(candidate_email, f"🎉 入职通知 - {candidate_name}", html)
