"""
SQLite 数据访问层（替代原 MongoDB + GridFS）。

使用 aiosqlite 实现异步操作，文件存储使用本地文件系统。
"""
import os
import json
import uuid
import aiosqlite
from datetime import datetime
from typing import Any, Optional
from config.settings import get_settings
from core.logging import get_logger

logger = get_logger("db.sqlite")

_db_path: str = ""
_db: Optional[aiosqlite.Connection] = None


async def connect() -> None:
    global _db, _db_path
    settings = get_settings()
    _db_path = settings.db_path

    # 确保目录存在
    os.makedirs(os.path.dirname(_db_path), exist_ok=True)

    _db = await aiosqlite.connect(_db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _create_tables()
    logger.info("sqlite_connected", db_path=_db_path)


async def disconnect() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None
        logger.info("sqlite_disconnected")


def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("SQLite not connected. Call connect() first.")
    return _db


async def _create_tables() -> None:
    db = get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            thread_id TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            call_sid TEXT UNIQUE NOT NULL,
            phone TEXT DEFAULT '',
            status TEXT DEFAULT 'initiated',
            conversation_json TEXT DEFAULT '[]',
            screening_data_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            metric_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);
        CREATE INDEX IF NOT EXISTS idx_candidates_session_id ON candidates(session_id);
        CREATE INDEX IF NOT EXISTS idx_calls_call_sid ON calls(call_sid);
        CREATE INDEX IF NOT EXISTS idx_calls_session_id ON calls(session_id);
        CREATE INDEX IF NOT EXISTS idx_metrics_session_id ON metrics(session_id);
    """)
    await db.commit()


# ── Sessions ──────────────────────────────────────────────────────────────────

async def create_session(session_id: str, thread_id: str, initial_state: dict) -> dict:
    db = get_db()
    now = datetime.utcnow().isoformat()
    state_json = json.dumps(initial_state, default=str)
    await db.execute(
        "INSERT INTO sessions (session_id, thread_id, state_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, thread_id, state_json, now, now),
    )
    await db.commit()
    logger.info("session_created", session_id=session_id)
    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "state_snapshot": initial_state,
        "created_at": now,
        "updated_at": now,
    }


async def get_session(session_id: str) -> Optional[dict]:
    db = get_db()
    cursor = await db.execute(
        "SELECT session_id, thread_id, state_json, created_at, updated_at FROM sessions WHERE session_id = ?",
        (session_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        "session_id": row["session_id"],
        "thread_id": row["thread_id"],
        "state_snapshot": json.loads(row["state_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def update_session(session_id: str, update: dict) -> None:
    db = get_db()
    now = datetime.utcnow().isoformat()
    # 合并现有状态
    existing = await get_session(session_id)
    if existing:
        merged = {**existing.get("state_snapshot", {}), **update}
    else:
        merged = update
    state_json = json.dumps(merged, default=str)
    await db.execute(
        "UPDATE sessions SET state_json = ?, updated_at = ? WHERE session_id = ?",
        (state_json, now, session_id),
    )
    await db.commit()


# ── Candidates ────────────────────────────────────────────────────────────────

async def save_candidates(session_id: str, candidates: list[dict]) -> None:
    if not candidates:
        return
    db = get_db()
    now = datetime.utcnow().isoformat()
    for c in candidates:
        data_json = json.dumps(c, default=str)
        await db.execute(
            "INSERT INTO candidates (session_id, data_json, created_at) VALUES (?, ?, ?)",
            (session_id, data_json, now),
        )
    await db.commit()
    logger.info("candidates_saved", session_id=session_id, count=len(candidates))


async def get_candidates(session_id: str) -> list[dict]:
    db = get_db()
    cursor = await db.execute(
        "SELECT id, session_id, data_json, created_at FROM candidates WHERE session_id = ?",
        (session_id,),
    )
    rows = await cursor.fetchall()
    results = []
    for row in rows:
        data = json.loads(row["data_json"])
        data["_id"] = row["id"]
        data["session_id"] = row["session_id"]
        results.append(data)
    return results


# ── Calls ─────────────────────────────────────────────────────────────────────

async def create_call_record(session_id: str, candidate_id: str, call_sid: str, phone: str) -> None:
    db = get_db()
    now = datetime.utcnow().isoformat()
    await db.execute(
        """INSERT INTO calls (session_id, candidate_id, call_sid, phone, status,
           conversation_json, screening_data_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'initiated', '[]', '{}', ?, ?)""",
        (session_id, candidate_id, call_sid, phone, now, now),
    )
    await db.commit()
    logger.info("call_record_created", call_sid=call_sid, phone=phone)


async def get_call_record(call_sid: str) -> Optional[dict]:
    db = get_db()
    cursor = await db.execute(
        "SELECT * FROM calls WHERE call_sid = ?", (call_sid,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return _row_to_call_dict(row)


async def update_call_record(call_sid: str, update: dict) -> None:
    db = get_db()
    now = datetime.utcnow().isoformat()
    # 获取现有记录
    existing = await get_call_record(call_sid)
    if not existing:
        return

    # 更新字段
    if "status" in update:
        await db.execute(
            "UPDATE calls SET status = ?, updated_at = ? WHERE call_sid = ?",
            (update["status"], now, call_sid),
        )
    if "screening_data" in update:
        sd_json = json.dumps(update["screening_data"], default=str)
        await db.execute(
            "UPDATE calls SET screening_data_json = ?, updated_at = ? WHERE call_sid = ?",
            (sd_json, now, call_sid),
        )
    if "conversation" in update:
        conv_json = json.dumps(update["conversation"], default=str)
        await db.execute(
            "UPDATE calls SET conversation_json = ?, updated_at = ? WHERE call_sid = ?",
            (conv_json, now, call_sid),
        )
    await db.commit()


async def append_call_turn(call_sid: str, role: str, text: str) -> None:
    db = get_db()
    now = datetime.utcnow().isoformat()
    existing = await get_call_record(call_sid)
    if not existing:
        return

    conversation = existing.get("conversation", [])
    conversation.append({"role": role, "text": text, "ts": now})
    conv_json = json.dumps(conversation, default=str)
    await db.execute(
        "UPDATE calls SET conversation_json = ?, updated_at = ? WHERE call_sid = ?",
        (conv_json, now, call_sid),
    )
    await db.commit()


async def get_session_calls(session_id: str) -> list[dict]:
    db = get_db()
    cursor = await db.execute(
        "SELECT * FROM calls WHERE session_id = ?", (session_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_call_dict(row) for row in rows]


def _row_to_call_dict(row) -> dict:
    return {
        "_id": row["id"],
        "session_id": row["session_id"],
        "candidate_id": row["candidate_id"],
        "call_sid": row["call_sid"],
        "phone": row["phone"],
        "status": row["status"],
        "conversation": json.loads(row["conversation_json"]),
        "screening_data": json.loads(row["screening_data_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ── Metrics ───────────────────────────────────────────────────────────────────

async def save_metric(session_id: str, metric: dict) -> None:
    db = get_db()
    now = datetime.utcnow().isoformat()
    metric_json = json.dumps(metric, default=str)
    await db.execute(
        "INSERT INTO metrics (session_id, metric_json, created_at) VALUES (?, ?, ?)",
        (session_id, metric_json, now),
    )
    await db.commit()


# ── File storage (本地文件系统，替代 GridFS) ──────────────────────────────────

async def store_file(filename: str, content: bytes, metadata: dict | None = None) -> str:
    """存储文件到本地文件系统，返回文件 ID。"""
    settings = get_settings()
    uploads_dir = os.path.join(os.path.dirname(settings.db_path), "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    file_id = str(uuid.uuid4())
    file_path = os.path.join(uploads_dir, f"{file_id}_{filename}")

    with open(file_path, "wb") as f:
        f.write(content)

    # 记录到数据库
    db = get_db()
    now = datetime.utcnow().isoformat()
    await db.execute(
        "INSERT INTO metrics (session_id, metric_json, created_at) VALUES (?, ?, ?)",
        ("file_storage", json.dumps({"file_id": file_id, "filename": filename, "path": file_path, "metadata": metadata or {}}, default=str), now),
    )
    await db.commit()

    logger.info("file_stored", filename=filename, file_id=file_id)
    return file_id


async def read_file(file_id: str) -> bytes:
    """从本地文件系统读取文件。"""
    settings = get_settings()
    uploads_dir = os.path.join(os.path.dirname(settings.db_path), "uploads")

    # 查找匹配的文件
    for fname in os.listdir(uploads_dir):
        if fname.startswith(file_id):
            with open(os.path.join(uploads_dir, fname), "rb") as f:
                return f.read()

    raise FileNotFoundError(f"File not found: {file_id}")
