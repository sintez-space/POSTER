import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .paths import DB_PATH, ensure_dirs

STATUSES = ("draft", "submitted", "approved", "exported", "rejected")
MEDIA_KINDS = ("video", "carousel")


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect():
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                login TEXT UNIQUE NOT NULL,
                email TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
                display_name TEXT NOT NULL,
                is_approved INTEGER NOT NULL DEFAULT 1,
                created_at TEXT,
                profile_json TEXT NOT NULL DEFAULT '{}',
                avatar_name TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                scheduled_at TEXT,
                slot_part INTEGER NOT NULL DEFAULT 1,
                media_kind TEXT NOT NULL DEFAULT 'video',
                status TEXT NOT NULL,
                descriptions_json TEXT NOT NULL DEFAULT '{}',
                video_name TEXT,
                cover_name TEXT,
                carousel_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS user_series_tags (
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                color TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, name)
            );
            CREATE TABLE IF NOT EXISTS catalog_tags (
                name TEXT PRIMARY KEY,
                color TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_submissions_user ON submissions(user_id);
            CREATE INDEX IF NOT EXISTS idx_submissions_sched ON submissions(scheduled_at);
            """
        )
        _ensure_column(conn, "users", "profile_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "users", "avatar_name", "TEXT")
        _ensure_column(conn, "users", "email", "TEXT")
        _ensure_column(conn, "users", "is_approved", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "users", "created_at", "TEXT")
        _ensure_column(conn, "submissions", "slot_part", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "submissions", "media_kind", "TEXT NOT NULL DEFAULT 'video'")
        _ensure_column(conn, "submissions", "carousel_json", "TEXT NOT NULL DEFAULT '[]'")


def default_collab_from_profile(prof: dict) -> str:
    """Формирует строку коллабораторов по умолчанию из ников автора в профиле."""
    handles = []
    for k in ("instagram_handle", "tiktok_handle", "youtube_handle", "vk_handle", "telegram_handle"):
        val = (prof.get(k) or "").strip().lstrip("@")
        if val and val not in handles:
            handles.append(val)
    return ", ".join(handles)


def row_to_user_profile(row) -> dict:
    try:
        prof = json.loads(row["profile_json"] or "{}")
    except Exception:
        prof = {}
    keys = row.keys()
    disp = (row["display_name"] or "").strip()
    parts = disp.split(None, 1) if disp else []
    first_default = parts[0] if parts else ""
    last_default = parts[1] if len(parts) > 1 else ""
    email_val = (row["email"] if "email" in keys and row["email"] else "") or prof.get("email", "")
    is_app = int(row["is_approved"]) if "is_approved" in keys and row["is_approved"] is not None else 1
    created_at = row["created_at"] if "created_at" in keys else None
    return {
        "id": row["id"],
        "login": row["login"],
        "email": email_val,
        "role": row["role"],
        "display_name": row["display_name"],
        "is_approved": bool(is_app),
        "created_at": created_at,
        "avatar_url": f"/api/users/{row['id']}/avatar" if row["avatar_name"] else None,
        "profile": {
            "first_name": prof.get("first_name", first_default),
            "last_name": prof.get("last_name", last_default),
            "email": email_val,
            "instagram_handle": prof.get("instagram_handle", ""),
            "tiktok_handle": prof.get("tiktok_handle", ""),
            "youtube_handle": prof.get("youtube_handle", ""),
            "vk_handle": prof.get("vk_handle", ""),
            "telegram_handle": prof.get("telegram_handle", ""),
            "tags_cloud": prof.get("tags_cloud", ""),
            "ig_mentions_cloud": prof.get("ig_mentions_cloud", ""),
            "tt_mentions_cloud": prof.get("tt_mentions_cloud", ""),
            "default_collaborators": default_collab_from_profile(prof),
        },
    }


def row_to_submission(row):
    desc = json.loads(row["descriptions_json"] or "{}")
    try:
        carousel_files = json.loads(row["carousel_json"] or "[]")
        if not isinstance(carousel_files, list):
            carousel_files = []
    except Exception:
        carousel_files = []
    keys = row.keys()
    slot_part = int(row["slot_part"]) if "slot_part" in keys and row["slot_part"] in (1, 2) else 1
    media_kind = row["media_kind"] if "media_kind" in keys and row["media_kind"] in MEDIA_KINDS else "video"
    author_avatar = row["author_avatar"] if "author_avatar" in keys else None
    author_name = (
        (row["author_name"] if "author_name" in keys and row["author_name"] else None)
        or desc.get("author_name_saved")
        or "Удалённый автор"
    )
    author_login = (
        (row["author_login"] if "author_login" in keys and row["author_login"] else None)
        or desc.get("author_login_saved")
        or "deleted"
    )
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "author_name": author_name,
        "author_login": author_login,
        "scheduled_at": row["scheduled_at"] or "",
        "slot_part": slot_part,
        "media_kind": media_kind,
        "status": row["status"],
        "descriptions": desc,
        "video_name": row["video_name"],
        "cover_name": row["cover_name"],
        "carousel_files": carousel_files,
        "author_avatar_url": f"/api/users/{row['user_id']}/avatar" if author_avatar else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
