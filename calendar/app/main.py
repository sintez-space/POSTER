import hashlib
import io
import json
import re
import shutil
import uuid
from pathlib import Path

from fastapi import Body, Cookie, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from PIL import Image, ImageOps

from . import auth, db, demo_seed, export_poster
from .limits import load_limits
from .paths import AVATARS_DIR, CALENDAR_ROOT, UPLOADS_DIR, ensure_dirs
from .slots import SLOT_HOURS, validate_scheduled_at, week_range

app = FastAPI(title="POSTER Calendar", docs_url="/api/docs")
app.add_middleware(GZipMiddleware, minimum_size=1024)
STATIC = CALENDAR_ROOT / "static"
SESSION_COOKIE = "calendar_session"
MAX_AVATAR_BYTES = 100 * 1024
MAX_VIDEO_BYTES = 150 * 1024 * 1024
MAX_IMAGE_BYTES = 7 * 1024 * 1024
MAX_CAROUSEL_IMAGES = 15


def submission_dir(submission_id: str) -> Path:
    path = UPLOADS_DIR / submission_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_submission(submission_id: str):
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, u.display_name AS author_name, u.avatar_name AS author_avatar
            FROM submissions s
            JOIN users u ON u.id = s.user_id
            WHERE s.id = ?
            """,
            (submission_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="материал не найден")
    sub = db.row_to_submission(row)
    sub["author_name"] = row["author_name"]
    return sub


def assert_owner_or_admin(user, sub):
    if user["role"] == "admin" or sub["user_id"] == user["id"]:
        return
    raise HTTPException(status_code=403, detail="нет доступа")


def _ensure_thumb(src_path: Path, max_side: int = 260) -> Path:
    """Генерирует лёгкое превью (JPEG) для демонстрации в календаре, не трогая оригинал."""
    if not src_path.is_file() or src_path.stat().st_size < 1024:
        return src_path
    thumb_path = src_path.with_name(f".thumb_{max_side}_{src_path.name}.jpg")
    if thumb_path.is_file() and thumb_path.stat().st_mtime >= src_path.stat().st_mtime:
        return thumb_path
    try:
        with Image.open(src_path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            im.save(thumb_path, format="JPEG", quality=78, optimize=True)
        return thumb_path
    except Exception:
        return src_path


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


@app.on_event("startup")
def startup():
    ensure_dirs()
    db.init_db()
    auth.ensure_demo_users()
    demo_seed.ensure_demo_authors()
    demo_seed.ensure_demo_submissions()
    demo_seed.align_demo_slots()
    demo_seed.sync_demo_descriptions()


@app.get("/")
def index(calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user:
        return RedirectResponse("/login.html", status_code=302)
    if user["role"] == "admin":
        return RedirectResponse("/admin.html", status_code=302)
    return FileResponse(STATIC / "user.html")


@app.get("/login.html")
def login_page():
    return FileResponse(STATIC / "login.html")


@app.get("/admin.html")
def admin_page(calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user:
        return RedirectResponse("/login.html", status_code=302)
    if user["role"] != "admin":
        return RedirectResponse("/", status_code=302)
    return FileResponse(STATIC / "admin.html")


@app.get("/static/{path:path}")
def static_files(path: str):
    target = STATIC / path
    if not target.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(target)


@app.post("/api/login")
def api_login(login: str = Form(...), password: str = Form(...)):
    result, err = auth.login(login, password)
    if not result:
        raise HTTPException(status_code=400, detail=err or "неверный логин или пароль")
    token, user = result
    resp = JSONResponse({"ok": True, "user": user})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400 * 14)
    return resp


@app.post("/api/register")
def api_register(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    try:
        user_info = auth.register_user(first_name, last_name, email, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "message": "Заявка отправлена администратору! После подтверждения вы сможете войти по почте и паролю.",
        "user": user_info,
    }


@app.post("/api/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/me")
def api_me(calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="нужен вход")
    return db.row_to_user_profile(row)


@app.put("/api/me/profile")
def api_update_my_profile(
    body: dict = Body(default_factory=dict),
    calendar_session: str | None = Cookie(default=None),
):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    prof_in = body.get("profile") if isinstance(body.get("profile"), dict) else body
    allowed_keys = (
        "first_name",
        "last_name",
        "instagram_handle",
        "tiktok_handle",
        "youtube_handle",
        "vk_handle",
        "telegram_handle",
        "tags_cloud",
        "ig_mentions_cloud",
        "tt_mentions_cloud",
    )
    prof = {k: str(prof_in.get(k) or "").strip() for k in allowed_keys}
    combined_name = f"{prof['first_name']} {prof['last_name']}".strip()
    display_name = combined_name or str(body.get("display_name") or user["display_name"]).strip() or user["display_name"]
    with db.connect() as conn:
        conn.execute(
            "UPDATE users SET display_name = ?, profile_json = ? WHERE id = ?",
            (display_name, json.dumps(prof, ensure_ascii=False), user["id"]),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    return db.row_to_user_profile(row)


def _compress_avatar_bw(raw: bytes, max_bytes: int = MAX_AVATAR_BYTES) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im).convert("L")
            side = min(im.width, im.height)
            left = (im.width - side) // 2
            top = (im.height - side) // 2
            im = im.crop((left, top, left + side, top + side)).resize((240, 240), Image.Resampling.LANCZOS)
            for q in (86, 74, 62, 50, 38):
                buf = io.BytesIO()
                im.save(buf, format="WEBP", quality=q)
                if buf.tell() <= max_bytes:
                    return buf.getvalue()
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=50, optimize=True)
            return buf.getvalue()
    except Exception:
        return raw[:max_bytes]


@app.post("/api/me/avatar")
async def api_upload_my_avatar(
    avatar: UploadFile = File(...),
    calendar_session: str | None = Cookie(default=None),
):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    data = await avatar.read()
    if not data:
        raise HTTPException(status_code=400, detail="пустой файл")
    if len(data) > MAX_AVATAR_BYTES:
        data = _compress_avatar_bw(data, MAX_AVATAR_BYTES)
        ext = ".webp"
    else:
        ext = Path(avatar.filename or "avatar.webp").suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".webp"
    ensure_dirs()
    for old in AVATARS_DIR.glob(f"{user['id']}.*"):
        old.unlink(missing_ok=True)
    fname = f"{user['id']}{ext}"
    (AVATARS_DIR / fname).write_bytes(data)
    with db.connect() as conn:
        conn.execute("UPDATE users SET avatar_name = ? WHERE id = ?", (fname, user["id"]))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    return db.row_to_user_profile(row)


@app.get("/api/users/{user_id}/avatar")
def api_user_avatar(user_id: str):
    with db.connect() as conn:
        row = conn.execute("SELECT avatar_name FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not row["avatar_name"]:
        raise HTTPException(status_code=404, detail="нет аватара")
    path = AVATARS_DIR / row["avatar_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="файл аватара не найден")
    return FileResponse(path)


@app.get("/api/series-tags")
def api_get_series_tags(calendar_session: str | None = Cookie(default=None)):
    """Возвращает личные теги текущего пользователя (для выпадушки) и палитру всех тегов (для отрисовки календаря)."""
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    with db.connect() as conn:
        my_rows = conn.execute(
            "SELECT name, color FROM user_series_tags WHERE user_id = ? ORDER BY created_at ASC, name ASC",
            (user["id"],),
        ).fetchall()
        all_rows = conn.execute(
            "SELECT name, color FROM user_series_tags ORDER BY created_at ASC"
        ).fetchall()
    return {
        "mine": [{"name": r["name"], "color": r["color"]} for r in my_rows],
        "all": [{"name": r["name"], "color": r["color"]} for r in all_rows],
    }


@app.post("/api/series-tags")
def api_create_series_tag(
    body: dict = Body(default_factory=dict),
    calendar_session: str | None = Cookie(default=None),
):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    raw_name = str(body.get("name") or "").strip()
    if not raw_name:
        raise HTTPException(status_code=400, detail="введите название тега (одно слово)")
    if re.search(r"\s", raw_name):
        raise HTTPException(status_code=400, detail="название тега должно быть одним словом без пробелов")
    if len(raw_name) > 24:
        raise HTTPException(status_code=400, detail="название тега — до 24 символов")
    color = str(body.get("color") or "#2dd4bf").strip()
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        color = "#2dd4bf"
    now = db._utc_now()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_series_tags (user_id, name, color, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, name) DO UPDATE SET color = excluded.color
            """,
            (user["id"], raw_name, color, now),
        )
        my_rows = conn.execute(
            "SELECT name, color FROM user_series_tags WHERE user_id = ? ORDER BY created_at ASC, name ASC",
            (user["id"],),
        ).fetchall()
        all_rows = conn.execute(
            "SELECT name, color FROM user_series_tags ORDER BY created_at ASC"
        ).fetchall()
    return {
        "mine": [{"name": r["name"], "color": r["color"]} for r in my_rows],
        "all": [{"name": r["name"], "color": r["color"]} for r in all_rows],
    }


@app.get("/api/limits")
def api_limits(calendar_session: str | None = Cookie(default=None)):
    if not auth.user_by_token(calendar_session):
        raise HTTPException(status_code=401, detail="нужен вход")
    return load_limits()


@app.get("/api/calendar/month")
def api_calendar_month(year: int, month: int, calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    prefix = f"{year:04d}-{month:02d}"
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT substr(scheduled_at, 1, 10) AS day, COUNT(*) AS cnt
            FROM submissions
            WHERE scheduled_at LIKE ? AND scheduled_at != ''
            GROUP BY substr(scheduled_at, 1, 10)
            """,
            (prefix + "%",),
        ).fetchall()
    counts = {r["day"]: int(r["cnt"]) for r in rows if r["day"]}
    return {"year": year, "month": month, "counts": counts}


@app.get("/api/calendar/week")
def api_calendar_week(week_start: str, calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    try:
        start_s, end_s = week_range(week_start)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, u.display_name AS author_name, u.avatar_name AS author_avatar
            FROM submissions s
            JOIN users u ON u.id = s.user_id
            WHERE s.scheduled_at >= ? AND s.scheduled_at <= ?
            ORDER BY s.scheduled_at ASC, s.slot_part ASC
            """,
            (start_s, end_s),
        ).fetchall()
    items = []
    for row in rows:
        sub = db.row_to_submission(row)
        sub["author_name"] = row["author_name"]
        sub["is_mine"] = sub["user_id"] == user["id"]
        items.append(sub)
    return {"week_start": start_s, "week_end": end_s[:10], "slot_hours": list(SLOT_HOURS), "items": items}


@app.get("/api/meta/slots")
def api_meta_slots():
    from .slots import SLOT_LABELS

    return {"slot_hours": list(SLOT_HOURS), "slot_labels": list(SLOT_LABELS), "weekdays": ["Пн", "Вт", "Ср", "Чт", "Пт"]}


@app.get("/api/submissions/{submission_id}/cover")
def api_submission_cover(
    submission_id: str,
    thumb: int = 1,
    calendar_session: str | None = Cookie(default=None),
):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    sub = get_submission(submission_id)
    folder = UPLOADS_DIR / submission_id
    target = None
    if sub["cover_name"]:
        path = folder / sub["cover_name"]
        if path.is_file():
            target = path
    if not target and sub["carousel_files"]:
        first_slide = folder / sub["carousel_files"][0]
        if first_slide.is_file():
            target = first_slide
    if not target:
        raise HTTPException(status_code=404, detail="нет обложки")
    out_path = _ensure_thumb(target, 260) if thumb else target
    return FileResponse(out_path, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/submissions/{submission_id}/carousel/{index}")
def api_submission_carousel_item(
    submission_id: str,
    index: int,
    thumb: int = 1,
    calendar_session: str | None = Cookie(default=None),
):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    sub = get_submission(submission_id)
    files = sub["carousel_files"] or []
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="слайд не найден")
    path = UPLOADS_DIR / submission_id / files[index]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="файл не найден")
    out_path = _ensure_thumb(path, 360) if thumb else path
    return FileResponse(out_path, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/submissions/{submission_id}/video")
def api_submission_video(submission_id: str, calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    sub = get_submission(submission_id)
    if not sub["video_name"]:
        raise HTTPException(status_code=404, detail="нет видео")
    path = UPLOADS_DIR / submission_id / sub["video_name"]
    if not path.is_file() or path.stat().st_size < 32:
        raise HTTPException(status_code=404, detail="файл не найден")
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "public, max-age=86400", "Accept-Ranges": "bytes"},
    )


@app.get("/api/submissions/pool")
def api_pool_submissions(calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, u.display_name AS author_name, u.login AS author_login, u.avatar_name AS author_avatar
            FROM submissions s JOIN users u ON u.id = s.user_id
            ORDER BY u.display_name ASC, s.updated_at DESC
            """
        ).fetchall()
    out = []
    for row in rows:
        sub = db.row_to_submission(row)
        sub["author_name"] = row["author_name"]
        sub["author_login"] = row["author_login"]
        sub["is_mine"] = sub["user_id"] == user["id"]
        out.append(sub)
    return out


@app.get("/api/submissions/mine")
def api_my_submissions(calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, u.display_name AS author_name, u.avatar_name AS author_avatar
            FROM submissions s JOIN users u ON u.id = s.user_id
            WHERE s.user_id = ?
            ORDER BY s.updated_at DESC
            """,
            (user["id"],),
        ).fetchall()
    out = []
    for r in rows:
        sub = db.row_to_submission(r)
        sub["author_name"] = r["author_name"]
        sub["is_mine"] = True
        out.append(sub)
    return out


@app.get("/api/admin/submissions")
def api_admin_submissions(calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="только админ")
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, u.display_name AS author_name, u.login AS author_login, u.avatar_name AS author_avatar
            FROM submissions s JOIN users u ON u.id = s.user_id
            ORDER BY s.scheduled_at DESC
            """
        ).fetchall()
    out = []
    for row in rows:
        sub = db.row_to_submission(row)
        sub["author_name"] = row["author_name"]
        sub["author_login"] = row["author_login"]
        out.append(sub)
    return out


@app.get("/api/admin/authors")
def api_admin_authors(calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="только админ")
    with db.connect() as conn:
        u_rows = conn.execute(
            "SELECT * FROM users ORDER BY role DESC, display_name ASC"
        ).fetchall()
        sub_rows = conn.execute(
            "SELECT user_id, scheduled_at, status FROM submissions"
        ).fetchall()
        tag_rows = conn.execute(
            "SELECT user_id, name, color FROM user_series_tags ORDER BY created_at ASC"
        ).fetchall()

    stats_by_user: dict[str, dict[str, int]] = {}
    for sr in sub_rows:
        uid = sr["user_id"]
        st = stats_by_user.setdefault(uid, {"scheduled": 0, "unscheduled": 0, "approved": 0, "total": 0})
        st["total"] += 1
        if (sr["scheduled_at"] or "").strip():
            st["scheduled"] += 1
        else:
            st["unscheduled"] += 1
        if sr["status"] in ("approved", "exported"):
            st["approved"] += 1

    tags_by_user: dict[str, list[dict]] = {}
    for tr in tag_rows:
        tags_by_user.setdefault(tr["user_id"], []).append({"name": tr["name"], "color": tr["color"]})

    out = []
    for row in u_rows:
        prof_obj = db.row_to_user_profile(row)
        uid = row["id"]
        prof_obj["stats"] = stats_by_user.get(uid, {"scheduled": 0, "unscheduled": 0, "approved": 0, "total": 0})
        prof_obj["series_tags"] = tags_by_user.get(uid, [])
        out.append(prof_obj)
    return out


@app.get("/api/admin/pending-users")
def api_admin_pending_users(calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="только админ")
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE COALESCE(is_approved, 1) = 0 ORDER BY created_at DESC"
        ).fetchall()
    return [db.row_to_user_profile(r) for r in rows]


@app.post("/api/admin/users/{user_id}/approve")
def api_admin_approve_user(user_id: str, calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="только админ")
    with db.connect() as conn:
        conn.execute("UPDATE users SET is_approved = 1 WHERE id = ?", (user_id,))
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def api_admin_delete_user(user_id: str, calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="только админ")
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="нельзя удалить самого себя")
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_series_tags WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"ok": True}


def _check_series_tag_limit(conn, user_id: str, series_tag: str, exclude_id: str | None = None):
    tag = (series_tag or "").strip()
    if not tag:
        return
    rows = conn.execute("SELECT id, descriptions_json FROM submissions WHERE user_id = ?", (user_id,)).fetchall()
    count = 0
    for r in rows:
        if exclude_id and r["id"] == exclude_id:
            continue
        try:
            d = json.loads(r["descriptions_json"] or "{}")
        except Exception:
            d = {}
        if (d.get("series_tag") or "").strip() == tag:
            count += 1
    if count >= 10:
        raise HTTPException(status_code=400, detail=f"К тегу «{tag}» уже привязано максимум 10 карточек")


def _default_descriptions_from_profile(prof: dict) -> dict:
    out = {}
    if prof.get("tags_cloud"):
        out["tags"] = prof["tags_cloud"]
    collab = db.default_collab_from_profile(prof)
    if collab:
        out["instagram_collaborators"] = collab
    if prof.get("ig_mentions_cloud"):
        out["instagram_reel_tags"] = prof["ig_mentions_cloud"]
    if prof.get("tt_mentions_cloud"):
        out["tiktok_mentions"] = prof["tt_mentions_cloud"]
    return out


@app.post("/api/submissions")
def api_create_submission(
    scheduled_at: str = Form(default=""),
    slot_part: int = Form(default=1),
    media_kind: str = Form(default="video"),
    descriptions_json: str = Form(default=""),
    calendar_session: str | None = Cookie(default=None),
):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    raw_sched = (scheduled_at or "").strip()
    sp = 2 if int(slot_part) == 2 else 1
    mk = "carousel" if media_kind == "carousel" else "video"
    if raw_sched:
        try:
            when_s = validate_scheduled_at(raw_sched)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        when_s = ""

    with db.connect() as conn:
        u_row = conn.execute("SELECT profile_json FROM users WHERE id = ?", (user["id"],)).fetchone()
        prof = json.loads((u_row["profile_json"] if u_row else "{}") or "{}")
        initial_desc = _default_descriptions_from_profile(prof)
        if descriptions_json.strip():
            try:
                passed = json.loads(descriptions_json)
                if isinstance(passed, dict):
                    initial_desc.update(passed)
            except Exception:
                pass

        _check_series_tag_limit(conn, user["id"], initial_desc.get("series_tag", ""))

        if when_s:
            occ = conn.execute(
                "SELECT id FROM submissions WHERE scheduled_at = ? AND slot_part = ?",
                (when_s, sp),
            ).fetchone()
            if occ:
                raise HTTPException(status_code=400, detail="этот полуслот уже занят")
        sid = str(uuid.uuid4())
        now = db._utc_now()
        conn.execute(
            """
            INSERT INTO submissions (
                id, user_id, scheduled_at, slot_part, media_kind, status,
                descriptions_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?)
            """,
            (sid, user["id"], when_s, sp, mk, json.dumps(initial_desc, ensure_ascii=False), now, now),
        )
    submission_dir(sid)
    sub = get_submission(sid)
    sub["is_mine"] = True
    return sub


@app.post("/api/submissions/{submission_id}/duplicate")
def api_duplicate_submission(
    submission_id: str,
    calendar_session: str | None = Cookie(default=None),
):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    orig = get_submission(submission_id)
    assert_owner_or_admin(user, orig)

    new_id = str(uuid.uuid4())
    now = db._utc_now()
    desc_copy = dict(orig["descriptions"] or {})
    base_txt = (desc_copy.get("base") or "").strip()
    if base_txt and not base_txt.endswith("(копия)"):
        desc_copy["base"] = f"{base_txt} (копия)"

    src_dir = submission_dir(submission_id)
    dst_dir = submission_dir(new_id)
    for item in src_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, dst_dir / item.name)

    with db.connect() as conn:
        _check_series_tag_limit(conn, user["id"], desc_copy.get("series_tag", ""))
        conn.execute(
            """
            INSERT INTO submissions (
                id, user_id, scheduled_at, slot_part, media_kind, status,
                descriptions_json, video_name, cover_name, carousel_json, created_at, updated_at
            ) VALUES (?, ?, '', ?, ?, 'draft', ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                user["id"],
                1 if orig["media_kind"] == "video" else orig["slot_part"],
                orig["media_kind"],
                json.dumps(desc_copy, ensure_ascii=False),
                orig["video_name"],
                orig["cover_name"],
                json.dumps(orig["carousel_files"], ensure_ascii=False),
                now,
                now,
            ),
        )
    sub = get_submission(new_id)
    sub["is_mine"] = True
    return sub


def _reorder_existing_carousel(dest: Path, current_files: list[str], order_indices: list[int]) -> list[str]:
    """Переименовывает существующие слайды карусели по новому порядку: 01_<hash>.ext, 02_<hash>.ext ..."""
    valid = [current_files[i] for i in order_indices if 0 <= i < len(current_files)]
    if not valid:
        return current_files
    temp_paths = []
    for idx, fname in enumerate(valid, start=1):
        src = dest / fname
        if not src.is_file():
            continue
        data = src.read_bytes()
        h = _short_hash(data)
        ext = src.suffix.lower() or ".jpg"
        tmp = dest / f"__tmp_{idx:02d}_{h}{ext}"
        src.replace(tmp)
        temp_paths.append((tmp, f"{idx:02d}_{h}{ext}"))
    for t_old in dest.glob(".thumb_*"):
        t_old.unlink(missing_ok=True)
    new_names = []
    for tmp, final_name in temp_paths:
        final_path = dest / final_name
        tmp.replace(final_path)
        _ensure_thumb(final_path, 360)
        new_names.append(final_name)
    return new_names


@app.put("/api/submissions/{submission_id}")
def api_update_submission(
    submission_id: str,
    body: dict = Body(default_factory=dict),
    calendar_session: str | None = Cookie(default=None),
):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    sub = get_submission(submission_id)
    assert_owner_or_admin(user, sub)

    is_locked = sub["status"] in ("approved", "exported")
    unlocking_now = "status" in body and str(body["status"]) not in ("approved", "exported")

    if is_locked and not unlocking_now:
        if any(k in body for k in ("scheduled_at", "slot_part", "descriptions", "carousel_order", "media_kind")):
            raise HTTPException(
                status_code=403,
                detail="Карточка подтверждена и зафиксирована. Сначала снимите подтверждение.",
            )

    updates = []
    params = []
    if "descriptions" in body:
        payload = body["descriptions"]
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="descriptions должен быть объектом")
        with db.connect() as conn:
            _check_series_tag_limit(conn, sub["user_id"], payload.get("series_tag", ""), exclude_id=submission_id)
        updates.append("descriptions_json = ?")
        params.append(json.dumps(payload, ensure_ascii=False))
    if "carousel_order" in body and isinstance(body["carousel_order"], list):
        dest = submission_dir(submission_id)
        order_idx = [int(x) for x in body["carousel_order"] if isinstance(x, (int, float, str)) and str(x).isdigit()]
        reordered = _reorder_existing_carousel(dest, sub["carousel_files"] or [], order_idx)
        updates.append("carousel_json = ?")
        params.append(json.dumps(reordered, ensure_ascii=False))
    if "media_kind" in body:
        mk = "carousel" if body["media_kind"] == "carousel" else "video"
        updates.append("media_kind = ?")
        params.append(mk)
    if "status" in body:
        status = str(body["status"])
        if status not in db.STATUSES:
            raise HTTPException(status_code=400, detail="неизвестный статус")
        if user["role"] == "user":
            if is_locked:
                raise HTTPException(status_code=403, detail="карточка подтверждена админом и зафиксирована")
            if status not in ("draft", "submitted"):
                raise HTTPException(status_code=403, detail="подтверждать слот может только админ")
            updates.append("status = ?")
            params.append(status)
        else:
            updates.append("status = ?")
            params.append(status)
    if "scheduled_at" in body or "slot_part" in body:
        if user["role"] != "admin" and sub["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="чужой слот может менять только админ")
        if user["role"] == "user" and is_locked:
            raise HTTPException(status_code=403, detail="слот подтверждён админом и зафиксирован")
        raw_sched = str(body.get("scheduled_at", sub["scheduled_at"]) or "").strip()
        target_part = int(body.get("slot_part", sub["slot_part"]) or 1)
        target_part = 2 if target_part == 2 else 1

        if not raw_sched:
            when_s = ""
            if user["role"] == "admin" and sub["status"] == "approved":
                updates.append("status = ?")
                params.append("submitted")
        else:
            try:
                when_s = validate_scheduled_at(raw_sched)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            with db.connect() as conn:
                occ = conn.execute(
                    "SELECT id, user_id, status FROM submissions WHERE scheduled_at = ? AND slot_part = ? AND id != ?",
                    (when_s, target_part, submission_id),
                ).fetchone()
            if occ:
                if occ["status"] in ("approved", "exported"):
                    raise HTTPException(status_code=403, detail="Целевой слот подтверждён и зафиксирован. Сначала снимите подтверждение.")
                if user["role"] != "admin" and occ["user_id"] != user["id"]:
                    raise HTTPException(status_code=403, detail="нельзя занять или очистить чужой слот")
        updates.append("scheduled_at = ?")
        params.append(when_s)
        updates.append("slot_part = ?")
        params.append(target_part)

    if not updates:
        return sub
    updates.append("updated_at = ?")
    params.append(db._utc_now())
    params.append(submission_id)
    with db.connect() as conn:
        conn.execute(f"UPDATE submissions SET {', '.join(updates)} WHERE id = ?", params)
    out = get_submission(submission_id)
    out["is_mine"] = out["user_id"] == user["id"]
    return out


@app.post("/api/submissions/{submission_id}/files")
async def api_upload_files(
    submission_id: str,
    calendar_session: str | None = Cookie(default=None),
    video: UploadFile | None = File(default=None),
    cover: UploadFile | None = File(default=None),
    carousel: list[UploadFile] | None = File(default=None),
):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    sub = get_submission(submission_id)
    assert_owner_or_admin(user, sub)
    if sub["status"] in ("approved", "exported"):
        raise HTTPException(
            status_code=403,
            detail="Карточка подтверждена и зафиксирована. Сначала снимите подтверждение.",
        )
    dest = submission_dir(submission_id)
    video_name = sub["video_name"]
    cover_name = sub["cover_name"]
    carousel_files = list(sub["carousel_files"] or [])

    if video and video.filename:
        ext = Path(video.filename).suffix.lower() or ".mp4"
        tmp_video = dest / f"__uploading_video{ext}"
        total_v = 0
        try:
            with tmp_video.open("wb") as vf:
                while True:
                    chunk = await video.read(1024 * 1024)
                    if not chunk:
                        break
                    total_v += len(chunk)
                    if total_v > MAX_VIDEO_BYTES:
                        raise HTTPException(status_code=400, detail="Видео превышает лимит 150 МБ")
                    vf.write(chunk)
        except Exception:
            tmp_video.unlink(missing_ok=True)
            raise
        if sub["video_name"]:
            old = dest / sub["video_name"]
            if old.is_file():
                old.unlink()
        video_name = f"video{ext}"
        tmp_video.replace(dest / video_name)

    if cover and cover.filename:
        raw_cov = await cover.read()
        if len(raw_cov) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="Обложка превышает лимит 7 МБ")
        if sub["cover_name"]:
            old = dest / sub["cover_name"]
            if old.is_file():
                old.unlink()
        for t_old in dest.glob(".thumb_*_cover*"):
            t_old.unlink(missing_ok=True)
        ext = Path(cover.filename).suffix.lower() or ".jpg"
        cover_name = f"cover_{_short_hash(raw_cov)}{ext}"
        (dest / cover_name).write_bytes(raw_cov)
        _ensure_thumb(dest / cover_name, 260)

    if carousel:
        valid_uploads = [u for u in carousel if u and u.filename]
        if valid_uploads:
            slides_payload = []
            for up in valid_uploads[:MAX_CAROUSEL_IMAGES]:
                raw_slide = await up.read()
                if len(raw_slide) > MAX_IMAGE_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Картинка «{up.filename}» превышает лимит 7 МБ",
                    )
                slides_payload.append((up.filename, raw_slide))
            for old_name in carousel_files:
                old_p = dest / old_name
                if old_p.is_file():
                    old_p.unlink()
            for t_old in dest.glob(".thumb_*"):
                t_old.unlink(missing_ok=True)
            carousel_files = []
            for idx, (orig_name, raw_slide) in enumerate(slides_payload, start=1):
                ext = Path(orig_name).suffix.lower() or ".jpg"
                h = _short_hash(raw_slide)
                cname = f"{idx:02d}_{h}{ext}"
                (dest / cname).write_bytes(raw_slide)
                _ensure_thumb(dest / cname, 360)
                if idx == 1:
                    _ensure_thumb(dest / cname, 260)
                carousel_files.append(cname)

    now = db._utc_now()
    with db.connect() as conn:
        conn.execute(
            "UPDATE submissions SET video_name = ?, cover_name = ?, carousel_json = ?, updated_at = ? WHERE id = ?",
            (video_name, cover_name, json.dumps(carousel_files, ensure_ascii=False), now, submission_id),
        )
    return get_submission(submission_id)


@app.delete("/api/submissions/{submission_id}")
def api_delete_submission(submission_id: str, calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user:
        raise HTTPException(status_code=401, detail="нужен вход")
    sub = get_submission(submission_id)
    assert_owner_or_admin(user, sub)
    if sub["status"] in ("approved", "exported"):
        raise HTTPException(
            status_code=403,
            detail="Нельзя удалить подтверждённую карточку. Сначала снимите подтверждение.",
        )
    folder = submission_dir(submission_id)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    with db.connect() as conn:
        conn.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
    return {"ok": True}


@app.post("/api/admin/export-poster")
def api_export_poster(calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="только админ")
    return export_poster.export_approved()


@app.get("/api/admin/export-day-zip")
def api_export_day_zip(date: str, calendar_session: str | None = Cookie(default=None)):
    user = auth.user_by_token(calendar_session)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="только админ")
    zip_bytes, filename = export_poster.build_day_zip(date)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
