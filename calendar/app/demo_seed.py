"""Демо-материалы для ЛК автора и общего календаря (идемпотентно)."""

import json
import math
import struct
import uuid
import zlib
from datetime import datetime, timedelta

from . import db
from .paths import AVATARS_DIR, UPLOADS_DIR, ensure_dirs
from .slots import monday_of

DEMO_SLOT_MAP = (
    ("demo-author-001", 0, 9),
    ("demo-author-002", 1, 11),
    ("demo-author-003", 0, 15),
    ("demo-maria-001", 2, 13),
    ("demo-maria-002", None, None),
)


def _make_cover_png(width: int = 72, height: int = 128, top_rgb=(28, 78, 72), bot_rgb=(18, 34, 36)) -> bytes:
    """Генерирует аккуратную вертикальную PNG-заставку с мягким градиентом."""
    raw = bytearray()
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(top_rgb[0] * (1 - t) + bot_rgb[0] * t)
        g = int(top_rgb[1] * (1 - t) + bot_rgb[1] * t)
        b = int(top_rgb[2] * (1 - t) + bot_rgb[2] * t)
        raw.append(0)
        raw.extend(bytes((r, g, b)) * width)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _make_bw_avatar_png(size: int = 96, variant: int = 0) -> bytes:
    """Генерирует круглый ЧБ-аватар (RGBA PNG < 15KB) с силуэтом."""
    raw = bytearray()
    cx = cy = (size - 1) / 2.0
    radius = size / 2.0 - 1.0
    head_y = size * 0.38
    head_r = size * (0.17 if variant == 0 else 0.19)
    body_y = size * 0.88
    body_rx = size * 0.32
    body_ry = size * 0.28

    for y in range(size):
        raw.append(0)
        for x in range(size):
            dist = math.hypot(x - cx, y - cy)
            if dist > radius:
                raw.extend((0, 0, 0, 0))
                continue
            bg = int(52 + 28 * (1 - y / size))
            in_head = math.hypot(x - cx, y - head_y) <= head_r
            in_body = ((x - cx) / body_rx) ** 2 + ((y - body_y) / body_ry) ** 2 <= 1.0
            if in_head or in_body:
                val = 210 if variant == 0 else 192
            else:
                val = bg
            if dist > radius - 1.5:
                val = 140
            raw.extend((val, val, val, 255))

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


_DEMO_COVERS = {
    "demo-author-001": _make_cover_png(top_rgb=(36, 112, 98), bot_rgb=(16, 36, 38)),
    "demo-author-002": _make_cover_png(top_rgb=(48, 86, 118), bot_rgb=(18, 28, 42)),
    "demo-author-003": _make_cover_png(top_rgb=(92, 68, 124), bot_rgb=(24, 20, 38)),
    "demo-maria-001": _make_cover_png(top_rgb=(118, 78, 52), bot_rgb=(34, 24, 20)),
}

DEMO_SUBMISSIONS = (
    {
        "id": "demo-author-001",
        "login": "author",
        "offset_days": 0,
        "hour": 9,
        "minute": 0,
        "status": "submitted",
        "video_name": "video.mp4",
        "cover_name": "cover.png",
        "descriptions": {
            "base": "Демо: обзор нового ролика для всех сетей.",
            "tags": "#demo, poster",
            "instagram_collaborators": "brand.partner",
            "instagram_reel_tags": "face.model",
            "series_tag": "Видео-1",
            "overrides": {},
        },
    },
    {
        "id": "demo-author-002",
        "login": "author",
        "offset_days": 1,
        "hour": 11,
        "minute": 0,
        "status": "draft",
        "video_name": None,
        "cover_name": "cover.png",
        "descriptions": {
            "base": "Черновик: ещё без видео, только обложка и текст.",
            "series_tag": "Видео-1",
            "overrides": {},
        },
    },
    {
        "id": "demo-author-003",
        "login": "author",
        "offset_days": 0,
        "hour": 15,
        "minute": 0,
        "status": "approved",
        "video_name": "video.mp4",
        "cover_name": "cover.png",
        "descriptions": {
            "base": "Подтверждено админом — слот зафиксирован.",
            "series_tag": "Видео-2",
            "overrides": {},
        },
    },
    {
        "id": "demo-maria-001",
        "login": "maria",
        "offset_days": 2,
        "hour": 13,
        "minute": 0,
        "status": "submitted",
        "video_name": "video.mp4",
        "cover_name": "cover.png",
        "descriptions": {
            "base": "Мария: демо-слот на общем календаре.",
            "series_tag": "Видео-1",
            "overrides": {},
        },
    },
    {
        "id": "demo-maria-002",
        "login": "maria",
        "offset_days": None,
        "hour": None,
        "minute": 0,
        "status": "draft",
        "video_name": None,
        "cover_name": None,
        "descriptions": {
            "base": "Мария: карточка в пуле (ещё не поставлена в слот).",
            "series_tag": "Видео-2",
        },
    },
)


def _scheduled(offset_days: int | None, hour: int | None, minute: int) -> str:
    if offset_days is None or hour is None:
        return ""
    when = monday_of() + timedelta(days=offset_days)
    when = when.replace(hour=hour, minute=minute)
    return when.strftime("%Y-%m-%dT%H:%M")


def _write_demo_files(sub_id: str, cover_name: str | None, video_name: str | None):
    folder = UPLOADS_DIR / sub_id
    folder.mkdir(parents=True, exist_ok=True)
    if cover_name:
        path = folder / cover_name
        png_bytes = _DEMO_COVERS.get(sub_id) or _make_cover_png()
        if not path.is_file() or path.stat().st_size < 120:
            path.write_bytes(png_bytes)
    if video_name:
        path = folder / video_name
        if not path.is_file():
            path.write_bytes(b"\x00")


def ensure_demo_submissions():
    ensure_dirs()
    now = db._utc_now()
    with db.connect() as conn:
        for item in DEMO_SUBMISSIONS:
            _write_demo_files(item["id"], item.get("cover_name"), item.get("video_name"))
            exists = conn.execute("SELECT 1 FROM submissions WHERE id = ?", (item["id"],)).fetchone()
            if exists:
                continue
            user = conn.execute("SELECT id FROM users WHERE login = ?", (item["login"],)).fetchone()
            if not user:
                continue
            scheduled = _scheduled(item["offset_days"], item["hour"], item["minute"])
            conn.execute(
                """
                INSERT INTO submissions (
                    id, user_id, scheduled_at, slot_part, media_kind, status, descriptions_json,
                    video_name, cover_name, created_at, updated_at
                ) VALUES (?, ?, ?, 1, 'video', ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    user["id"],
                    scheduled,
                    item["status"],
                    json.dumps(item["descriptions"], ensure_ascii=False),
                    item.get("video_name"),
                    item.get("cover_name"),
                    now,
                    now,
                ),
            )


def ensure_demo_authors():
    """Второй демо-автор для общего календаря + демо-профили и ЧБ-аватары."""
    from . import auth

    ensure_dirs()
    demos = [
        ("maria", "maria", "user", "Мария"),
    ]
    demo_profiles = {
        "author": (
            0,
            {
                "instagram_handle": "@author.reels",
                "tiktok_handle": "@author.tt",
                "youtube_handle": "@author_shorts",
                "vk_handle": "@author_vk",
                "telegram_handle": "@author_tg",
                "tags_cloud": "#контент, #рилс, #обзор",
                "ig_mentions_cloud": "brand.partner, studio.main",
                "tt_mentions_cloud": "@duet_friend, @sound_creator",
            },
        ),
        "maria": (
            1,
            {
                "instagram_handle": "@maria.visual",
                "tiktok_handle": "@maria.clips",
                "youtube_handle": "@maria_yt",
                "vk_handle": "@maria_vk",
                "telegram_handle": "@maria_channel",
                "tags_cloud": "#лайфстайл, #влог",
                "ig_mentions_cloud": "fashion.room",
                "tt_mentions_cloud": "@maria.team",
            },
        ),
    }
    with db.connect() as conn:
        for login, password, role, name in demos:
            row = conn.execute("SELECT id FROM users WHERE login = ?", (login,)).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO users (id, login, password_hash, role, display_name) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), login, auth.hash_password(password), role, name),
                )
        for login, (variant, prof_default) in demo_profiles.items():
            row = conn.execute("SELECT id, profile_json, avatar_name FROM users WHERE login = ?", (login,)).fetchone()
            if not row:
                continue
            uid = row["id"]
            av_file = f"{uid}.png"
            av_path = AVATARS_DIR / av_file
            if not av_path.is_file():
                av_path.write_bytes(_make_bw_avatar_png(96, variant=variant))
            cur_prof = json.loads(row["profile_json"] or "{}")
            if not cur_prof:
                cur_prof = prof_default
            conn.execute(
                "UPDATE users SET profile_json = ?, avatar_name = COALESCE(avatar_name, ?) WHERE id = ?",
                (json.dumps(cur_prof, ensure_ascii=False), av_file, uid),
            )


def sync_demo_descriptions():
    """Обновить тексты демо-карточек из шаблона (только если ещё нет series_tag)."""
    with db.connect() as conn:
        for item in DEMO_SUBMISSIONS:
            row = conn.execute(
                "SELECT descriptions_json FROM submissions WHERE id = ?",
                (item["id"],),
            ).fetchone()
            if not row:
                continue
            try:
                cur = json.loads(row["descriptions_json"] or "{}")
            except Exception:
                cur = {}
            if "series_tag" not in cur:
                merged = {**item["descriptions"], **cur}
                merged["series_tag"] = item["descriptions"].get("series_tag", "")
                conn.execute(
                    "UPDATE submissions SET descriptions_json = ? WHERE id = ?",
                    (json.dumps(merged, ensure_ascii=False), item["id"]),
                )


def align_demo_slots():
    """Демо-карточки выравниваются по текущей неделе, если их дата из прошлой недели."""
    mon = monday_of()
    mon_s = mon.strftime("%Y-%m-%d")
    fri_s = (mon + timedelta(days=4)).strftime("%Y-%m-%dT23:59")
    now = db._utc_now()
    with db.connect() as conn:
        for sid, day_off, hour in DEMO_SLOT_MAP:
            row = conn.execute("SELECT scheduled_at FROM submissions WHERE id = ?", (sid,)).fetchone()
            if not row:
                continue
            cur = row["scheduled_at"] or ""
            if day_off is None or hour is None:
                continue
            if cur and (cur < mon_s or cur > fri_s):
                when = mon + timedelta(days=day_off)
                when_s = when.replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
                conn.execute(
                    "UPDATE submissions SET scheduled_at = ?, updated_at = ? WHERE id = ?",
                    (when_s, now, sid),
                )
