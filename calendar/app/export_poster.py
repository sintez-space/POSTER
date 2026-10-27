import json
import re
import shutil
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


def export_approved():
    """Копирует одобренные материалы (видео и карусели) в POSTER/posts и пишет poster_schedule.json."""
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
                    import hashlib
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

        desc = sub["descriptions"]
        base = (desc.get("base") or desc.get("tiktok") or "").strip()
        ig = (desc.get("instagram") or base).strip()
        tt = (desc.get("tiktok") or base).strip()
        yt_title = (desc.get("youtube_title") or "").strip()
        yt_body = (desc.get("youtube") or base).strip()
        vk = (desc.get("vk") or base).strip()

        tt_mentions = _merge_csv_tokens(desc.get("tiktok_mentions", ""), prof.get("tt_mentions_cloud", ""))
        tt_at = _format_mentions_at(tt_mentions)
        tt_final = f"{tt} {tt_at}".strip() if tt_at and tt_at not in tt else tt

        (POSTER_POSTS / f"{stem}.txt").write_text(tt_final or base, encoding="utf-8")
        if ig and ig != tt_final:
            (POSTER_POSTS / f"{stem}.instagram.txt").write_text(ig, encoding="utf-8")
        if yt_body:
            (POSTER_POSTS / f"{stem}.youtube.txt").write_text(yt_body, encoding="utf-8")
        if yt_title:
            (POSTER_POSTS / f"{stem}.youtube_title.txt").write_text(yt_title, encoding="utf-8")
        if vk and vk != tt_final:
            (POSTER_POSTS / f"{stem}.vk.txt").write_text(vk, encoding="utf-8")

        tags = _merge_csv_tokens(desc.get("tags", ""), prof.get("tags_cloud", ""))
        if tags:
            (POSTER_POSTS / f"{stem}.tags.txt").write_text(tags, encoding="utf-8")

        collab = _merge_csv_tokens(desc.get("instagram_collaborators", ""), db.default_collab_from_profile(prof))
        if collab:
            (POSTER_POSTS / f"{stem}.collaborators.txt").write_text(collab, encoding="utf-8")

        reel = _merge_csv_tokens(desc.get("instagram_reel_tags", ""), prof.get("ig_mentions_cloud", ""))
        if reel:
            (POSTER_POSTS / f"{stem}.reel_tags.txt").write_text(reel, encoding="utf-8")

        if tt_mentions:
            (POSTER_POSTS / f"{stem}.tiktok_mentions.txt").write_text(tt_mentions, encoding="utf-8")

        author_handles = {
            k: prof.get(k, "")
            for k in ("instagram_handle", "tiktok_handle", "youtube_handle", "vk_handle", "telegram_handle")
            if prof.get(k)
        }
        if author_handles:
            (POSTER_POSTS / f"{stem}.author.json").write_text(
                json.dumps({"author_name": row["author_name"], "handles": author_handles}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

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
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    SCHEDULE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if exported_ids:
        now = db._utc_now()
        with db.connect() as conn:
            for sid in exported_ids:
                conn.execute(
                    "UPDATE submissions SET status = 'exported', updated_at = ? WHERE id = ?",
                    (now, sid),
                )

    return {"ok": True, "count": len(entries), "path": str(SCHEDULE_JSON)}
