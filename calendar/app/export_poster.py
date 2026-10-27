import hashlib
import io
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone

from . import db
from .paths import POSTER_POSTS, SCHEDULE_JSON, UPLOADS_DIR, ensure_dirs


def _submission_dir(submission_id: str):
    return UPLOADS_DIR / submission_id


def _next_stems(count: int, existing: set[str]):
    nums = []
    for name in existing:
        m = re.match(r"^(\d{3})$", name)
        if m:
            nums.append(int(m.group(1)))
    start = max(nums) + 1 if nums else 1
    return [f"{n:03d}" for n in range(start, start + count)]


def _merge_csv_tokens(*parts: str) -> str:
    seen = []
    for part in parts:
        if not part:
            continue
        for tok in re.split(r"[,;\n]+", part):
            t = tok.strip()
            if t and t not in seen:
                seen.append(t)
    return ", ".join(seen)


def _format_mentions_at(raw: str) -> str:
    items = []
    for tok in re.split(r"[,;\s]+", raw or ""):
        t = tok.strip().lstrip("@")
        if t:
            items.append(f"@{t}")
    return " ".join(dict.fromkeys(items))


def _prepare_post_bundle(sub: dict, prof: dict, author_name: str, stem: str):
    """Формирует набор текстовых sidecar-файлов и метаданных поста для POSTER."""
    desc = sub["descriptions"] or {}
    base = (desc.get("base") or desc.get("tiktok") or "").strip()
    ig = (desc.get("instagram") or base).strip()
    tt = (desc.get("tiktok") or base).strip()
    yt_title = (desc.get("youtube_title") or "").strip()
    yt_body = (desc.get("youtube") or base).strip()
    vk = (desc.get("vk") or base).strip()

    tt_mentions = _merge_csv_tokens(desc.get("tiktok_mentions", ""), prof.get("tt_mentions_cloud", ""))
    tt_at = _format_mentions_at(tt_mentions)
    tt_final = f"{tt} {tt_at}".strip() if tt_at and tt_at not in tt else tt

    tags = _merge_csv_tokens(desc.get("tags", ""), prof.get("tags_cloud", ""))
    collab = _merge_csv_tokens(desc.get("instagram_collaborators", ""), db.default_collab_from_profile(prof))
    reel = _merge_csv_tokens(desc.get("instagram_reel_tags", ""), prof.get("ig_mentions_cloud", ""))

    author_handles = {
        k: prof.get(k, "")
        for k in ("instagram_handle", "tiktok_handle", "youtube_handle", "vk_handle", "telegram_handle")
        if prof.get(k)
    }

    text_files = {
        f"{stem}.txt": tt_final or base,
    }
    if ig and ig != tt_final:
        text_files[f"{stem}.instagram.txt"] = ig
    if yt_body:
        text_files[f"{stem}.youtube.txt"] = yt_body
    if yt_title:
        text_files[f"{stem}.youtube_title.txt"] = yt_title
    if vk and vk != tt_final:
        text_files[f"{stem}.vk.txt"] = vk
    if tags:
        text_files[f"{stem}.tags.txt"] = tags
    if collab:
        text_files[f"{stem}.collaborators.txt"] = collab
    if reel:
        text_files[f"{stem}.reel_tags.txt"] = reel
    if tt_mentions:
        text_files[f"{stem}.tiktok_mentions.txt"] = tt_mentions
    if author_handles:
        text_files[f"{stem}.author.json"] = json.dumps(
            {"author_name": author_name, "handles": author_handles},
            ensure_ascii=False,
            indent=2,
        )

    return text_files, author_handles


def build_day_zip(date_iso: str) -> tuple[bytes, str]:
    """
    Собирает все запланированные на день YYYY-MM-DD материалы в один ZIP-архив:
    - папка posts/ (готовая для запуска poster.py: 001.mp4, 001.txt, 001.cover.jpg, 001.tags.txt, 001.collaborators.txt, 001.reel_tags.txt и карусели)
    - папка slots/<HH-MM>_slot<1|2>_<Автор>/ (разложенные по времени и автору папки)
    - файл posts/poster_queue.json (полное расписание и цепочка задач для poster.py)
    """
    ensure_dirs()
    day_prefix = (date_iso or "").strip()[:10]
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, u.profile_json, u.display_name AS author_name
            FROM submissions s
            JOIN users u ON u.id = s.user_id
            WHERE s.scheduled_at LIKE ? AND s.scheduled_at != ''
            ORDER BY s.scheduled_at ASC, s.slot_part ASC
            """,
            (day_prefix + "%",),
        ).fetchall()

    buf = io.BytesIO()
    queue_entries = []

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for idx, row in enumerate(rows, start=1):
            stem = f"{idx:03d}"
            sub = db.row_to_submission(row)
            author_name = row["author_name"] or "Автор"
            safe_author = re.sub(r"[^\w\-]+", "_", author_name).strip("_") or "author"
            time_part = (sub["scheduled_at"][11:16] or "09:00").replace(":", "-")
            slot_folder = f"slots/{time_part}_slot{sub['slot_part']}_{safe_author}_{stem}"

            try:
                prof = json.loads(row["profile_json"] or "{}")
            except Exception:
                prof = {}

            src = _submission_dir(sub["id"])
            media_kind = sub["media_kind"]
            text_files, author_handles = _prepare_post_bundle(sub, prof, author_name, stem)

            video_out = None
            cover_out = None
            carousel_out = []

            if media_kind == "carousel":
                cfiles = sub["carousel_files"] or []
                for s_idx, fname in enumerate(cfiles[:15], start=1):
                    fsrc = src / fname
                    if fsrc.is_file():
                        data = fsrc.read_bytes()
                        h = hashlib.sha256(data).hexdigest()[:8]
                        ext = fsrc.suffix.lower() or ".jpg"
                        slide_name = f"{s_idx:02d}_{h}{ext}"
                        zf.writestr(f"posts/{stem}_carousel/{slide_name}", data)
                        zf.writestr(f"{slot_folder}/carousel/{slide_name}", data)
                        carousel_out.append(f"{stem}_carousel/{slide_name}")
            else:
                if sub["video_name"]:
                    vsrc = src / sub["video_name"]
                    if vsrc.is_file() and vsrc.stat().st_size >= 32:
                        ext = vsrc.suffix.lower() or ".mp4"
                        video_out = f"{stem}{ext}"
                        zf.write(vsrc, f"posts/{video_out}")
                        zf.write(vsrc, f"{slot_folder}/{video_out}")

            if sub["cover_name"]:
                csrc = src / sub["cover_name"]
                if csrc.is_file():
                    ext = csrc.suffix.lower() or ".jpg"
                    cover_out = f"{stem}.cover{ext}"
                    zf.write(csrc, f"posts/{cover_out}")
                    zf.write(csrc, f"{slot_folder}/{cover_out}")

            for fname, content in text_files.items():
                zf.writestr(f"posts/{fname}", content.encode("utf-8"))
                zf.writestr(f"{slot_folder}/{fname}", content.encode("utf-8"))

            entry_meta = {
                "stem": stem,
                "date": day_prefix,
                "schedule_start": sub["scheduled_at"][:16],
                "slot_part": sub["slot_part"],
                "media_kind": media_kind,
                "status": sub["status"],
                "submission_id": sub["id"],
                "author_name": author_name,
                "author_handles": author_handles,
                "series_tag": (sub["descriptions"] or {}).get("series_tag", ""),
                "slot_folder": slot_folder,
                "files": {
                    "video": video_out,
                    "cover": cover_out,
                    "carousel": carousel_out,
                    "text": f"{stem}.txt",
                },
            }
            zf.writestr(
                f"{slot_folder}/slot_info.json",
                json.dumps(entry_meta, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            queue_entries.append(entry_meta)

        manifest = {
            "version": 2,
            "date": day_prefix,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(queue_entries),
            "entries": queue_entries,
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        zf.writestr("posts/poster_queue.json", manifest_bytes)
        zf.writestr("poster_schedule.json", manifest_bytes)

    filename = f"poster_day_{day_prefix}.zip"
    return buf.getvalue(), filename


def export_approved():
    """Копирует одобренные материалы в POSTER/posts и пишет poster_schedule.json."""
    ensure_dirs()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, u.profile_json, u.display_name AS author_name
            FROM submissions s
            JOIN users u ON u.id = s.user_id
            WHERE s.status IN ('approved', 'submitted') AND s.scheduled_at IS NOT NULL AND s.scheduled_at != ''
            ORDER BY s.scheduled_at ASC, s.slot_part ASC
            """
        ).fetchall()

    stems = _next_stems(len(rows), {p.stem for p in POSTER_POSTS.glob("*") if p.is_file()})

    entries = []
    exported_ids = []

    for row, stem in zip(rows, stems):
        sub = db.row_to_submission(row)
        try:
            prof = json.loads(row["profile_json"] or "{}")
        except Exception:
            prof = {}

        src = _submission_dir(sub["id"])
        media_kind = sub["media_kind"]
        carousel_copied = []

        if media_kind == "carousel":
            cfiles = sub["carousel_files"] or []
            if not cfiles:
                continue
            for idx, fname in enumerate(cfiles[:15], start=1):
                fsrc = src / fname
                if fsrc.is_file():
                    data = fsrc.read_bytes()
                    h = hashlib.sha256(data).hexdigest()[:8]
                    ext = fsrc.suffix.lower() or ".jpg"
                    out_name = f"{stem}.{idx:02d}_{h}{ext}"
                    shutil.copy2(fsrc, POSTER_POSTS / out_name)
                    carousel_copied.append(out_name)
            if not carousel_copied:
                continue
        else:
            if not sub["video_name"]:
                continue
            video_src = src / sub["video_name"]
            if not video_src.is_file():
                continue
            shutil.copy2(video_src, POSTER_POSTS / f"{stem}.mp4")

        if sub["cover_name"]:
            cover_src = src / sub["cover_name"]
            if cover_src.is_file():
                shutil.copy2(cover_src, POSTER_POSTS / f"{stem}.cover.jpg")

        text_files, author_handles = _prepare_post_bundle(sub, prof, row["author_name"] or "Автор", stem)
        for fname, content in text_files.items():
            (POSTER_POSTS / fname).write_text(content, encoding="utf-8")

        entries.append(
            {
                "stem": stem,
                "media_kind": media_kind,
                "slot_part": sub["slot_part"],
                "schedule_start": sub["scheduled_at"][:16],
                "submission_id": sub["id"],
                "user_id": sub["user_id"],
                "author_handles": author_handles,
                "folder": "posts",
                "files": {
                    "video": f"{stem}.mp4" if media_kind == "video" else None,
                    "carousel": carousel_copied if media_kind == "carousel" else None,
                    "text": f"{stem}.txt",
                    "cover": f"{stem}.cover.jpg" if sub["cover_name"] else None,
                },
            }
        )
        exported_ids.append(sub["id"])

    payload = {
        "version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    SCHEDULE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (POSTER_POSTS / "poster_queue.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if exported_ids:
        now = db._utc_now()
        with db.connect() as conn:
            for sid in exported_ids:
                conn.execute(
                    "UPDATE submissions SET status = 'exported', updated_at = ? WHERE id = ?",
                    (now, sid),
                )

    return {"ok": True, "count": len(entries), "path": str(SCHEDULE_JSON)}
