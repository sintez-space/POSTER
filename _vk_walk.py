"""Пошаговый проход VK клипов: снимки UI на каждом шаге."""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from poster import (
    connect,
    first_visible,
    load_config,
    load_sites,
    video_for_form,
    vk_caption_field,
    vk_ensure_new_clip,
    vk_publish_scope,
    vk_upload_video,
    vk_wait_publish_form,
    describe_fields,
    load_posts,
    abs_under,
    ROOT,
)

OUT = Path(__file__).resolve().parent / "_vk_walk"
OUT.mkdir(exist_ok=True)


def snap(page, name):
    path = OUT / f"{name}.txt"
    try:
        path.write_text(page.locator("body").aria_snapshot(timeout=15000), encoding="utf-8")
    except Exception as exc:
        path.write_text(f"aria error: {exc}", encoding="utf-8")
    try:
        page.screenshot(path=OUT / f"{name}.png", full_page=True)
    except Exception:
        pass
    print("snap", name, "->", path.name)


cfg = load_config()
site = load_sites()["vk"]
posts = load_posts(abs_under(ROOT, cfg.get("posts_folder") or "posts"), 1, cfg)
if not posts:
    raise SystemExit("нет постов")
post = posts[0]
video = post["video"]

pw, ctx = connect(cfg["port"], 250)
# URL из уже открытой вкладки кабинета
dash_url = ""
for p in ctx.pages:
    u = p.url or ""
    if "cabinet.vkvideo.ru" in u:
        dash_url = u.split("?")[0] + "?" + u.split("?", 1)[1] if "?" in u else u
        # полный URL
        dash_url = u
        print("found tab:", dash_url[:200])
        break
if not dash_url:
    dash_url = "https://cabinet.vkvideo.ru/dashboard/@public210918942"

page = ctx.new_page()
step = "open"
try:
    # клипы: subsection часто video_clips
    from poster import vk_normalize_url

    clips_url = vk_normalize_url(dash_url or cfg.get("vk_url") or "")
    print("goto", clips_url)
    page.goto(clips_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    snap(page, "01_list")
    step = "new_clip"
    vk_ensure_new_clip(page, site)
    page.wait_for_timeout(1000)
    snap(page, "02_new_clip_modal")
    step = "file"
    vk_upload_video(page, site, video)
    print("upload sent", video_for_form(video))
    page.wait_for_timeout(3000)
    snap(page, "03_after_upload")
    step = "form"
    scope, editor = vk_wait_publish_form(page, site)
    snap(page, "04_publish_form")
    scope2 = vk_publish_scope(page)
    fields = describe_fields(scope2)
    (OUT / "fields.json").write_text(repr(fields), encoding="utf-8")
    print("caption field ok", editor is not None)
    # schedule row
    sched = first_visible(scope2.get_by_text(re.compile(r"Когда опубликовать", re.I)))
    print("schedule label", sched is not None)
    chip = first_visible(
        scope2.get_by_text(re.compile(r"(Сегодня|Завтра|\d{1,2}\s+[а-яa-z]{3,}\s+в)", re.I))
    )
    print("schedule chip", chip.inner_text()[:80] if chip else None)
    btn = first_visible(
        scope2.get_by_role("button", name=re.compile(r"Запланировать публикацию", re.I))
    )
    if btn:
        print("schedule btn enabled", btn.is_enabled())
    say("walk ok до формы, publish не жму")
except Exception as exc:
    snap(page, f"err_{step}")
    print("FAIL", step, exc)
    raise
finally:
    print("OUT", OUT)
    # pw.stop() — оставляем вкладку для ручного осмотра
