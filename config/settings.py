from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    # ── LLM (必填 — 支持任何 OpenAI 兼容接口) ───────────────────
    llm_api_key: str = Field(..., env="LLM_API_KEY")
    llm_base_url: str = Field("https://api.deepseek.com/v1", env="LLM_BASE_URL")
    llm_model: str = Field("deepseek-chat", env="LLM_MODEL")

    # ── 语音功能已移除（MIMO TTS/ASR） ──────────────────────────

    # ── SQLite ──────────────────────────────────────────────────
    db_path: str = Field("data/smarthire.db", env="DB_PATH")

    # ── Twilio 语音通话 (可选 — 不配则跳过电话预筛选) ────────────
    twilio_account_sid: str = Field("", env="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field("", env="TWILIO_AUTH_TOKEN")
    twilio_from_number: str = Field("", env="TWILIO_FROM_NUMBER")

    # ── 飞书日历 (可选 — 不配则跳过日历集成) ─────────────────────
    feishu_app_id: str = Field("", env="FEISHU_APP_ID")
    feishu_app_secret: str = Field("", env="FEISHU_APP_SECRET")
    feishu_user_id: str = Field("", env="FEISHU_USER_ID")

    # ── QQ 邮箱 SMTP (可选 — 不配则跳过邮件发送) ─────────────────
    smtp_host: str = Field("smtp.qq.com", env="SMTP_HOST")
    smtp_port: int = Field(465, env="SMTP_PORT")
    smtp_sender_email: str = Field("", env="SMTP_SENDER_EMAIL")
    smtp_auth_code: str = Field("", env="SMTP_AUTH_CODE")
    hr_email: str = Field("", env="HR_EMAIL")

    # ── 应用 ────────────────────────────────────────────────────
    app_host: str = Field("0.0.0.0", env="APP_HOST")
    app_port: int = Field(8000, env="APP_PORT")
    public_base_url: str = Field("http://localhost:8000", env="PUBLIC_BASE_URL")
    log_level: str = Field("INFO", env="LOG_LEVEL")
    env: str = Field("development", env="ENV")

    # ── 业务参数 ────────────────────────────────────────────────
    max_shortlisted_candidates: int = 5
    call_polling_interval_seconds: int = 10
    call_max_wait_minutes: int = 30
    tool_max_retries: int = 3
    tool_retry_wait_seconds: float = 2.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # 忽略 .env 中的未知变量


@lru_cache()
def get_settings() -> Settings:
    return Settings()
