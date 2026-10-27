"""Заполняет формы поста в уже открытом Chrome. Кнопку публикации не нажимает.

Chrome запускается отдельно (из панели или вручную):
  chrome --remote-debugging-port=9333 --user-data-dir=<свой профиль>
Скрипт только подключается по CDP и открывает новые вкладки.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
# Текст кнопки, которую нельзя нажимать. «Запланировать» тоже: это часто сама кнопка отправки.
PUBLISH = re.compile(
    r"post|publish|share|опубликовать|поделиться|запланировать|schedule|отложить",
    re.I,
)


class StopSite(Exception):
    def __init__(self, step, message, verification=False):
        super().__init__(message)
        self.step = step
        self.verification = verification


def say(*parts):
    print(*parts, flush=True)


def install_dialog_guard(target):
    """beforeunload / «Reload site?» — всегда Cancel, иначе Playwright висит."""

    def on_dialog(dialog):
        msg = dialog.message or ""
        say("диалог:", dialog.type, msg[:120])
        leave = dialog.type == "beforeunload" or re.search(
            r"reload site|changes you made|may not be saved|"
            r"обновить|перезагруз|не сохран|изменения.*не будут",
            msg,
            re.I,
        )
        try:
            if leave:
                dialog.dismiss()
                say("диалог: оставили страницу (Cancel)")
            else:
                dialog.accept()
        except Exception as exc:
            say("диалог не закрыт:", exc)

    target.on("dialog", on_dialog)


def snap_five(when):
    """Ближайшие минуты, кратные 5. У TikTok других в списке нет."""
    base = when.replace(second=0, microsecond=0)
    minute = (base.minute + 2) // 5 * 5
    return base.replace(minute=0) + timedelta(minutes=minute)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_config(path=None):
    file = Path(path) if path else ROOT / "config.json"
    if not file.is_absolute():
        file = ROOT / file
    cfg = load_json(file)
    cfg["_path"] = str(file)
    return cfg


def load_sites():
    return load_json(ROOT / "sites.json")


def abs_under(root, value):
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path


def assert_local(url):
    host = (urlparse(url).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(f"LLM только на localhost, получен {url}")


def natural_key(path):
    chunks = re.split(r"(\d+)", path.stem)
    return [int(chunk) if chunk.isdigit() else chunk.lower() for chunk in chunks]


def normalize_tag(tag):
    token = re.sub(r"\s+", "", (tag or "").strip().lstrip("#"))
    return f"#{token}" if token else ""


def compose(caption, tags, limit):
    """Склеивает текст и хэштеги и укладывает в лимит сети."""
    body = (caption or "").strip()
    extra = []
    for tag in tags:
        token = normalize_tag(tag)
        if token and token.lower() not in body.lower():
            extra.append(token)
    tail = " ".join(extra)
    text = f"{body}\n{tail}".strip() if body and tail else (body or tail)
    if len(text) <= limit:
        return text
    if not tail or len(tail) >= limit:
        return text[:limit].rstrip()
    room = max(0, limit - len(tail) - 1)
    return f"{body[:room].rstrip()}\n{tail}".strip()


def is_video(path):
    """Ролик по расширению или по заголовку файла, если расширения нет."""
    if not path.is_file() or path.name.endswith(".txt"):
        return False
    if path.suffix.lower() in VIDEO_EXT:
        return True
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return False
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return True
    return head.startswith(b"\x1a\x45\xdf\xa3")


POST_TEXT_SUFFIXES = (".tags", ".collaborators", ".reel_tags", ".youtube_title")


def is_post_text_file(path):
    if path.suffix.lower() != ".txt":
        return False
    stem = path.stem
    return not any(stem.endswith(suffix) for suffix in POST_TEXT_SUFFIXES)


def parse_accounts(raw):
    """Логины через запятую или с новой строки; пробелы после запятой не мешают."""
    if not raw:
        return []
    found = []
    for part in re.split(r"[,;\n]+", raw):
        name = re.sub(r"^@+", "", part.strip())
        if name:
            found.append(name)
    return found


def merge_accounts(*raws):
    """Объединяет списки из config, instagram.txt и sidecar без дубликатов."""
    seen = set()
    out = []
    for raw in raws:
        for name in parse_accounts(raw):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
    return out


def read_sidecar(text_file, kind):
    path = text_file.with_name(f"{text_file.stem}.{kind}.txt")
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def resolve_instagram_people(text_file, folder, cfg):
    """Собирает всех из config, instagram.txt и sidecar поста (пробелы после запятой ок)."""
    collab_raws = [str(cfg.get("instagram_collaborators") or "").strip()]
    reel_raws = [str(cfg.get("instagram_reel_tags") or "").strip()]
    meta = folder / "instagram.txt"
    if meta.exists():
        lines = [line.strip() for line in meta.read_text(encoding="utf-8").splitlines()]
        while lines and not lines[-1]:
            lines.pop()
        if lines:
            collab_raws.append(lines[0])
        if len(lines) > 1:
            reel_raws.append(lines[1])
    collab_raws.append(read_sidecar(text_file, "collaborators"))
    reel_raws.append(read_sidecar(text_file, "reel_tags"))
    return merge_accounts(*collab_raws), merge_accounts(*reel_raws)


def read_caption(text_file):
    caption = text_file.read_text(encoding="utf-8").strip() if text_file.exists() else ""
    tags_file = text_file.with_name(f"{text_file.stem}.tags.txt")
    tags = []
    if tags_file.exists():
        raw = tags_file.read_text(encoding="utf-8")
        tags = [part for part in re.split(r"[\s,]+", raw) if part.strip()]
    return caption, tags


def resolve_youtube_fields(text_file, folder, cfg, caption, tags):
    """Название до 100 символов; описание до 5000 (текст поста + хэштеги)."""
    title = ""
    desc_extra = ""
    meta = folder / "youtube.txt"
    if meta.exists():
        lines = [line.strip() for line in meta.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            title = lines[0]
        if len(lines) > 1:
            desc_extra = "\n".join(lines[1:])
    side_title = read_sidecar(text_file, "youtube_title")
    if side_title:
        title = side_title.split("\n", 1)[0].strip()
    cfg_title = str(cfg.get("youtube_title") or "").strip()
    if cfg_title and not title:
        title = cfg_title.split("\n", 1)[0].strip()
    if not title:
        title = (caption.split("\n", 1)[0].strip() if caption else "") or text_file.stem
    title = title[:100]
    if desc_extra:
        description = desc_extra[:5000]
    else:
        description = compose(caption, tags, 5000)
    return title, description


def make_post(video, text_file, folder, cfg):
    caption, tags = read_caption(text_file)
    collaborators, reel_tags = resolve_instagram_people(text_file, folder, cfg)
    yt_title, yt_description = resolve_youtube_fields(text_file, folder, cfg, caption, tags)
    return {
        "stem": text_file.stem,
        "video": video.resolve(),
        "caption": caption,
        "tags": tags,
        "collaborators": collaborators,
        "reel_tags": reel_tags,
        "youtube_title": yt_title,
        "youtube_description": yt_description,
    }


def load_posts(folder, limit, cfg=None):
    folder = Path(folder)
    cfg = cfg or {}
    if not folder.exists():
        raise SystemExit(f"нет папки постов: {folder}")
    files = [path for path in folder.iterdir() if path.is_file()]
    videos = sorted((path for path in files if is_video(path)), key=natural_key)
    texts = sorted((path for path in files if is_post_text_file(path)), key=natural_key)
    used = set()
    posts = []
    loose = []
    for video in videos:
        text_file = folder / f"{video.stem}.txt"
        if text_file.exists():
            used.add(text_file.resolve())
            posts.append(make_post(video, text_file, folder, cfg))
        else:
            loose.append(video)
    free_texts = [path for path in texts if path.resolve() not in used]
    if len(loose) == 1 and len(free_texts) == 1:
        say(f"имя не совпало, беру {loose[0].name} вместе с {free_texts[0].name}")
        posts.append(make_post(loose[0], free_texts[0], folder, cfg))
    else:
        for video in loose:
            say(f"{video.name}: нет текста с тем же именем")
        for text_file in free_texts:
            say(f"{text_file.name}: нет видео с тем же именем")
    posts.sort(key=lambda post: natural_key(Path(post["stem"])))
    if limit and limit > 0:
        posts = posts[:limit]
    return posts


def video_for_form(video):
    """В форму уходит имя с .mp4, даже если исходный файл скачан без расширения."""
    if video.suffix.lower() in VIDEO_EXT:
        return video
    stage = ROOT / ".upload"
    stage.mkdir(exist_ok=True)
    alias = stage / f"{video.stem}.mp4"
    if alias.exists():
        alias.unlink()
    try:
        os.link(video, alias)
    except OSError:
        shutil.copy2(video, alias)
    say(f"в форму отдам как {alias.name}")
    return alias


def models_url(chat_url):
    base = chat_url.rstrip("/")
    suffix = "/chat/completions"
    if not base.endswith(suffix):
        raise RuntimeError("lm_url должен заканчиваться на /v1/chat/completions")
    return base[: -len(suffix)] + "/models"


def strip_model_text(content):
    content = (content or "").strip()
    content = re.sub(r"^```(?:\w+)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content).strip()
    if len(content) >= 2 and content[0] == content[-1] and content[0] in {'"', "'"}:
        content = content[1:-1].strip()
    return content


def adapt_text(cfg, site_cfg, text):
    assert_local(cfg["lm_url"])
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("для --adapt установи пакет requests в то же окружение Python") from exc

    model = (cfg.get("lm_model") or "").strip()
    if not model:
        found = requests.get(models_url(cfg["lm_url"]), timeout=5)
        found.raise_for_status()
        rows = found.json().get("data") or []
        if not rows:
            raise RuntimeError("LM Studio не отдал ни одной модели")
        model = rows[0]["id"]
    prompt = site_cfg["adapt_prompt"].replace("{limit}", str(site_cfg["char_limit"]))
    response = requests.post(
        cfg["lm_url"],
        json={
            "model": model,
            "temperature": 0.3,
            "max_tokens": 800,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
        },
        timeout=90,
    )
    response.raise_for_status()
    content = strip_model_text(response.json()["choices"][0]["message"]["content"])
    limit = int(site_cfg["char_limit"])
    if len(content) > limit:
        content = content[:limit].rstrip()
    return content


def norm(text):
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def texts_match(expected, actual):
    exp = norm(expected)
    act = norm(actual)
    return bool(exp) and (exp == act or exp in act)


def dump_error(page, site_id, stem, step, exc):
    folder = ROOT / "errors" / f"{site_id}_{re.sub(r'[^\w.-]+', '_', stem)}"
    folder.mkdir(parents=True, exist_ok=True)
    info = {
        "site": site_id,
        "post": stem,
        "step": step,
        "error": str(exc),
        "traceback": traceback.format_exc(),
    }
    try:
        info["url"] = page.url
    except Exception as err:
        info["url_error"] = str(err)
    frames = []
    try:
        for index, frame in enumerate(page.frames):
            frames.append(frame.url)
            if frame == page.main_frame:
                continue
            try:
                (folder / f"frame-{index}.html").write_text(frame.content(), encoding="utf-8")
            except Exception as err:
                frames.append(f"frame {index}: {err}")
    except Exception as err:
        info["frames_error"] = str(err)
    info["frames"] = frames
    (folder / "error.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        (folder / "page.html").write_text(page.content(), encoding="utf-8")
    except Exception as err:
        (folder / "page_error.txt").write_text(str(err), encoding="utf-8")
    try:
        page.screenshot(path=str(folder / "page.png"), full_page=True, timeout=15000)
    except Exception:
        try:
            page.screenshot(path=str(folder / "page.png"), timeout=10000)
        except Exception as err:
            (folder / "screenshot_error.txt").write_text(str(err), encoding="utf-8")
    aria_parts = []
    try:
        aria_parts.append(page.locator("body").aria_snapshot(timeout=10000))
    except Exception as err:
        aria_parts.append(f"aria_snapshot: {err}")
    try:
        snap = page.accessibility.snapshot()
        (folder / "accessibility.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as err:
        (folder / "accessibility.json").write_text(
            json.dumps({"error": str(err)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (folder / "aria.txt").write_text("\n".join(aria_parts), encoding="utf-8")
    say(f"диагностика: {folder}")
    return folder


_popup_site = None
_accepting = False


def guard_click(locator):
    """Клик только если это не кнопка публикации или отложки."""
    if not _accepting and _popup_site is not None:
        try:
            accept_popups(locator.page, _popup_site)
        except Exception:
            pass
    info = locator.evaluate(
        """el => {
            const btn = el.closest('button,[role="button"],[type="submit"]');
            const node = btn || el;
            return {
                name: (node.innerText || node.getAttribute('aria-label') || '').trim().slice(0, 120),
                isButton: !!btn
            };
        }"""
    )
    if info.get("isButton") and PUBLISH.search(info.get("name") or ""):
        raise StopSite("click", f"отказ: не нажимаю «{info.get('name')}»")
    locator.scroll_into_view_if_needed()
    locator.click()


def first_visible(locator):
    try:
        count = locator.count()
    except Exception:
        return None
    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def page_text(page):
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def blocked_reason(page, site):
    url = (page.url or "").lower()
    for part in site.get("login_url_parts") or []:
        if part.lower() in url:
            return f"похоже на вход: {page.url}"
    body = page_text(page).casefold()
    for needle in site.get("logged_out_texts") or []:
        if needle.casefold() in body:
            return "нет входа. Залогинься в этом окне Chrome сам, пароль скрипт не вводит."
    for needle in site.get("verify_texts") or []:
        if needle.casefold() in body:
            return f"страница просит проверку ({needle}). Остановился, капчу не обхожу."
    return ""


def instagram_popup_skip(overlay):
    """Композер IG и Sharing не закрываем через accept_labels / одиночную кнопку."""
    try:
        label = (overlay.get_attribute("aria-label") or "").lower()
        text = (overlay.inner_text(timeout=900) or "")[:1200].lower()
    except Exception:
        return True
    blob = f"{label} {text}"
    if re.search(
        r"sharing|discard post|create new post|новая публика|tag people|отметить людей|"
        r"add collaborators|crop|обрез|\bedit\b|schedule content|запланировать контент",
        blob,
        re.I,
    ):
        return True
    if re.search(r"profile picture|checkmark|checkbox", text, re.I) and re.search(r"\bdone\b|готово", text, re.I):
        return True
    return False


def vk_popup_skip(overlay):
    try:
        text = overlay.inner_text()
    except Exception:
        return False
    return bool(
        re.search(
            r"Публикация клипа|Новый клип|Прервать загрузку|Когда опубликовать",
            text,
            re.I,
        )
    )


def vk_dismiss_abort_dialog(page):
    dialog = page.get_by_role("dialog", name=re.compile(r"Прервать загрузку", re.I))
    cancel = first_visible(dialog.get_by_role("button", name=re.compile(r"^Отмена$", re.I)))
    if cancel is not None:
        cancel.click(force=True, timeout=3000)
        page.wait_for_timeout(400)
        say("VK: оставил загрузку клипа — «Отмена» в «Прервать загрузку»")


def accept_popups(scope, site=None):
    """Подтверждает окна, которые перекрывают страницу. Кнопку публикации не трогает."""
    global _accepting
    site = site or _popup_site
    if not site or _accepting:
        return
    page = scope.page if hasattr(scope, "page") else scope
    page_url = page.url if hasattr(page, "url") else ""
    if "vkvideo.ru" in page_url:
        vk_dismiss_abort_dialog(page)
    if "instagram.com" in (site.get("url") or ""):
        cancel_discard_post(page)
        cancel_sharing_dialog(page)
    labels = site.get("accept_labels") or []
    _accepting = True
    try:
        confirm_schedule_save(scope, site)
        overlays = scope.locator("[role=dialog], [role=alertdialog], [aria-modal=true]")
        for _ in range(6):
            clicked = False
            try:
                count = overlays.count()
            except Exception:
                return
            for index in range(count):
                overlay = overlays.nth(index)
                try:
                    if not overlay.is_visible():
                        continue
                except Exception:
                    continue
                if "instagram.com" in (site.get("url") or "") and instagram_popup_skip(overlay):
                    continue
                if "vkvideo.ru" in page_url and vk_popup_skip(overlay):
                    continue
                button = None
                for label in labels:
                    button = first_visible(
                        overlay.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I))
                    )
                    if button:
                        break
                if button is None:
                    visible = []
                    buttons = overlay.get_by_role("button")
                    try:
                        total = min(buttons.count(), 6)
                    except Exception:
                        total = 0
                    for button_index in range(total):
                        item = buttons.nth(button_index)
                        try:
                            if item.is_visible():
                                visible.append(item)
                        except Exception:
                            continue
                    if len(visible) == 1:
                        button = visible[0]
                if button is None:
                    continue
                try:
                    name = norm(button.inner_text())
                except Exception:
                    name = ""
                if "vkvideo.ru" in page_url and re.fullmatch(
                    r"Закрыть|Close",
                    name,
                    re.I,
                ):
                    continue
                try:
                    guard_click(button)
                except Exception:
                    continue
                say("закрыл перекрытие:", name or "кнопка")
                clicked = True
                break
            if not clicked:
                return
            try:
                (scope.page if hasattr(scope, "page") else scope).wait_for_timeout(250)
            except Exception:
                return
    finally:
        _accepting = False


def find_video_input(page, selector):
    for frame in page.frames:
        loc = frame.locator(selector)
        try:
            count = loc.count()
        except Exception:
            continue
        fallback = None
        for index in range(count):
            item = loc.nth(index)
            accept = (item.get_attribute("accept") or "").lower()
            if "image" in accept and "video" not in accept:
                continue
            if "video" in accept or "mp4" in accept or accept == "":
                return frame, item
            fallback = (frame, item)
        if fallback:
            return fallback
    return None, None


def wait_upload_form(page, site):
    timeout_s = 25
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        reason = blocked_reason(page, site)
        if reason:
            raise StopSite("login", reason, verification=True)
        frame, field = find_video_input(page, site["file_input"])
        if field is not None:
            return frame, field
        page.wait_for_timeout(250)
    reason = blocked_reason(page, site)
    if reason:
        raise StopSite("login", reason, verification=True)
    raise StopSite("file", "не вижу поле загрузки файла")


def pick_editor(scope, site):
    # Мелкий contenteditable на странице — это поиск, не подпись.
    min_area = 8000
    best = None
    best_area = 0
    for selector in site.get("caption_selectors") or []:
        loc = scope.locator(selector)
        try:
            count = loc.count()
        except Exception:
            continue
        for index in range(count):
            item = loc.nth(index)
            try:
                if not item.is_visible():
                    continue
                box = item.bounding_box()
            except Exception:
                continue
            if not box:
                continue
            area = box["width"] * box["height"]
            if area >= min_area and area > best_area:
                best, best_area = item, area
        if best:
            return best
    for role in site.get("caption_roles") or []:
        for name in site.get("caption_names") or []:
            item = first_visible(scope.get_by_role(role, name=re.compile(name, re.I)))
            if item:
                return item
    raise StopSite("caption", "поле описания не найдено")


def editor_text(editor):
    try:
        tag = (editor.evaluate("el => el.tagName") or "").upper()
    except Exception:
        tag = ""
    if tag in {"TEXTAREA", "INPUT"}:
        try:
            return editor.input_value()
        except Exception:
            return ""
    try:
        return editor.inner_text()
    except Exception:
        return ""


def clear_editor(page, editor):
    guard_click(editor)
    try:
        editor.evaluate("el => el.focus()")
    except Exception:
        pass
    for _ in range(4):
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.press("Delete")
        if not norm(editor_text(editor)):
            return
    leftover = editor_text(editor) or ""
    for _ in range(min(len(leftover) + 2, 160)):
        page.keyboard.press("Backspace")
        if not norm(editor_text(editor)):
            return


def insert_caption(page, editor, text, close_suggest=True):
    """Вставка целиком, затем по кускам, если всплывающая подсказка съела хэштег."""
    clear_editor(page, editor)
    page.keyboard.insert_text(text)
    if close_suggest:
        page.keyboard.press("Escape")
    if texts_match(text, editor_text(editor)):
        return editor_text(editor)
    clear_editor(page, editor)
    for part in re.split(r"(#\S+)", text):
        if not part:
            continue
        page.keyboard.insert_text(part)
        if close_suggest and part.startswith("#"):
            page.keyboard.press("Escape")
    if close_suggest:
        page.keyboard.press("Escape")
    got = editor_text(editor)
    if not texts_match(text, got):
        raise StopSite("caption", f"текст в поле не совпал. ожидал {text!r}, в поле {got!r}")
    return got


def visible_value(locator):
    try:
        return (locator.input_value() or "").strip()
    except Exception:
        try:
            return norm(locator.inner_text())
        except Exception:
            return ""


def commit_value(page, locator, values):
    last = ""
    for value in values:
        guard_click(locator)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.insert_text(value)
        page.keyboard.press("Escape")
        last = visible_value(locator)
        if value[:5] in last or value[-5:] in last:
            return last
    return last


def find_when_field(scope, names, selectors):
    item = first_visible(scope.get_by_role("textbox", name=re.compile(names, re.I)))
    if item:
        return item
    item = first_visible(scope.get_by_role("combobox", name=re.compile(names, re.I)))
    if item:
        return item
    for selector in selectors:
        item = first_visible(scope.locator(selector))
        if item:
            return item
    return None


def describe_fields(scope):
    loc = scope.locator("input, [role='textbox'], [role='radio'], [contenteditable='true']")
    try:
        count = min(loc.count(), 40)
    except Exception:
        return []
    found = []
    for index in range(count):
        item = loc.nth(index)
        try:
            if not item.is_visible():
                continue
            found.append(item.evaluate(
                """el => ({
                    tag: el.tagName,
                    type: el.getAttribute('type'),
                    role: el.getAttribute('role'),
                    label: el.getAttribute('aria-label'),
                    placeholder: el.getAttribute('placeholder'),
                    text: (el.innerText || el.value || '').trim().slice(0, 60)
                })"""
            ))
        except Exception:
            continue
    return found


def find_cover(video):
    folder = video.parent
    for name in (
        f"{video.stem}.jpg", f"{video.stem}.jpeg", f"{video.stem}.png", f"{video.stem}.webp",
        f"{video.stem}.cover.jpg", f"{video.stem}.cover.png",
    ):
        path = folder / name
        if path.exists():
            return path
    return None


def schedule_prompt_open(scope, site):
    return first_visible(scope.get_by_text(re.compile(site.get("schedule_allow_text") or "scheduled posting", re.I))) is not None


def confirm_schedule_save(scope, site):
    """Жмёт Allow. Без этого TikTok не открывает дату и время."""
    if not schedule_prompt_open(scope, site):
        return False
    button = first_visible(scope.get_by_role("button", name=re.compile(site.get("schedule_allow_button") or r"^Allow$", re.I)))
    if button is None:
        button = first_visible(scope.get_by_text(re.compile(site.get("schedule_allow_button") or r"^Allow$", re.I)))
    if button is None:
        return False
    button.click(force=True)
    say("разрешил сохранение видео для отложки")
    scope.wait_for_timeout(400)
    return True


def clear_schedule_permission(scope, site):
    """Окно Allow закрывается до полей даты и времени."""
    for _ in range(4):
        if not schedule_prompt_open(scope, site):
            return
        if not confirm_schedule_save(scope, site):
            break
    if schedule_prompt_open(scope, site):
        raise StopSite("schedule", "окно Allow всё ещё на экране, дату не трогаю")


def enable_schedule(scope, site):
    """Сначала Allow, если окно уже есть. Потом подпись Schedule. Потом снова Allow, и только после этого дата."""
    clear_schedule_permission(scope, site)
    radio = scope.locator(site["schedule_input"]).first
    label = radio.locator("xpath=ancestor::label[1]")
    label.scroll_into_view_if_needed()
    for _ in range(3):
        clear_schedule_permission(scope, site)
        if radio.is_checked():
            break
        label.locator("span", has_text=re.compile(site["schedule_name"], re.I)).last.click(timeout=2000)
        scope.wait_for_timeout(500)
        clear_schedule_permission(scope, site)
    if not radio.is_checked():
        radio.evaluate("el => el.click()")
        scope.wait_for_timeout(400)
        clear_schedule_permission(scope, site)
    if not radio.is_checked():
        raise StopSite("schedule", "переключатель Schedule не включился")
    clear_schedule_permission(scope, site)
    say("отложка включена, окно Allow закрыто")


def schedule_fields(scope):
    """В блоке отложки первое поле — время, второе — дата."""
    loc = scope.locator("[class*='scheduled-picker'] input.TUXTextInputCore-input")
    time_field = None
    date_field = None
    try:
        count = min(loc.count(), 4)
    except Exception:
        return None, None
    for index in range(count):
        item = loc.nth(index)
        try:
            if not item.is_visible():
                continue
            value = item.input_value(timeout=1000)
        except Exception:
            continue
        if re.fullmatch(r"\d{2}:\d{2}", value or ""):
            time_field = item
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
            date_field = item
    return time_field, date_field


def pick_calendar_day(scope, when):
    """Число дня только внутри открытого календаря, не по всей странице."""
    day = str(when.day)
    calendar = scope.locator("[class*='calendar'], [class*='Calendar']")
    try:
        count = min(calendar.count(), 4)
    except Exception:
        count = 0
    for index in range(count):
        box = calendar.nth(index)
        try:
            if not box.is_visible():
                continue
        except Exception:
            continue
        options = box.get_by_text(day, exact=True)
        try:
            total = min(options.count(), 4)
        except Exception:
            continue
        for option_index in range(total):
            item = options.nth(option_index)
            try:
                if not item.is_visible():
                    continue
                cls = (item.get_attribute("class") or "").lower()
            except Exception:
                continue
            if "disabled" in cls or "outside" in cls:
                continue
            item.click(timeout=2000)
            return True
    return False


def picker_open(page):
    try:
        return bool(page.evaluate(
            """() => [...document.querySelectorAll(".tiktok-timepicker-time-picker-container")].some((el) => {
                const style = getComputedStyle(el);
                const box = el.getBoundingClientRect();
                return style.display !== "none" && style.visibility !== "hidden"
                    && !String(el.className).includes("invisible")
                    && box.height > 40
                    && el.querySelectorAll("span.tiktok-timepicker-left").length >= 12;
            })"""
        ))
    except Exception:
        return False


def clock_point(page, selector, text):
    """Прокручивает колонку так, чтобы ячейка стала видна, и возвращает её центр."""
    return page.evaluate(
        """(arg) => {
            const host = [...document.querySelectorAll(".tiktok-timepicker-time-picker-container")].find((el) => {
                const style = getComputedStyle(el);
                const box = el.getBoundingClientRect();
                return style.display !== "none" && style.visibility !== "hidden"
                    && !String(el.className).includes("invisible")
                    && box.height > 40
                    && el.querySelector("span.tiktok-timepicker-left");
            });
            if (!host) return null;
            host.scrollIntoView({block: "center", inline: "nearest"});
            const node = [...host.querySelectorAll(arg.selector)].find((el) => (el.textContent || "").trim() === arg.text);
            if (!node) return null;
            const item = node.closest(".tiktok-timepicker-option-item") || node;
            const box = node.closest(".tiktok-timepicker-time-scroll-container");
            if (box) box.scrollTop = item.offsetTop - (box.clientHeight - item.clientHeight) / 2;
            const rect = node.getBoundingClientRect();
            if (rect.width < 2 || rect.height < 2) return null;
            if (rect.bottom < 0 || rect.top > window.innerHeight) return null;
            return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        }""",
        {"selector": selector, "text": text},
    )


def pick_clock(page, hour, minute):
    """Обычный клик по ячейке вне видимой части списка не срабатывает. Сначала прокрутка, потом мышь."""
    hour_point = clock_point(page, "span.tiktok-timepicker-left", f"{hour:02d}")
    minute_point = clock_point(page, "span.tiktok-timepicker-right", f"{minute:02d}")
    if not hour_point or not minute_point:
        raise StopSite("schedule", f"в списке времени нет {hour:02d}:{minute:02d}")
    page.mouse.click(hour_point["x"], hour_point["y"])
    page.wait_for_timeout(300)
    minute_point = clock_point(page, "span.tiktok-timepicker-right", f"{minute:02d}")
    if not minute_point:
        raise StopSite("schedule", f"минута {minute:02d} пропала из списка")
    page.mouse.click(minute_point["x"], minute_point["y"])


def close_open_picker(time_field):
    """Escape этот список не закрывает. Повторный клик по полю времени закрывает."""
    page = time_field.page
    opened = page.evaluate(
        """() => {
            const picker = document.querySelector(".tiktok-timepicker-time-picker-container");
            if (!picker) return false;
            const style = getComputedStyle(picker);
            return style.display !== "none" && style.visibility !== "hidden" && !picker.className.includes("invisible");
        }"""
    )
    if opened:
        time_field.click(timeout=2000)


def wait_value(field, want, seconds=3):
    deadline = time.monotonic() + seconds
    got = ""
    while True:
        try:
            got = field.input_value(timeout=1000)
        except Exception:
            got = ""
        if got == want or time.monotonic() >= deadline:
            return got
        field.page.wait_for_timeout(200)


def set_schedule(page, scope, site, when):
    when = snap_five(when)
    enable_schedule(scope, site)
    clear_schedule_permission(scope, site)
    page.keyboard.press("Escape")
    want_time = f"{when.hour:02d}:{when.minute:02d}"
    want_date = when.strftime("%Y-%m-%d")
    time_field, date_field = schedule_fields(scope)
    if time_field is None or date_field is None:
        raise StopSite("schedule", f"не вижу поля даты и времени. поля: {describe_fields(scope)}")
    if date_field.input_value(timeout=1000) != want_date:
        date_field.click(timeout=2000)
        if not pick_calendar_day(scope, when):
            page.keyboard.press("Escape")
            raise StopSite("schedule", f"в календаре не выбрал {want_date}")
    page.keyboard.press("Escape")
    got_time = time_field.input_value(timeout=1000)
    if got_time != want_time:
        for attempt in range(1, 4):
            say(f"время {want_time}, попытка {attempt}")
            if not picker_open(page):
                try:
                    time_field.click(timeout=2000)
                except Exception as exc:
                    say(f"поле времени не нажалось: {exc}")
                    continue
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not picker_open(page):
                    page.wait_for_timeout(200)
            if not picker_open(page):
                say("список времени не открылся за 3 с")
                continue
            try:
                pick_clock(page, when.hour, when.minute)
            except StopSite as exc:
                say(str(exc))
                close_open_picker(time_field)
                continue
            got_time = wait_value(time_field, want_time, seconds=2)
            if got_time == want_time:
                say(f"время встало с попытки {attempt}")
                break
            say(f"в поле {got_time or 'пусто'}, список закрываю и пробую снова")
            close_open_picker(time_field)
        else:
            raise StopSite("schedule", f"за 3 попытки в поле так и не {want_time}, сейчас {got_time or 'пусто'}")
    close_open_picker(time_field)
    got_date = wait_value(date_field, want_date)
    got_time = wait_value(time_field, want_time)
    say(f"расписание в форме: {got_date} {got_time}")
    if got_date != want_date or got_time != want_time:
        raise StopSite("schedule", f"ожидал {want_date} {want_time}, в форме {got_date} {got_time}")


def press_schedule(page, scope, site, when):
    """Жмёт кнопку отправки отложки только если дата и время уже стоят в полях."""
    when = snap_five(when)
    want_time = f"{when.hour:02d}:{when.minute:02d}"
    want_date = when.strftime("%Y-%m-%d")
    time_field, date_field = schedule_fields(scope)
    if time_field is None or date_field is None:
        raise StopSite("schedule", "перед кнопкой не вижу поля даты и времени")
    got_date = date_field.input_value(timeout=1000)
    got_time = time_field.input_value(timeout=1000)
    if got_date != want_date or got_time != want_time:
        raise StopSite("schedule", f"кнопку не жму: в форме {got_date} {got_time}, нужно {want_date} {want_time}")
    pattern = re.compile(site.get("schedule_submit") or r"^(Schedule|Запланировать)$", re.I)
    button = first_visible(scope.get_by_role("button", name=pattern))
    if button is None:
        raise StopSite("schedule", "кнопку Запланировать не нашёл")
    button.scroll_into_view_if_needed(timeout=2000)
    button.click(timeout=3000)
    say("нажал Запланировать")
    page.wait_for_timeout(600)
    dialog = scope.locator("[role='dialog'], [role='alertdialog']")
    confirm = first_visible(dialog.get_by_role("button", name=pattern))
    if confirm is not None:
        confirm.click(timeout=3000)
        say("подтвердил отложку в окне")
        page.wait_for_timeout(600)
    notice = first_visible(dialog.get_by_text(re.compile(r"still checking|continue to post|провер", re.I)))
    if notice is not None:
        carry_on = first_visible(dialog.get_by_role("button", name=re.compile(r"^(Post now|Continue|Продолжить)$", re.I)))
        if carry_on is not None:
            carry_on.click(timeout=3000)
            say("подтвердил отправку, пока проверка ролика не кончилась")


def image_file_input(page):
    inputs = page.locator("input[type='file']")
    try:
        count = inputs.count()
    except Exception:
        return None
    for index in range(count):
        item = inputs.nth(index)
        accept = (item.get_attribute("accept") or "").lower()
        if "image" in accept or "png" in accept or "jpg" in accept or "webp" in accept:
            return item
    return None


def try_set_cover(page, scope, site, video):
    if not find_cover(video):
        return
    if not site.get("cover_open"):
        say("файл обложки есть, но у сети в sites.json нет cover_*")
        return
    set_cover(page, scope, site, video)


def cover_opener(scope, site):
    pattern = re.compile(site["cover_open"], re.I)
    for locator in (
        scope.get_by_label(pattern),
        scope.get_by_role("button", name=pattern),
        scope.get_by_text(pattern),
    ):
        item = first_visible(locator)
        if item is not None:
            return item
    return None


def set_cover(page, scope, site, video):
    opener = cover_opener(scope, site)
    if opener is None:
        raise StopSite("cover", "кнопка обложки не найдена")
    opener.click()
    cover = find_cover(video)
    deadline = time.monotonic() + 8
    ready = False
    while time.monotonic() < deadline:
        if first_visible(scope.get_by_text(re.compile(site["cover_confirm"], re.I))):
            ready = True
            break
        if first_visible(scope.get_by_text(re.compile(site["cover_upload_tab"], re.I))):
            ready = True
            break
        page.wait_for_timeout(200)
    if not ready:
        raise StopSite("cover", "окно обложки не открылось")
    if cover:
        # Не кликать по «Upload cover»: это открывает системное окно «Открыть» и скрипт замирает.
        field = image_file_input(page)
        if field is not None:
            field.set_input_files(str(cover), timeout=60000)
        else:
            tab = first_visible(scope.get_by_text(re.compile(site["cover_upload_tab"], re.I)))
            if tab is None:
                raise StopSite("cover", f"нет поля файла для {cover.name}")
            with page.expect_file_chooser(timeout=5000) as picked:
                tab.click()
            picked.value.set_files(str(cover))
        say(f"обложка: {cover.name}")
    else:
        say("файла обложки нет, подтверждаю кадр из ролика")
    confirm = first_visible(scope.get_by_role("button", name=re.compile(site["cover_confirm"], re.I)))
    if confirm is None:
        confirm = first_visible(scope.get_by_text(re.compile(site["cover_confirm"], re.I)))
    if confirm is None:
        raise StopSite("cover", "нет кнопки подтверждения обложки")
    guard_click(confirm)
    say("обложка подтверждена")


def set_location(page, scope, site):
    query = site.get("location") or "Moscow"
    field = first_visible(scope.locator(site["location_input"]))
    if field is None:
        field = first_visible(scope.get_by_placeholder(re.compile(r"location|место|локац", re.I)))
    if field is None:
        raise StopSite("location", "поле локации не найдено")
    field.click()
    field.fill(query)
    pattern = re.compile(rf"^{re.escape(query)}\b", re.I)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        options = scope.locator("[role='option'], [role='listitem'], [class*='option'], [class*='Option']")
        try:
            count = min(options.count(), 30)
        except Exception:
            count = 0
        for index in range(count):
            item = options.nth(index)
            try:
                if not item.is_visible():
                    continue
                text = norm(item.inner_text())
            except Exception:
                continue
            if not text or not pattern.search(text):
                continue
            item.click()
            say("локация:", text)
            page.keyboard.press("Escape")
            return
        page.wait_for_timeout(250)
    raise StopSite("location", f"в подсказках нет «{query}»")


def wait_upload_hint(scope, site):
    pattern = re.compile(site.get("upload_ready") or "$^", re.I)
    deadline = time.monotonic() + int(site.get("upload_timeout_s") or 90)
    while time.monotonic() < deadline:
        accept_popups(scope, site)
        if first_visible(scope.get_by_text(pattern)):
            return True
        scope.wait_for_timeout(400)
    return False


def youtube_upload_dialog(page):
    custom = page.locator("ytcp-uploads-dialog").first
    if first_visible(custom):
        return custom
    dialog = page.get_by_role("dialog").filter(
        has=page.get_by_role("button", name=re.compile(r"^Далее$|^Next$|^Дальше$", re.I))
    )
    if first_visible(dialog.first):
        return dialog.first
    return page.get_by_role("dialog").first


def youtube_selected_tab(scope):
    tab = first_visible(scope.get_by_role("tab", selected=True))
    if tab is None:
        tab = first_visible(scope.locator("[role=tab][aria-selected=true]"))
    if tab is None:
        return ""
    try:
        return norm(tab.inner_text())
    except Exception:
        return ""


def youtube_host_id(site, kind):
    defaults = {"title": "title-textarea", "description": "description-textarea"}
    return (site.get(f"{kind}_host_id") or defaults.get(kind) or kind).lstrip("#")


def youtube_apply_text(page, site, kind, text):
    """Текст в shadow DOM диалога загрузки — без клика по host, чтобы не ронять DOM."""
    host_id = youtube_host_id(site, kind)
    snippet = (text or "")[:12]
    for attempt in range(1, 6):
        ok = page.evaluate(
            """([hostId, value]) => {
                const scope = document.querySelector("ytcp-uploads-dialog") || document;
                const host = scope.querySelector("#" + hostId);
                if (!host) return false;
                const box = host.querySelector("#textbox")
                    || host.querySelector("[contenteditable=true]")
                    || host.querySelector("textarea");
                if (!box) return false;
                box.focus();
                const range = document.createRange();
                range.selectNodeContents(box);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                document.execCommand("delete", false, null);
                document.execCommand("insertText", false, value);
                box.dispatchEvent(new InputEvent("input", {bubbles: true}));
                const got = (box.innerText || box.textContent || "").trim();
                return !value || got.includes(value.slice(0, Math.min(8, value.length)));
            }""",
            [host_id, text or ""],
        )
        if ok:
            page.wait_for_timeout(350)
            return
        page.wait_for_timeout(600)
        say(f"youtube {kind}: повтор записи {attempt}/5")
    raise StopSite(kind, f"не записал текст в #{host_id}")


def youtube_scroll_audience(page):
    page.evaluate(
        """() => {
            const root = document.querySelector("ytcp-uploads-dialog") || document;
            const nodes = [...root.querySelectorAll("ytcp-video-metadata-editor, tp-yt-paper-radio, label, div")];
            const hit = nodes.find((el) => /не для детей|not made for kids/i.test(el.innerText || ""));
            if (hit) hit.scrollIntoView({block: "center", behavior: "instant"});
        }"""
    )
    page.wait_for_timeout(300)


def youtube_set_not_for_kids(page, site):
    dialog = youtube_upload_dialog(page)
    scope = dialog if first_visible(dialog) else page
    youtube_scroll_audience(page)
    patterns = site.get("audience_no_kids") or [
        r"No, it's not made for kids",
        r"Нет, это видео не для детей",
        r"Нет, это не для детей",
        r"not made for kids",
    ]
    if isinstance(patterns, str):
        patterns = [patterns]
    for raw in patterns:
        pat = re.compile(raw, re.I)
        radio = first_visible(scope.get_by_role("radio", name=pat))
        if radio is not None:
            radio.click(timeout=3000)
            page.wait_for_timeout(400)
            say("аудитория: не для детей")
            return True
        item = first_visible(scope.get_by_text(pat))
        if item is not None:
            item.click(timeout=3000)
            page.wait_for_timeout(400)
            say("аудитория: не для детей")
            return True
    body = page_text(page)
    if re.search(r"not made for kids|не для детей", body, re.I):
        raise StopSite("audience", "переключатель «не для детей» не найден")
    return False


def wait_youtube_processing_overlay(page, timeout_s=8):
    """Не ждём конца SD-обработки — «Далее» обычно доступно раньше."""
    dialog = youtube_upload_dialog(page)
    deadline = time.monotonic() + max(1, int(timeout_s))
    while time.monotonic() < deadline:
        overlay = first_visible(dialog.locator(".ytcpVideoInfoUploadingOverlay"))
        if overlay is None:
            return True
        page.wait_for_timeout(400)
    return False


def youtube_dialog_step(page):
    dialog = youtube_upload_dialog(page)
    try:
        return (dialog.get_attribute("workflow-step") or "").upper()
    except Exception:
        return ""


def youtube_next_button(page, site):
    dialog = youtube_upload_dialog(page)
    next_pat = re.compile(site.get("next_name") or r"^Next$|^Далее$|^Дальше$", re.I)
    for selector in ("#next-button", "ytcp-button#next-button", "ytcp-uploads-dialog"):
        root = dialog.locator(selector) if selector != "ytcp-uploads-dialog" else dialog
        button = first_visible(root.get_by_role("button", name=next_pat))
        if button is not None:
            return button
    return first_visible(dialog.get_by_role("button", name=next_pat))


def youtube_upload_root(page):
    return page.evaluate(
        """() => {
            const custom = document.querySelector("ytcp-uploads-dialog");
            if (custom) return "custom";
            const dialogs = [...document.querySelectorAll("[role=dialog]")];
            const hit = dialogs.find((d) =>
                [...d.querySelectorAll("button")].some((b) => /^(Далее|Next|Дальше)$/i.test((b.innerText || "").trim()))
            );
            return hit ? "dialog" : "";
        }"""
    )


def youtube_click_next(page, site):
    work = youtube_upload_dialog(page)
    button = youtube_next_button(page, site)
    if button is not None:
        try:
            button.click(force=True, timeout=8000)
            page.wait_for_timeout(700)
            say("youtube: Далее")
            return True
        except Exception as exc:
            say(f"youtube: клик «Далее» через locator: {exc}")
    result = page.evaluate(
        """() => {
            const root = document.querySelector("ytcp-uploads-dialog")
                || [...document.querySelectorAll("[role=dialog]")].find((d) =>
                    [...d.querySelectorAll("button")].some((b) => /^(Далее|Next|Дальше)$/i.test((b.innerText || "").trim()))
                );
            if (!root) return "no-root";
            let btn = root.querySelector("#next-button");
            if (btn && btn.tagName.toLowerCase() === "ytcp-button") btn = btn.querySelector("button") || btn;
            if (!btn) {
                btn = [...root.querySelectorAll("button")].find((b) =>
                    /^(Далее|Next|Дальше)$/i.test((b.innerText || "").trim())
                );
            }
            if (!btn) return "no-btn";
            const disabled = btn.disabled || btn.getAttribute("aria-disabled") === "true"
                || btn.closest("[disabled], [aria-disabled=true]");
            if (disabled) return "disabled";
            btn.click();
            return "ok";
        }"""
    )
    if result == "ok":
        page.wait_for_timeout(700)
        say("youtube: Далее")
        return True
    say(f"youtube: «Далее» не нажата ({result})")
    return False


def youtube_click_tab(page, pattern):
    work = youtube_upload_dialog(page)
    tab = first_visible(work.get_by_role("tab", name=pattern))
    if tab is None:
        return False
    tab.click(force=True, timeout=4000)
    page.wait_for_timeout(700)
    try:
        say(f"youtube: вкладка «{norm(tab.inner_text())}»")
    except Exception:
        say("youtube: переключил вкладку")
    return True


def youtube_on_visibility_step(page, site):
    work = youtube_upload_dialog(page)
    tab = youtube_selected_tab(work)
    if re.fullmatch(r"Доступ|Visibility", tab, re.I):
        return True
    if youtube_dialog_step(page) in {"VISIBILITY", "PUBLISH", "REVIEW"}:
        return True
    return False


def youtube_advance_to_visibility(page, site):
    work = youtube_upload_dialog(page)
    if not first_visible(work) and not youtube_upload_root(page):
        say("youtube: диалог загрузки не найден")
        return False
    tab_order = site.get("wizard_tabs") or [
        r"^Дополнения$",
        r"^Video elements$",
        r"^Предварительная проверка$",
        r"^Checks$",
        r"^Доступ$",
        r"^Visibility$",
    ]
    if isinstance(tab_order, str):
        tab_order = [tab_order]
    for attempt in range(1, 14):
        if youtube_on_visibility_step(page, site):
            say(f"youtube: экран «Доступ» ({attempt})")
            return True
        work = youtube_upload_dialog(page)
        tab = youtube_selected_tab(work)
        say(f"youtube: сейчас «{tab or youtube_dialog_step(page) or '?'}»")
        if re.search(r"Информац|Details", tab, re.I) or youtube_dialog_step(page) == "DETAILS":
            youtube_set_not_for_kids(page, site)
        wait_youtube_processing_overlay(page, timeout_s=2)
        if youtube_click_next(page, site):
            continue
        advanced = False
        for raw in tab_order:
            if youtube_on_visibility_step(page, site):
                break
            if youtube_click_tab(page, re.compile(raw, re.I)):
                advanced = True
                if youtube_on_visibility_step(page, site):
                    break
        if not advanced:
            page.wait_for_timeout(600)
    return youtube_on_visibility_step(page, site)


def youtube_pick_public_schedule(page, site):
    work = youtube_upload_dialog(page)
    public_pat = re.compile(site.get("visibility_public") or r"Public|Открытый доступ|^Открытый$", re.I)
    schedule_pat = re.compile(
        site.get("visibility_schedule") or r"Schedule|Запланировать публикацию|Запланировать",
        re.I,
    )
    public = first_visible(work.get_by_role("radio", name=public_pat))
    if public is None:
        public = first_visible(work.get_by_text(public_pat))
    if public is not None:
        public.click(force=True, timeout=4000)
        page.wait_for_timeout(400)
    schedule = first_visible(work.get_by_role("radio", name=schedule_pat))
    if schedule is None:
        schedule = first_visible(work.get_by_label(schedule_pat))
    if schedule is None:
        schedule = first_visible(work.get_by_text(schedule_pat))
    if schedule is not None:
        schedule.click(force=True, timeout=4000)
        page.wait_for_timeout(450)
        say("видимость: открытый доступ, по расписанию")
        return True
    picked = page.evaluate(
        """() => {
            const root = document.querySelector("ytcp-uploads-dialog")
                || [...document.querySelectorAll("[role=dialog]")].find((d) =>
                    [...d.querySelectorAll("button")].some((b) => /^(Далее|Next)$/i.test((b.innerText || "").trim()))
                );
            if (!root) return false;
            const labels = [...root.querySelectorAll("label, tp-yt-paper-radio, ytcp-ve, div, button")];
            const pub = labels.find((el) => /Открытый доступ|^Public$/i.test((el.innerText || "").trim()));
            if (pub) (pub.closest("tp-yt-paper-radio, label, button") || pub).click();
            const sched = labels.find((el) => /Запланировать|Schedule/i.test(el.innerText || ""));
            if (sched) (sched.closest("tp-yt-paper-radio, label, button") || sched).click();
            return !!(pub && sched);
        }"""
    )
    if picked:
        page.wait_for_timeout(400)
        say("видимость: открытый доступ, по расписанию (js)")
        return True
    raise StopSite("visibility", "режим «запланировать» / Public не найден")


def youtube_schedule_fields(page):
    date_field = None
    time_field = None
    loc = page.locator("input[type='date'], input[type='datetime-local'], input[type='text']")
    try:
        total = min(loc.count(), 24)
    except Exception:
        total = 0
    for index in range(total):
        item = loc.nth(index)
        try:
            if not item.is_visible():
                continue
            label = " ".join(
                filter(
                    None,
                    [
                        item.get_attribute("aria-label"),
                        item.get_attribute("placeholder"),
                        item.get_attribute("name"),
                    ],
                )
            ).lower()
            value = (item.input_value() or "").strip()
        except Exception:
            continue
        if "date" in label or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            date_field = item
        if "time" in label or re.fullmatch(r"\d{1,2}:\d{2}", value):
            time_field = item
    if date_field is None:
        date_field = first_visible(page.locator("input[type='date']"))
    if time_field is None:
        time_field = first_visible(
            page.locator("input[type='time'], input[aria-label*='time' i], input[aria-label*='время' i]")
        )
    return date_field, time_field


def youtube_set_schedule(page, site, when):
    when = snap_five(when)
    youtube_pick_public_schedule(page, site)
    want_date = when.strftime("%Y-%m-%d")
    want_time = f"{when.hour:02d}:{when.minute:02d}"
    date_field, time_field = youtube_schedule_fields(page)
    if date_field is not None:
        date_field.click(timeout=2000)
        date_field.fill(want_date)
        page.keyboard.press("Escape")
    if time_field is not None:
        time_field.click(timeout=2000)
        commit_value(page, time_field, [want_time])
        page.keyboard.press("Escape")
    else:
        spin_date = first_visible(page.get_by_role("spinbutton", name=re.compile(r"date|дата", re.I)))
        spin_time = first_visible(page.get_by_role("spinbutton", name=re.compile(r"time|время", re.I)))
        if spin_date is not None:
            commit_value(page, spin_date, [want_date, when.strftime("%b %d, %Y")])
        if spin_time is not None:
            commit_value(page, spin_time, [want_time])
    say(f"расписание YouTube: {want_date} {want_time}")


def youtube_press_schedule(page, site, when):
    when = snap_five(when)
    dialog = youtube_upload_dialog(page)
    scope = dialog if first_visible(dialog) else page
    pat = re.compile(
        site.get("schedule_submit") or r"^Schedule$|^Запланировать$|Запланировать публикацию|Сохранить",
        re.I,
    )
    for role in ("button", "link"):
        button = first_visible(scope.get_by_role(role, name=pat))
        if button is not None:
            break
    else:
        button = first_visible(page.get_by_role("button", name=pat))
    if button is None:
        raise StopSite("schedule", "кнопку Schedule/Сохранить не нашёл. Publish сейчас не нажимал")
    button.scroll_into_view_if_needed(timeout=3000)
    button.click(force=True, timeout=8000)
    say("нажал Schedule (YouTube)")
    page.wait_for_timeout(1200)


def youtube_suppress_reload_prompt(page):
    """YouTube вешает onbeforeunload на форму загрузки — без этого Chrome блокирует UI."""
    page.evaluate(
        """() => {
            try { window.onbeforeunload = null; } catch (e) {}
            const stop = (e) => { e.stopImmediatePropagation(); };
            window.addEventListener("beforeunload", stop, true);
        }"""
    )


def wait_youtube_upload(page, site, timeout_s=None):
    timeout_s = int(timeout_s or site.get("upload_timeout_s") or 180)
    deadline = time.monotonic() + timeout_s
    host = youtube_host_id(site, "title")
    while time.monotonic() < deadline:
        accept_popups(page, site)
        ready = page.evaluate(
            """(hostId) => {
                const scope = document.querySelector("ytcp-uploads-dialog") || document;
                return !!scope.querySelector("#" + hostId);
            }""",
            host,
        )
        if ready:
            page.wait_for_timeout(800)
            return True
        page.wait_for_timeout(500)
    return False


def vk_community_slug(url):
    match = re.search(r"@(public\d+)", url or "")
    return match.group(1) if match else ""


def vk_discover_url(context, want_slug=""):
    """Если vk_url не сохранён, берём вкладку кабинета с нужным @public… (не чужой канал)."""
    slug = (want_slug or "").strip()
    for page in context.pages:
        link = (page.url or "").strip()
        if "cabinet.vkvideo.ru/dashboard" not in link:
            continue
        if slug and slug not in link:
            continue
        return vk_normalize_url(link)
    return ""


def vk_normalize_url(url):
    """Кабинет VK Видео: открываем список клипов сообщества."""
    url = (url or "").strip()
    if "cabinet.vkvideo.ru" not in url:
        return url
    if "subsection=video_my_content_clips" in url:
        return url
    if "subsection=" in url:
        return re.sub(r"subsection=[^&]+", "subsection=video_my_content_clips", url)
    join = "&" if "?" in url else "?"
    extra = "section=video_my_content&" if "section=" not in url else ""
    return f"{url}{join}{extra}subsection=video_my_content_clips"


def vk_publish_scope(page):
    heading = page.get_by_role("heading", name=re.compile(r"Публикация клипа", re.I))
    if first_visible(heading) is None:
        heading = page.get_by_text(re.compile(r"Публикация клипа", re.I))
    dialog = page.locator("[role='dialog']").filter(has=heading)
    item = first_visible(dialog)
    if item is not None:
        return dialog.first
    if first_visible(heading):
        return page.locator("body")
    return page


def vk_open_clips_tab(page, site):
    tab = first_visible(page.get_by_role("tab", name=re.compile(r"^Клипы$", re.I)))
    if tab is None:
        return
    try:
        if tab.get_attribute("aria-selected") == "true":
            return
    except Exception:
        pass
    tab.click(timeout=5000)
    page.wait_for_timeout(900)


def vk_caption_field(scope, site):
    field = first_visible(scope.get_by_role("textbox", name=re.compile(r"^Описание$", re.I)))
    if field is not None:
        return field
    placeholder = re.compile(site.get("caption_placeholder") or r"О чём ваш клип", re.I)
    field = first_visible(scope.get_by_placeholder(placeholder))
    if field is not None:
        return field
    for selector in site.get("caption_selectors") or []:
        field = first_visible(scope.locator(selector))
        if field is not None:
            return field
    return first_visible(scope.get_by_role("textbox", name=re.compile(r"описание|description|клип", re.I)))


def vk_ensure_new_clip(page, site):
    if first_visible(page.get_by_role("button", name=re.compile(r"Выбрать файл", re.I))):
        return
    vk_open_clips_tab(page, site)
    if first_visible(page.get_by_role("button", name=re.compile(r"Выбрать файл", re.I))):
        return
    add_testid = site.get("add_video_testid") or "add_video_header_icon_button"
    add = first_visible(page.locator(f"[data-testid='{add_testid}']"))
    if add is None:
        add_pat = re.compile(site.get("add_menu_button") or r"^Добавить$", re.I)
        add = first_visible(page.get_by_role("button", name=add_pat))
    if add is None:
        raise StopSite("open", "кнопку «Добавить» не нашёл — открой кабинет VK Видео → Мой контент → Клипы")
    try:
        expanded = add.get_attribute("aria-expanded")
    except Exception:
        expanded = None
    if expanded != "true":
        add.click(force=True, timeout=5000)
        page.wait_for_timeout(800)
    inline_create = first_visible(page.get_by_role("button", name=re.compile(r"^Создать клип$", re.I)))
    if inline_create is not None and first_visible(page.get_by_role("button", name=re.compile(r"Выбрать файл", re.I))) is None:
        inline_create.click(force=True, timeout=5000)
        page.wait_for_timeout(900)
        if first_visible(page.get_by_role("button", name=re.compile(r"Выбрать файл", re.I))):
            return
    create_testid = site.get("create_clip_testid") or "append_video_btn"
    create_pat = re.compile(site.get("create_clip_button") or r"^Создать клип$", re.I)
    create = first_visible(page.locator(f"[data-testid='{create_testid}']"))
    if create is None:
        create = first_visible(page.get_by_role("button", name=create_pat))
    if create is None:
        create = first_visible(page.get_by_text(create_pat))
    if create is None:
        raise StopSite("open", "в меню «Добавить» нет «Создать клип» — обнови страницу кабинета")
    create.click(force=True, timeout=5000)
    page.wait_for_timeout(900)
    if first_visible(page.get_by_role("button", name=re.compile(r"Выбрать файл", re.I))):
        return
    raise StopSite("open", "после «Создать клип» не открылось окно «Выбрать файл»")


def vk_select_new_clip_author(page, site):
    """В «Новый клип» VK помнит последний канал — переключаем на нужное сообщество."""
    want = (site.get("vk_channel_name") or "").strip()
    if not want:
        raise StopSite(
            "channel",
            "укажи vk_channel_name в панели (имя канала, как в модалке, например SINTEZ.SPACE)",
        )
    picker = first_visible(page.locator("[data-testid='video_upload_modal_autor_picker_input']"))
    if picker is None:
        testid = site.get("upload_author_picker_testid") or "video_upload_modal_autor_picker"
        picker = first_visible(page.locator(f"[data-testid='{testid}']"))
    if picker is None:
        dialog = page.get_by_role("dialog", name=re.compile(r"Новый клип", re.I))
        picker = first_visible(
            dialog.locator("button").filter(has_text=re.compile(r"подписчик", re.I))
        )
    if picker is None:
        say("выбор канала в модалке не нашёл — пропускаю")
        return
    current = norm(picker.inner_text())
    if want.lower() in current.lower() and len(current) < 160:
        say(f"канал VK в модалке: {want}")
        return
    picker.click(force=True, timeout=5000)
    page.wait_for_timeout(900)
    channel_row = re.compile(rf"{re.escape(want)}.*подписчик.*видео", re.I)
    option = first_visible(page.get_by_role("button", name=channel_row))
    if option is None:
        loc = page.get_by_role("button")
        try:
            total = min(loc.count(), 60)
        except Exception:
            total = 0
        for index in range(total):
            item = loc.nth(index)
            try:
                if not item.is_visible():
                    continue
                text = norm(item.inner_text())
            except Exception:
                continue
            if want.lower() not in text.lower() or "подписчик" not in text.lower():
                continue
            if "видео" not in text.lower():
                continue
            if "выбрать файл" in text.lower():
                continue
            option = item
            break
    if option is None:
        raise StopSite("channel", f"в списке авторов нет {want!r}, сейчас в модалке: {current!r}")
    option.click(force=True, timeout=5000)
    page.wait_for_timeout(500)
    picker = (
        first_visible(page.locator("[data-testid='video_upload_modal_autor_picker_input']"))
        or first_visible(page.locator("[data-testid='video_upload_modal_autor_picker']"))
        or picker
    )
    got = norm(picker.inner_text())
    if want.lower() not in got.lower():
        raise StopSite("channel", f"канал не переключился на {want!r}, в модалке: {got!r}")
    say(f"канал VK в модалке: {want}")


def vk_upload_video(page, site, video):
    path = str(video_for_form(video))
    selector = site.get("file_input") or "input[type='file']"
    field = first_visible(page.locator(selector))
    if field is not None:
        field.set_input_files(path, timeout=180000)
        return
    button = first_visible(page.get_by_role("button", name=re.compile(r"Выбрать файл", re.I)))
    if button is None:
        raise StopSite("file", "нет input[type=file] и кнопки «Выбрать файл»")
    with page.expect_file_chooser(timeout=20000) as chooser_info:
        button.click(timeout=5000)
    chooser_info.value.set_files(path)


def vk_peel_stale_popout(page):
    """Снимает зависший оверлей «Новый клип», когда уже открыта «Публикация клипа»."""
    page.evaluate(
        """() => {
            const hasPublish = document.body.innerText.includes('Публикация клипа');
            if (!hasPublish) return;
            for (const wrap of document.querySelectorAll('.vkuiPopoutWrapper__host.vkuiPopoutWrapper__opened')) {
                if (wrap.innerText.includes('Публикация клипа')) continue;
                if (wrap.innerText.includes('Новый клип') || wrap.querySelector('input[type=file]')) {
                    wrap.classList.remove('vkuiPopoutWrapper__opened', 'vkuiPopoutWrapper__masked');
                    const overlay = wrap.querySelector('.vkuiPopoutWrapper__overlay');
                    if (overlay) overlay.remove();
                }
            }
        }"""
    )


def vk_cover_file_input(scope):
    for locator in (
        scope.locator("input[type='file'][accept*='image' i]"),
        scope.get_by_label(re.compile(r"Выбрать обложку", re.I)),
    ):
        item = first_visible(locator)
        if item is not None:
            return item
    inputs = scope.locator("input[type='file']")
    try:
        count = inputs.count()
    except Exception:
        return None
    for index in range(count):
        item = inputs.nth(index)
        accept = (item.get_attribute("accept") or "").lower()
        if "video" in accept or "mp4" in accept:
            continue
        if not accept or "image" in accept or "jpg" in accept or "jpeg" in accept or "png" in accept or "webp" in accept:
            return item
    return None


def vk_wait_cover_ready(page, site):
    """Ждёт, пока VK включит «Выбрать обложку» после обработки клипа."""
    timeout_s = int(site.get("cover_wait_s") or site.get("upload_timeout_s") or 240)
    deadline = time.monotonic() + timeout_s
    ready_text = re.compile(site.get("cover_ready_text") or r"загружен и обработан|Клип загружен", re.I)
    cover_name = re.compile(site.get("cover_open") or r"Выбрать обложку", re.I)
    while time.monotonic() < deadline:
        vk_dismiss_abort_dialog(page)
        scope = vk_publish_scope(page)
        if first_visible(scope.get_by_text(re.compile(site.get("cover_wait_disabled_text") or r"после обработки", re.I))):
            page.wait_for_timeout(900)
            continue
        processed = first_visible(scope.get_by_text(ready_text)) is not None
        field = vk_cover_file_input(scope)
        if field is not None and processed:
            try:
                if field.is_enabled():
                    return scope
            except Exception:
                return scope
        button = first_visible(scope.get_by_role("button", name=cover_name))
        if button is not None and processed:
            try:
                if not button.is_disabled():
                    return scope
            except Exception:
                return scope
        page.wait_for_timeout(700)
    return None


def vk_set_cover(page, site, video):
    cover = find_cover(video)
    if not cover:
        return
    scope = vk_wait_cover_ready(page, site)
    if scope is None:
        raise StopSite("cover", "обложка недоступна — клип не успел обработаться")
    field = vk_cover_file_input(scope)
    if field is not None:
        field.set_input_files(str(cover), timeout=60000)
        page.wait_for_timeout(1200)
        say(f"обложка VK: {cover.name}")
        return
    button = first_visible(
        scope.get_by_role("button", name=re.compile(site.get("cover_open") or r"Выбрать обложку", re.I))
    )
    if button is None:
        raise StopSite("cover", "нет поля файла и кнопки «Выбрать обложку»")
    with page.expect_file_chooser(timeout=15000) as picked:
        button.click(timeout=8000)
    picked.value.set_files(str(cover))
    page.wait_for_timeout(1200)
    say(f"обложка VK: {cover.name}")


def vk_fill_description(page, scope, site, text):
    vk_peel_stale_popout(page)
    field = vk_caption_field(scope, site)
    if field is None:
        testid = site.get("description_testid") or "clips-upload-description"
        field = first_visible(page.locator(f"[data-testid='{testid}']"))
    if field is None:
        raise StopSite("caption", "поле описания не найдено")
    try:
        field.fill(text, force=True, timeout=15000)
    except Exception:
        field.evaluate(
            """(el, t) => {
                el.value = t;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            text,
        )
    got = field.input_value(timeout=3000)
    if not texts_match(text, got):
        raise StopSite("caption", f"описание не совпало: в поле {(got or '')[:160]!r}")
    return got


def vk_wait_publish_form(page, site):
    timeout_s = int(site.get("upload_timeout_s") or 180)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        vk_dismiss_abort_dialog(page)
        scope = vk_publish_scope(page)
        field = vk_caption_field(scope, site)
        if field is not None:
            vk_peel_stale_popout(page)
            page.wait_for_timeout(400)
            return scope, field
        page.wait_for_timeout(500)
    raise StopSite("upload", "форма «Публикация клипа» и поле описания не появились")


def vk_open_schedule_picker(scope, page, site):
    vk_peel_stale_popout(page)
    testid = site.get("schedule_testid") or "clips-upload-publish-date"
    button = first_visible(scope.locator(f"[data-testid='{testid}']"))
    if button is None:
        pattern = re.compile(site.get("schedule_open") or r"Когда опубликовать", re.I)
        button = first_visible(scope.get_by_role("button", name=pattern))
    if button is None:
        button = first_visible(scope.get_by_text(re.compile(site.get("schedule_open") or r"Когда опубликовать", re.I)))
    if button is None:
        raise StopSite("schedule", "кнопку «Когда опубликовать» не нашёл")
    button.scroll_into_view_if_needed(timeout=3000)
    try:
        button.click(force=True, timeout=5000)
    except Exception:
        button.evaluate("el => el.click()")
    page.wait_for_timeout(900)


def vk_schedule_inputs(scope, site):
    time_field = find_when_field(
        scope,
        site.get("time_names") or r"время|time",
        site.get("time_selectors") or [],
    )
    date_field = find_when_field(
        scope,
        site.get("date_names") or r"дата|date",
        site.get("date_selectors") or [],
    )
    if time_field is not None or date_field is not None:
        return time_field, date_field
    loc = scope.locator("input[type='date'], input[type='time'], input[type='datetime-local']")
    try:
        count = min(loc.count(), 8)
    except Exception:
        return None, None
    dt_local = None
    date_field = None
    time_field = None
    for index in range(count):
        item = loc.nth(index)
        try:
            if not item.is_visible():
                continue
            kind = (item.get_attribute("type") or "").lower()
        except Exception:
            continue
        if kind == "datetime-local" and dt_local is None:
            dt_local = item
        elif kind == "date" and date_field is None:
            date_field = item
        elif kind == "time" and time_field is None:
            time_field = item
    if dt_local is not None:
        return dt_local, dt_local
    return time_field, date_field


VK_MONTHS_GEN = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
VK_MONTHS_NOM = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def vk_calendar_open(page):
    return first_visible(page.get_by_role("grid", name=re.compile(r"\d{4}", re.I))) is not None


def vk_calendar_pick_day(page, when):
    month_combo = first_visible(page.get_by_role("combobox", name=re.compile(r"Изменить месяц", re.I)))
    year_combo = first_visible(page.get_by_role("combobox", name=re.compile(r"Изменить год", re.I)))
    want_month = VK_MONTHS_NOM[when.month - 1]
    want_year = str(when.year)
    if month_combo is not None:
        try:
            if want_month not in (month_combo.inner_text() or "").lower():
                month_combo.click(force=True)
                page.wait_for_timeout(200)
                opt = first_visible(page.get_by_role("option", name=re.compile(want_month, re.I)))
                if opt is not None:
                    opt.click(force=True)
                    page.wait_for_timeout(300)
        except Exception:
            pass
    if year_combo is not None:
        try:
            if want_year not in (year_combo.inner_text() or ""):
                year_combo.click(force=True)
                page.wait_for_timeout(200)
                opt = first_visible(page.get_by_role("option", name=re.compile(rf"^{want_year}$")))
                if opt is not None:
                    opt.click(force=True)
                    page.wait_for_timeout(300)
        except Exception:
            pass
    day_label = f"{when.day} {VK_MONTHS_GEN[when.month - 1]}"
    cell = first_visible(page.get_by_role("gridcell", name=re.compile(re.escape(day_label), re.I)))
    if cell is None:
        cell = first_visible(page.get_by_role("gridcell", name=re.compile(rf",\s*{when.day}\s+", re.I)))
    if cell is None:
        raise StopSite("schedule", f"в календаре VK нет ячейки {day_label}")
    cell.click(force=True, timeout=3000)
    page.wait_for_timeout(300)


def vk_calendar_set_time(page, when):
    hour_field = first_visible(page.get_by_role("textbox", name=re.compile(r"Изменить час", re.I)))
    minute_field = first_visible(page.get_by_role("textbox", name=re.compile(r"Изменить минуту", re.I)))
    if hour_field is None or minute_field is None:
        raise StopSite("schedule", "не вижу поля «Изменить час/минуту» в календаре VK")
    want_hour = f"{when.hour:02d}"
    want_minute = f"{when.minute:02d}"
    for field, value in ((hour_field, want_hour), (minute_field, want_minute)):
        field.click(force=True)
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(value)
        page.keyboard.press("Tab")
        page.wait_for_timeout(150)
    got_h = hour_field.input_value(timeout=1000)
    got_m = minute_field.input_value(timeout=1000)
    if got_h != want_hour or got_m != want_minute:
        raise StopSite("schedule", f"время в календаре {got_h}:{got_m}, нужно {want_hour}:{want_minute}")


def vk_calendar_close(page, scope):
    heading = first_visible(scope.get_by_role("heading", name=re.compile(r"Публикация клипа", re.I)))
    if heading is not None:
        heading.click(force=True)
    else:
        page.keyboard.press("Escape")
    page.wait_for_timeout(400)


def vk_schedule_chip_text(scope, site):
    testid = site.get("schedule_testid") or "clips-upload-publish-date"
    chip = first_visible(scope.locator(f"[data-testid='{testid}']"))
    if chip is None:
        return ""
    try:
        return norm(chip.inner_text())
    except Exception:
        return ""


def vk_set_schedule(page, scope, site, when):
    when = snap_five(when)
    vk_open_schedule_picker(scope, page, site)
    want_date = when.strftime("%Y-%m-%d")
    want_time = f"{when.hour:02d}:{when.minute:02d}"
    if vk_calendar_open(page):
        vk_calendar_pick_day(page, when)
        vk_calendar_set_time(page, when)
        vk_calendar_close(page, scope)
        chip = vk_schedule_chip_text(scope, site)
        say(f"расписание VK (календарь): {chip or want_time}")
        if str(when.hour) not in chip and want_time not in chip:
            raise StopSite("schedule", f"в «Когда опубликовать» нет {want_time}, сейчас: {chip!r}")
        return
    time_field, date_field = vk_schedule_inputs(scope, site)
    if time_field is not None and date_field is time_field:
        value = when.strftime("%Y-%m-%dT%H:%M")
        got = commit_value(page, time_field, [value])
        if want_date not in got or want_time not in got:
            raise StopSite("schedule", f"datetime-local не принял {value}, в поле {got!r}")
        say(f"расписание VK: {want_date} {want_time}")
        page.keyboard.press("Escape")
        return
    if time_field is None and date_field is None:
        raise StopSite("schedule", f"календарь VK не открылся: {describe_fields(scope)}")
    if date_field is not None and date_field.input_value(timeout=1000) != want_date:
        date_field.click(timeout=2000)
        if not pick_calendar_day(scope, when):
            page.keyboard.press("Escape")
            raise StopSite("schedule", f"в календаре не выбрал {want_date}")
        page.keyboard.press("Escape")
    if time_field is not None:
        got_time = time_field.input_value(timeout=1000)
        if got_time != want_time:
            for attempt in range(1, 4):
                say(f"VK время {want_time}, попытка {attempt}")
                try:
                    time_field.click(timeout=2000)
                except Exception as exc:
                    say(f"поле времени не нажалось: {exc}")
                    continue
                page.keyboard.press("Control+A")
                page.keyboard.insert_text(want_time)
                page.keyboard.press("Escape")
                got_time = wait_value(time_field, want_time, seconds=2)
                if got_time == want_time:
                    break
            else:
                raise StopSite("schedule", f"в поле времени так и не {want_time}, сейчас {got_time or 'пусто'}")
    got_date = date_field.input_value(timeout=1000) if date_field is not None else want_date
    got_time = time_field.input_value(timeout=1000) if time_field is not None else want_time
    say(f"расписание VK в форме: {got_date} {got_time}")
    if got_date != want_date or got_time != want_time:
        raise StopSite("schedule", f"ожидал {want_date} {want_time}, в форме {got_date} {got_time}")


def vk_wait_schedule_button(page, site, when=None):
    if when is not None:
        pattern = re.compile(
            site.get("schedule_submit")
            or r"Запланировать публикацию|Запланировать|Schedule",
            re.I,
        )
    else:
        pattern = re.compile(site.get("publish_submit") or r"^Опубликовать$", re.I)
    timeout_s = int(site.get("upload_timeout_s") or 180)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        accept_popups(page, site)
        scope = vk_publish_scope(page)
        button = first_visible(scope.get_by_role("button", name=pattern))
        if button is None:
            button = first_visible(page.get_by_role("button", name=pattern))
        if button is not None:
            try:
                if button.is_enabled():
                    return button
            except Exception:
                pass
        page.wait_for_timeout(600)
    raise StopSite("schedule", "кнопка «Запланировать публикацию» не активировалась (обработка клипа?)")


def vk_press_schedule(page, site, scheduled=True):
    button = vk_wait_schedule_button(page, site, when=scheduled or None)
    button.scroll_into_view_if_needed(timeout=3000)
    button.click(timeout=8000)
    say("нажал «Запланировать публикацию» (VK)")
    page.wait_for_timeout(1200)


def fill_vk(page, site, post, caption, when):
    global _popup_site
    _popup_site = site
    video = post["video"]
    text = (caption or "")[: int(site.get("char_limit") or 4000)]
    step = "open"
    try:
        url = vk_normalize_url(site["url"])
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        try:
            page.locator("[data-testid='add_video_header_icon_button']").wait_for(state="visible", timeout=20000)
        except Exception:
            pass
        reason = blocked_reason(page, site)
        if reason:
            raise StopSite("login", reason, verification=True)
        vk_dismiss_abort_dialog(page)
        accept_popups(page, site)
        step = "new_clip"
        vk_ensure_new_clip(page, site)
        step = "channel"
        vk_select_new_clip_author(page, site)
        step = "file"
        vk_upload_video(page, site, video)
        step = "upload"
        scope, editor = vk_wait_publish_form(page, site)
        say("форма:", page.url)
        step = "caption"
        put = vk_fill_description(page, scope, site, text)
        say(f"описание ({len(norm(put))} симв.)")
        if find_cover(video):
            step = "cover"
            vk_peel_stale_popout(page)
            vk_set_cover(page, site, video)
        if when:
            step = "schedule"
            vk_dismiss_abort_dialog(page)
            vk_peel_stale_popout(page)
            scope = vk_publish_scope(page)
            vk_set_schedule(page, scope, site, when)
            vk_press_schedule(page, site, scheduled=True)
            say("отложка VK отправлена, вкладка остаётся открытой")
        else:
            say("время не задано — «Запланировать публикацию» не нажимал")
    except StopSite:
        raise
    except Exception as exc:
        raise StopSite(step, str(exc)) from exc


def fill_youtube(page, site, post, caption, when):
    global _popup_site
    _popup_site = site
    video = post["video"]
    title = (post.get("youtube_title") or "")[: int(site.get("title_limit") or 100)]
    description = (caption or post.get("youtube_description") or "")[: int(site.get("char_limit") or 5000)]
    step = "open"
    try:
        page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        reason = blocked_reason(page, site)
        if reason:
            raise StopSite("login", reason, verification=True)
        accept_popups(page, site)
        step = "file"
        scope, field = wait_upload_form(page, site)
        say("форма:", page.url)
        field.set_input_files(str(video_for_form(video)), timeout=180000)
        step = "upload"
        if not wait_youtube_upload(page, site):
            raise StopSite("upload", "форма названия не появилась после выбора файла")
        youtube_suppress_reload_prompt(page)
        step = "title"
        youtube_apply_text(page, site, "title", title)
        say(f"название ({len(title)} симв.)")
        step = "description"
        page.wait_for_timeout(700)
        youtube_apply_text(page, site, "description", description)
        say(f"описание ({len(norm(description))} симв.)")
        step = "audience"
        youtube_set_not_for_kids(page, site)
        if when:
            step = "visibility"
            if not youtube_advance_to_visibility(page, site):
                raise StopSite("visibility", "не дошёл до «Доступ» — проверь «Далее» и аудиторию")
            step = "schedule"
            youtube_set_not_for_kids(page, site)
            youtube_set_schedule(page, site, when)
            youtube_press_schedule(page, site, when)
            say("отложка YouTube отправлена, вкладка остаётся открытой")
        else:
            say("время не задано — Publish не нажимал")
    except StopSite:
        raise
    except Exception as exc:
        raise StopSite(step, str(exc)) from exc


def fill_tiktok(page, site, post, caption, when):
    global _popup_site
    _popup_site = site
    video = post["video"]
    step = "open"
    try:
        page.goto(site["url"], wait_until="domcontentloaded", timeout=45000)
        step = "file"
        scope, field = wait_upload_form(page, site)
        say("форма:", scope.url if hasattr(scope, "url") else page.url)
        accept_popups(scope, site)
        field.set_input_files(str(video_for_form(video)), timeout=120000)
        step = "caption"
        editor = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                editor = pick_editor(scope, site)
                break
            except StopSite:
                page.wait_for_timeout(300)
        if editor is None:
            raise StopSite("caption", "поле описания не появилось после выбора файла")
        put = insert_caption(page, editor, caption)
        say(f"текст в поле ({len(norm(put))} симв.)")
        if wait_upload_hint(scope, site):
            say("загрузка файла на странице отметилась готовой")
        else:
            say("метку окончания загрузки не увидел, форму всё равно заполнил")
        accept_popups(scope, site)
        page.set_default_timeout(5000)
        issues = []
        steps = [
            ("cover", lambda: set_cover(page, scope, site, video)),
            ("location", lambda: set_location(page, scope, site)),
            ("schedule", lambda: set_schedule(page, scope, site, when) if when else say("время не задано — расписание не трогаю")),
        ]
        for step_name, action in steps:
            step = step_name
            done = False
            for attempt in range(1, 3):
                started = time.monotonic()
                try:
                    accept_popups(scope, site)
                    action()
                    say(f"{step_name}: готово за {time.monotonic() - started:.1f} с")
                    done = True
                    break
                except StopSite as exc:
                    say(f"{step_name}: попытка {attempt} не вышла за {time.monotonic() - started:.1f} с: {exc}")
                    page.keyboard.press("Escape")
            if not done:
                issues.append(step_name)
        if issues:
            raise StopSite(step, "не вышло: " + ", ".join(issues))
        if when:
            step = "schedule"
            press_schedule(page, scope, site, when)
        say("отложка отправлена, вкладка остаётся открытой")
    except StopSite:
        raise
    except Exception as exc:
        raise StopSite(step, str(exc)) from exc


def click_named(scope, pattern, timeout=3000):
    """Жмёт кнопку, ссылку или подпись. Кнопки Share и Post сюда не попадают: у них другие подписи."""
    compiled = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    for role in ("button", "link", "menuitem"):
        item = first_visible(scope.get_by_role(role, name=compiled))
        if item is not None:
            item.click(timeout=timeout)
            return True
    item = first_visible(scope.get_by_label(compiled))
    if item is not None:
        item.click(timeout=timeout)
        return True
    return False


def instagram_step_attempts(site):
    return max(3, int(site.get("step_attempts") or 10))


def cancel_discard_post(page):
    """Escape на IG открывает «Discard post?» — только Cancel."""
    heading = first_visible(page.get_by_role("heading", name=re.compile(r"Discard post|Отменить публика|Удалить публика", re.I)))
    if heading is None:
        body = page_text(page)
        if not re.search(r"discard post|отменить публика|удалить публика", body, re.I):
            return False
    cancel = first_visible(page.get_by_role("button", name=re.compile(r"^Cancel$|^Отмена$", re.I)))
    if cancel is None:
        return False
    cancel.click(timeout=3000)
    say("закрыл «Discard post» — Cancel")
    page.wait_for_timeout(400)
    return True


def cancel_sharing_dialog(page):
    heading = first_visible(page.get_by_role("heading", name=re.compile(r"^Sharing$|^Публикация$", re.I)))
    if heading is None:
        return False
    for pattern in (r"^Back$|^Назад$", r"^Close$|^Закрыть$", r"^Cancel$|^Отмена$"):
        button = first_visible(page.get_by_role("button", name=re.compile(pattern, re.I)))
        if button is not None:
            button.click(force=True, timeout=3000)
            say("закрыл диалог Sharing")
            page.wait_for_timeout(500)
            return True
    return False


def heal_instagram(page, site):
    cancel_sharing_dialog(page)
    cancel_discard_post(page)
    accept_popups(page, site)


def dismiss_stray(page, site):
    url = (site or {}).get("url") or ""
    if "instagram.com" in url:
        heal_instagram(page, site)
        return
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    accept_popups(page, site)


def instagram_caption_ready(scope, site):
    try:
        pick_editor(scope, site)
        return True
    except StopSite:
        return False


def instagram_on_crop(scope):
    dialog = first_visible(scope.get_by_role("dialog", name=re.compile(r"^Crop$|Обрез", re.I)))
    if dialog is not None:
        return True
    return first_visible(scope.get_by_role("heading", name=re.compile(r"^Crop$|Обрез", re.I))) is not None


def instagram_on_edit(scope):
    if first_visible(scope.get_by_role("heading", name=re.compile(r"^Edit$|Редакт", re.I))):
        return True
    if first_visible(scope.get_by_text(re.compile(r"Cover photo|Обложка|Select from computer|Выбрать с компьютера", re.I))):
        return True
    return False


def instagram_stage(scope, site):
    if instagram_caption_ready(scope, site):
        return "caption"
    if instagram_on_crop(scope):
        return "crop"
    if instagram_on_edit(scope):
        return "edit"
    if cover_opener(scope, site) is not None:
        return "cover"
    return "unknown"


def instagram_composer_open(page):
    dialogs = page.locator("[role=dialog]")
    try:
        count = min(dialogs.count(), 6)
    except Exception:
        return False
    marker = re.compile(
        r"create new post|новая публика|crop|обрез|\bedit\b|редакт|cover photo|обложк|add a caption|"
        r"добавьте подпись|schedule content|запланировать контент|add collaborators|tag people",
        re.I,
    )
    for index in range(count):
        dialog = dialogs.nth(index)
        try:
            if not dialog.is_visible():
                continue
            label = dialog.get_attribute("aria-label") or ""
            if marker.search(label):
                return True
            text = dialog.inner_text(timeout=1500)
        except Exception:
            return True
        if marker.search(text):
            return True
    return False


def wait_instagram_composer_closed(page, site, timeout_s=None):
    timeout_s = int(timeout_s or site.get("compose_close_timeout_s") or 45)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        accept_popups(page, site)
        if not instagram_composer_open(page):
            say("окно постинга закрылось")
            return True
        page.wait_for_timeout(500)
    return False


def retry_instagram(page, site, step, work, check, attempts=None, pause_ms=650):
    """Повторяет шаг, пока check() не станет True или не кончатся попытки."""
    attempts = attempts or instagram_step_attempts(site)
    last = ""
    for attempt in range(1, attempts + 1):
        accept_popups(page, site)
        started = time.monotonic()
        try:
            work()
        except StopSite as exc:
            last = str(exc)
            say(f"{step}: попытка {attempt} за {time.monotonic() - started:.1f} с: {exc}")
            dismiss_stray(page, site)
            page.wait_for_timeout(pause_ms)
            continue
        except Exception as exc:
            last = str(exc)
            say(f"{step}: попытка {attempt} за {time.monotonic() - started:.1f} с: {exc}")
            dismiss_stray(page, site)
            page.wait_for_timeout(pause_ms)
            continue
        if check():
            say(f"{step}: готово с попытки {attempt} за {time.monotonic() - started:.1f} с")
            return
        last = "проверка после шага не прошла"
        say(f"{step}: попытка {attempt}: {last}")
        dismiss_stray(page, site)
        page.wait_for_timeout(pause_ms)
    raise StopSite(step, f"за {attempts} попыток не вышло: {last}")


def set_instagram_edit_cover(page, scope, site, cover_path):
    """Экран Edit: «Select from computer» без системного «Открыть», если есть input."""
    upload_pat = re.compile(
        site.get("cover_upload_link") or r"Select from computer|Выбрать с компьютера|Загрузить с компьютера",
        re.I,
    )
    field = image_file_input(page)
    if field is not None:
        field.set_input_files(str(cover_path), timeout=60000)
        say(f"обложка: {cover_path.name}")
        page.wait_for_timeout(900)
        return True
    link = first_visible(scope.get_by_text(upload_pat))
    if link is None:
        link = first_visible(scope.get_by_role("button", name=upload_pat))
    if link is None:
        link = cover_opener(scope, site)
    if link is None:
        return False
    try:
        with page.expect_file_chooser(timeout=8000) as picked:
            link.click(timeout=3000)
        picked.value.set_files(str(cover_path))
    except Exception:
        field = image_file_input(page)
        if field is None:
            return False
        field.set_input_files(str(cover_path), timeout=60000)
    say(f"обложка: {cover_path.name}")
    page.wait_for_timeout(900)
    return True


def instagram_progress_to_caption(page, site, video):
    """Crop → Edit (обложка) → подпись. Без Escape, с ретраями."""
    next_name = site.get("next_name") or r"^(Next|Далее)$"
    cover_path = find_cover(video)
    cover_done = cover_path is None
    attempts = max(instagram_step_attempts(site), int(site.get("cover_attempts") or 8))
    last = ""
    for attempt in range(1, attempts + 1):
        heal_instagram(page, site)
        scope = composer_scope(page)
        stage = instagram_stage(scope, site)
        say(f"instagram: {stage}, попытка {attempt}/{attempts}")
        if stage == "caption":
            return pick_editor(scope, site)
        if stage == "crop":
            if not click_named(scope, next_name, timeout=4000):
                last = "на Crop нет Next"
            else:
                page.wait_for_timeout(900)
            continue
        if stage == "edit":
            if cover_path and not cover_done:
                if set_instagram_edit_cover(page, scope, site, cover_path):
                    cover_done = True
                    continue
                say("cover: на Edit не вышло, жму Next без обложки")
                cover_done = True
            if click_named(scope, next_name, timeout=4000):
                page.wait_for_timeout(900)
                continue
            last = "на Edit нет Next"
            continue
        if stage == "cover" and cover_path and not cover_done:
            try:
                try_set_cover(page, scope, site, video)
                cover_done = True
                continue
            except StopSite as exc:
                last = str(exc)
                say(f"cover: {exc}")
                cover_done = True
                continue
        if click_named(scope, next_name, timeout=3000):
            page.wait_for_timeout(900)
            continue
        last = "не вижу следующий шаг"
        page.wait_for_timeout(600)
    raise StopSite("caption", f"не дошёл до подписи: {last}")


def open_instagram_create(page, site):
    """New post, затем пункт Post. Ссылку Reels в меню слева не трогаем."""
    if not click_named(page, site["create_name"]):
        return False
    page.wait_for_timeout(500)
    post = first_visible(page.get_by_role("link", name=re.compile(site.get("post_menu") or r"^Post$", re.I)))
    if post is not None:
        post.click(timeout=3000)
        page.wait_for_timeout(500)
    return True


def composer_scope(page):
    cancel_discard_post(page)
    close_ig_helper_dialog(page)
    main = instagram_main_dialog(page)
    if main is not None:
        return main
    dialogs = page.locator("[role=dialog]")
    last = None
    try:
        count = dialogs.count()
    except Exception:
        count = 0
    for index in range(count):
        item = dialogs.nth(index)
        try:
            if item.is_visible():
                last = item
        except Exception:
            continue
    return last or page


def clock_12(when):
    suffix = "AM" if when.hour < 12 else "PM"
    hour = when.hour % 12 or 12
    return hour, when.minute, suffix


def date_matches(text, when):
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    low = (text or "").lower()
    return months[when.month - 1] in low and re.search(rf"\b{when.day}\b", low) is not None and str(when.year) in low


def schedule_switch(scope):
    switches = scope.locator("input[role='switch']")
    try:
        count = min(switches.count(), 8)
    except Exception:
        return None
    for index in range(count):
        item = switches.nth(index)
        try:
            label = item.evaluate(
                """el => {
                    let node = el;
                    for (let i = 0; i < 6 && node; i++) {
                        const text = (node.innerText || "").replace(/\\s+/g, " ").trim();
                        if (/schedule content|запланировать контент/i.test(text) && text.length < 80) return text;
                        node = node.parentElement;
                    }
                    return "";
                }"""
            )
        except Exception:
            continue
        if label:
            return item
    return None


def set_spin(page, name, text):
    box = page.get_by_role("spinbutton", name=re.compile(name, re.I)).first
    box.click(timeout=2000)
    page.keyboard.press("Control+A")
    page.keyboard.type(str(text), delay=20)
    page.keyboard.press("Tab")


def spin_now(page, name):
    box = page.get_by_role("spinbutton", name=re.compile(name, re.I)).first
    return box.get_attribute("aria-valuenow"), (box.get_attribute("aria-valuetext") or "")


def instagram_collaborator_field(scope, site):
    selector = site.get("collaborators_input") or "input[name='creation-collaborator-input']"
    field = first_visible(scope.locator(selector))
    if field is not None:
        return field
    names = re.compile(site.get("collaborators_field") or r"Add collaborators|Добавить соавтор", re.I)
    field = first_visible(scope.get_by_role("textbox", name=names))
    if field is not None:
        return field
    return first_visible(scope.get_by_placeholder(names))


def dismiss_ig_hashtag_overlay(page, scope, site):
    """Подсказки хэштегов перекрывают поля — клик в подпись, не в коллабораторов (там второй диалог)."""
    close_ig_helper_dialog(page)
    scope = instagram_main_dialog(page) or scope
    try:
        editor = pick_editor(scope, site)
        editor.click(force=True, timeout=3000)
        page.keyboard.press("End")
        page.wait_for_timeout(250)
        return
    except StopSite:
        pass
    anchor = first_visible(scope.get_by_text(re.compile(r"Schedule content|Запланировать контент", re.I)))
    if anchor is not None:
        anchor.click(force=True, timeout=2000)
        page.wait_for_timeout(300)


def close_ig_helper_dialog(page):
    dialogs = page.locator("[role=dialog]")
    try:
        count = dialogs.count()
    except Exception:
        return
    if count < 2:
        return
    for index in range(count):
        dialog = dialogs.nth(index)
        try:
            if not dialog.is_visible():
                continue
            text = dialog.inner_text(timeout=1000)
        except Exception:
            continue
        if re.search(r"if they accept|если .* примет|username will be added", text, re.I):
            close = first_visible(dialog.get_by_role("button", name=re.compile(r"^Close$|^Закрыть$", re.I)))
            if close is not None:
                close.click(force=True, timeout=2000)
                page.wait_for_timeout(300)
                return
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            cancel_discard_post(page)
            return


def instagram_main_dialog(page):
    dialogs = page.locator("[role=dialog]")
    try:
        count = dialogs.count()
    except Exception:
        return None
    fallback = None
    for index in range(count):
        item = dialogs.nth(index)
        try:
            if not item.is_visible():
                continue
        except Exception:
            continue
        fallback = item
        label = (item.get_attribute("aria-label") or "").lower()
        if "create new post" in label or "новая публика" in label:
            return item
        try:
            if item.get_by_role("textbox", name=re.compile(r"caption|подпис", re.I)).count() > 0:
                return item
        except Exception:
            pass
    return fallback


def dialog_people_buttons(page):
    rows = []
    dialogs = page.locator("[role=dialog]")
    try:
        count = dialogs.count()
    except Exception:
        return rows
    for index in range(count):
        dialog = dialogs.nth(index)
        try:
            if not dialog.is_visible():
                continue
        except Exception:
            continue
        buttons = dialog.get_by_role("button")
        try:
            total = min(buttons.count(), 20)
        except Exception:
            continue
        for button_index in range(total):
            button = buttons.nth(button_index)
            try:
                if not button.is_visible():
                    continue
                label = norm(button.inner_text())
            except Exception:
                continue
            if not label or label.lower() in {"done", "готово", "cancel", "отмена", "close", "закрыть"}:
                continue
            if "profile picture" in label.lower() or re.search(r"\b[a-z0-9._]{3,}\b", label, re.I):
                rows.append((button, label))
    return rows


def select_people_row(button):
    box = first_visible(button.locator("[role=checkbox], input[type=checkbox]"))
    if box is not None:
        try:
            if not box.is_checked():
                box.click(force=True, timeout=3000)
            return
        except Exception:
            pass
    button.click(force=True, timeout=3000)


def pick_instagram_suggestion(page, user, step):
    token = user.lower().lstrip("@")
    deadline = time.monotonic() + 10
    best = None
    while time.monotonic() < deadline:
        rows = dialog_people_buttons(page)
        if rows:
            break
        page.wait_for_timeout(250)
    else:
        raise StopSite(step, f"подсказки для «{user}» не появились")
    chosen = None
    for button, label in rows:
        compact = re.sub(r"\s+", "", label.lower())
        if token in compact or token in label.lower():
            chosen = (button, label)
            break
        if best is None and len(token) >= 4 and re.search(re.escape(token[:4]), compact):
            best = (button, label)
    if chosen is None:
        chosen = best or rows[0]
    if chosen is not best and best is not None and chosen == rows[0] and token not in rows[0][1].lower():
        say(f"{step}: точного «{user}» нет, взял «{chosen[1][:60]}»")
    select_people_row(chosen[0])
    page.wait_for_timeout(400)
    if step != "reel_tags":
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
    return True


def confirm_people_picker(page, site):
    done_pat = re.compile(site.get("people_done") or r"^(Done|Готово)$", re.I)
    dialogs = page.locator("[role=dialog]")
    try:
        total = dialogs.count()
    except Exception:
        total = 0
    for index in range(total - 1, -1, -1):
        dialog = dialogs.nth(index)
        try:
            if not dialog.is_visible():
                continue
            text = dialog.inner_text(timeout=1200)
        except Exception:
            continue
        if re.search(r"^sharing$|discard post|запланировать контент|schedule content", text, re.I):
            continue
        if not re.search(r"profile picture|checkmark|checkbox", text, re.I):
            continue
        done = first_visible(dialog.get_by_role("button", name=done_pat))
        if done is not None:
            done.click(force=True, timeout=3000)
            page.wait_for_timeout(500)
            return
    done = first_visible(page.get_by_role("button", name=done_pat))
    if done is not None:
        done.click(force=True, timeout=3000)
        page.wait_for_timeout(400)


def type_in_people_field(page, field, user):
    try:
        field.focus()
    except Exception:
        pass
    field.click(force=True, timeout=3000)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.type(user, delay=35)
    page.wait_for_timeout(1200)


def invite_instagram_user(page, scope, field, site, user, step):
    type_in_people_field(page, field, user)
    heal_instagram(page, site)
    pick_instagram_suggestion(page, user, step)
    if step != "collaborators":
        confirm_people_picker(page, site)
    invite = first_visible(page.get_by_role("button", name=re.compile(r"^Invite$|^Пригласить$", re.I)))
    if invite is not None:
        invite.click(force=True, timeout=3000)
        page.wait_for_timeout(400)


def collaborators_picker_open(page, users):
    tokens = [u.lower().lstrip("@") for u in users]
    dialogs = page.locator("[role=dialog]")
    try:
        total = dialogs.count()
    except Exception:
        return None
    for index in range(total - 1, -1, -1):
        dialog = dialogs.nth(index)
        try:
            if not dialog.is_visible():
                continue
            text = dialog.inner_text(timeout=1200)
        except Exception:
            continue
        if not re.search(r"collaborator|соавтор", text, re.I):
            continue
        low = text.lower()
        if not all(token in low for token in tokens):
            continue
        return dialog
    return None


def collaborators_picker_satisfies(page, users):
    dialog = collaborators_picker_open(page, users)
    if dialog is None:
        return False
    try:
        boxes = dialog.locator("[role=checkbox], input[type=checkbox]")
        checked = 0
        for index in range(min(boxes.count(), 12)):
            box = boxes.nth(index)
            try:
                if box.is_checked():
                    checked += 1
            except Exception:
                continue
        return checked >= len(users)
    except Exception:
        return True


def finish_collaborators_picker(page, site):
    done_pat = re.compile(site.get("people_done") or r"^(Done|Готово)$", re.I)
    dialog = None
    dialogs = page.locator("[role=dialog]")
    try:
        total = dialogs.count()
    except Exception:
        total = 0
    for index in range(total - 1, -1, -1):
        item = dialogs.nth(index)
        try:
            if not item.is_visible():
                continue
            text = item.inner_text(timeout=1000)
        except Exception:
            continue
        if re.search(r"collaborator|соавтор|profile picture|checkmark|checkbox", text, re.I):
            dialog = item
            break
    if dialog is not None:
        done = first_visible(dialog.get_by_role("button", name=done_pat))
        if done is not None:
            done.click(force=True, timeout=3000)
            page.wait_for_timeout(600)
            return True
    done = first_visible(page.get_by_role("button", name=done_pat))
    if done is not None:
        done.click(force=True, timeout=3000)
        page.wait_for_timeout(500)
        return True
    return False


def instagram_user_linked(scope, user):
    token = user.lower().lstrip("@")
    link = scope.locator(f"a[href='/{token}/'], a[href*='/{token}/']")
    try:
        if link.count() > 0:
            return True
    except Exception:
        pass
    item = first_visible(scope.get_by_role("link", name=re.compile(rf"\b{re.escape(token)}\b", re.I)))
    if item is not None:
        return True
    item = first_visible(scope.get_by_text(re.compile(rf"^{re.escape(token)}$", re.I)))
    return item is not None


def collaborators_field_state(main, site):
    field = instagram_collaborator_field(main, site)
    if field is None:
        return 0, ""
    try:
        summary = (field.input_value() or "").strip()
    except Exception:
        return 0, ""
    match = re.search(r"^(\d+)\s+(people|person|человек|человека)", summary, re.I)
    if match:
        return int(match.group(1)), summary
    if summary:
        return 1, summary
    return 0, summary


def instagram_collaborator_added(main, site, user, expected_total=None):
    """Коллаборатор в блоке соавторов, не @-упоминание в подписи."""
    if expected_total:
        count, _ = collaborators_field_state(main, site)
        if count >= expected_total:
            return True
    token = user.lower().lstrip("@")
    try:
        editor = pick_editor(main, site)
    except StopSite:
        editor = None
    links = main.locator(f"a[href='/{token}/'], a[href='/{token}']")
    if editor is not None:
        links = links.filter(has_not=editor)
    if first_visible(links):
        return True
    _, summary = collaborators_field_state(main, site)
    if summary.lower() == token:
        return True
    return False


def collaborators_ok(page, scope, users, site=None):
    if not users:
        return True
    site = site or _popup_site
    main = instagram_main_dialog(page) or scope
    count, _ = collaborators_field_state(main, site)
    if count >= len(users):
        return True
    if collaborators_picker_satisfies(page, users):
        finish_collaborators_picker(page, site)
        count, _ = collaborators_field_state(main, site)
        return count >= len(users) or count > 0
    return False


def caption_mentions_ok(scope, site, users):
    try:
        editor = pick_editor(scope, site)
        text = (editor_text(editor) or "").lower()
    except StopSite:
        return False
    for user in users:
        token = user.lower().lstrip("@")
        if f"@{token}" in text:
            continue
        if instagram_user_linked(scope, user):
            continue
        return False
    return True


def pick_caption_mention(page, scope, user, step="reel_tags"):
    token = user.lower().lstrip("@")
    skip = re.compile(r"#|^\d+/|posts\b|emoji|chevron|schedule|share to|accessibility|advanced", re.I)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        rows = dialog_people_buttons(page)
        for button, label in rows:
            if token in re.sub(r"\s+", "", label.lower()) or token in label.lower():
                button.click(force=True, timeout=3000)
                page.wait_for_timeout(450)
                return
        for role in ("button", "option"):
            loc = scope.get_by_role(role)
            try:
                total = min(loc.count(), 24)
            except Exception:
                total = 0
            for index in range(total):
                item = loc.nth(index)
                try:
                    if not item.is_visible():
                        continue
                    label = norm(item.inner_text())
                except Exception:
                    continue
                if not label or skip.search(label):
                    continue
                compact = re.sub(r"\s+", "", label.lower())
                if token in compact or re.search(rf"\b{re.escape(token)}\b", label, re.I):
                    item.click(force=True, timeout=3000)
                    page.wait_for_timeout(450)
                    return
        page.wait_for_timeout(220)
    raise StopSite(step, f"подсказка @ для «{user}» в подписи не появилась")


def append_caption_mention(page, scope, site, editor, user, step="reel_tags"):
    token = user.lstrip("@")
    dismiss_ig_hashtag_overlay(page, scope, site)
    guard_click(editor)
    page.keyboard.press("End")
    page.keyboard.insert_text("\n")
    page.wait_for_timeout(200)
    page.keyboard.type(f"@{token}", delay=38)
    page.wait_for_timeout(900)
    try:
        pick_caption_mention(page, scope, user, step)
    except StopSite:
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    dismiss_ig_hashtag_overlay(page, scope, site)


def append_caption_mentions(page, site, users):
    scope = composer_scope(page)
    main = instagram_main_dialog(page) or scope
    dismiss_ig_hashtag_overlay(page, scope, site)
    editor = pick_editor(main, site)
    missing = []
    for user in users:
        token = user.lstrip("@")
        if caption_mentions_ok(main, site, [user]):
            continue
        missing.append(user)
    if not missing:
        return
    for user in missing:
        append_caption_mention(page, main, site, editor, user)
        say(f"reel_tags: упомянул в подписи @{user.lstrip('@')}")


def reel_tags_ok(page, scope, users, site=None):
    if not users:
        return True
    if cancel_sharing_dialog(page):
        return False
    if not instagram_composer_open(page):
        return False
    site = site or _popup_site
    main = instagram_main_dialog(page) or scope
    if not instagram_caption_ready(main, site):
        return False
    return caption_mentions_ok(main, site, users)


def add_instagram_collaborators(page, scope, site, usernames, step="collaborators"):
    """Один проход по списку, одно Done в конце — без повторного ввода на ретраях."""
    if not usernames:
        return
    if collaborators_ok(page, scope, usernames, site):
        say(f"{step}: уже в форме")
        return
    if collaborators_picker_satisfies(page, usernames):
        finish_collaborators_picker(page, site)
        say(f"{step}: подтвердил выбранных ({len(usernames)})")
        return
    dismiss_ig_hashtag_overlay(page, scope, site)
    main = instagram_main_dialog(page) or composer_scope(page)
    count, _ = collaborators_field_state(main, site)
    if count >= len(usernames):
        say(f"{step}: уже {count} в форме")
        return
    for user in usernames:
        field = instagram_collaborator_field(composer_scope(page), site)
        if field is None:
            raise StopSite(step, "поле Add collaborators не найдено")
        invite_instagram_user(page, composer_scope(page), field, site, user, step)
        page.wait_for_timeout(450)
        say(f"{step}: {user}")
    finish_collaborators_picker(page, site)
    heal_instagram(page, site)
    dismiss_ig_hashtag_overlay(page, composer_scope(page), site)
    say(f"{step}: готово ({len(usernames)})")


def instagram_tag_people_button(scope):
    button = first_visible(scope.get_by_role("button", name=re.compile(r"Tag people|Отметить людей", re.I)))
    if button is not None:
        return button
    return first_visible(scope.get_by_label(re.compile(r"^Tags$|Теги", re.I)))


def find_reel_tag_search(page, site):
    search_pat = re.compile(site.get("people_search") or r"Search|Поиск|Who|Кого", re.I)
    skip = re.compile(r"caption|collabor|location|подпис|локац|соавтор", re.I)
    roots = [page, instagram_main_dialog(page) or page]
    for root in roots:
        if root is None:
            continue
        field = first_visible(root.get_by_placeholder(search_pat))
        if field is not None:
            return field
        field = first_visible(root.get_by_role("textbox", name=search_pat))
        if field is not None:
            return field
        field = first_visible(root.get_by_role("combobox", name=search_pat))
        if field is not None:
            return field
    loc = page.locator("input[type='text'], [role=textbox]")
    try:
        total = min(loc.count(), 20)
    except Exception:
        total = 0
    for index in range(total):
        item = loc.nth(index)
        try:
            if not item.is_visible():
                continue
            label = " ".join(
                filter(
                    None,
                    [
                        item.get_attribute("aria-label"),
                        item.get_attribute("placeholder"),
                        item.get_attribute("name"),
                    ],
                )
            )
        except Exception:
            continue
        if skip.search(label or ""):
            continue
        return item
    loc = page.locator("[contenteditable='true']")
    try:
        total = min(loc.count(), 12)
    except Exception:
        return None
    for index in range(total):
        item = loc.nth(index)
        try:
            if not item.is_visible():
                continue
            box = item.bounding_box()
        except Exception:
            continue
        if not box or box["width"] * box["height"] > 7000:
            continue
        label = (item.get_attribute("aria-label") or item.get_attribute("placeholder") or "").lower()
        if skip.search(label):
            continue
        return item
    return None


def click_reel_for_tag(page, scope, site):
    clicked = page.evaluate(
        """() => {
            const dialog = [...document.querySelectorAll("[role=dialog]")].find((el) => {
                const label = (el.getAttribute("aria-label") || "").toLowerCase();
                const text = (el.innerText || "").slice(0, 500).toLowerCase();
                return label.includes("create") || text.includes("tag people") || text.includes("new reel");
            });
            if (!dialog) return "";
            const video = dialog.querySelector("video");
            if (video) {
                const rect = video.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                video.dispatchEvent(new MouseEvent("click", {bubbles: true, clientX: x, clientY: y}));
                return "video";
            }
            const play = dialog.querySelector("[aria-label='Play'], [aria-label='Воспроизвести']");
            if (play) { play.click(); return "play"; }
            return "";
        }"""
    )
    if clicked:
        page.wait_for_timeout(600)
        return
    player = first_visible(scope.get_by_label(re.compile(site.get("reel_tags_video") or r"Video player|Play", re.I)))
    if player is not None:
        player.click(force=True, timeout=4000)
        page.wait_for_timeout(500)
        return
    box = scope.bounding_box()
    if box:
        page.mouse.click(box["x"] + box["width"] * 0.28, box["y"] + box["height"] * 0.45)
        page.wait_for_timeout(500)


def open_reel_tag_field(page, scope, site):
    work = composer_scope(page)
    tag_link = first_visible(work.get_by_text(re.compile(r"Tag people|Отметить людей", re.I)))
    if tag_link is not None:
        tag_link.click(force=True, timeout=4000)
    else:
        opener = instagram_tag_people_button(work)
        if opener is None:
            raise StopSite("reel_tags", "кнопку Tag people не нашёл")
        opener.click(force=True, timeout=4000)
    page.wait_for_timeout(600)
    for attempt in range(1, 6):
        click_reel_for_tag(page, composer_scope(page), site)
        page.wait_for_timeout(700)
        field = find_reel_tag_search(page, site)
        if field is not None:
            return field
        say(f"reel_tags: поле поиска не видно, клик по ролику {attempt}/5")
    return None


def add_instagram_reel_tags(page, scope, site, usernames, step="reel_tags"):
    """Упоминания @ в подписи — стабильно тегает; Tag people на видео часто открывает Sharing."""
    if not usernames:
        return
    if reel_tags_ok(page, scope, usernames, site):
        say(f"{step}: уже в форме")
        return
    heal_instagram(page, site)
    dismiss_ig_hashtag_overlay(page, composer_scope(page), site)
    append_caption_mentions(page, site, usernames)
    dismiss_ig_hashtag_overlay(page, composer_scope(page), site)
    if not reel_tags_ok(page, scope, usernames, site):
        raise StopSite(step, "упоминания @ не попали в подпись")


def apply_instagram_schedule(page, scope, site, when):
    """Включает Schedule content, ставит время и жмёт Schedule. Share не трогает."""
    when = snap_five(when)
    hour, minute, suffix = clock_12(when)
    switch = schedule_switch(scope)
    if switch is None:
        raise StopSite("schedule", "переключатель Schedule content не нашёл. Share не нажимал")
    if not switch.is_checked():
        switch.click(timeout=2000)
        page.wait_for_timeout(400)
    hour_name = site.get("hour_name") or "Hours"
    minute_name = site.get("minute_name") or "Minutes"
    ampm_name = site.get("ampm_name") or "AM PM"
    shown = ""
    for attempt in range(1, 4):
        started = time.monotonic()
        say(f"время Instagram {hour}:{minute:02d} {suffix}, попытка {attempt}")
        try:
            set_spin(page, hour_name, hour)
            set_spin(page, minute_name, f"{minute:02d}")
            set_spin(page, ampm_name, suffix)
        except Exception as exc:
            say(f"поля времени не нажались за {time.monotonic() - started:.1f} с: {exc}")
            continue
        hour_now, _ = spin_now(page, hour_name)
        minute_now, _ = spin_now(page, minute_name)
        _, ampm_text = spin_now(page, ampm_name)
        shown = f"{hour_now}:{minute_now} {ampm_text}"
        say(f"расписание в форме: {shown}")
        try:
            if int(hour_now) == hour and int(minute_now) == minute and ampm_text.upper() == suffix:
                break
        except (TypeError, ValueError):
            pass
        say(f"попытка {attempt} не совпала за {time.monotonic() - started:.1f} с")
    else:
        raise StopSite("schedule", f"за 3 попытки время не стало {hour}:{minute:02d} {suffix}, в форме {shown}")
    try:
        form_text = scope.inner_text(timeout=2000)
    except Exception:
        form_text = ""
    if not date_matches(form_text, when):
        raise StopSite("schedule", f"дата в форме не {when.strftime('%Y-%m-%d')}. Schedule не нажимал")
    button = first_visible(scope.get_by_role("button", name=re.compile(site.get("schedule_submit") or r"^(Schedule|Запланировать)$", re.I)))
    if button is None:
        raise StopSite("schedule", "кнопка Schedule не появилась. Share не нажимал")
    button.click(timeout=3000)
    say("нажал Schedule")
    page.wait_for_timeout(1200)


def finish_instagram_schedule(page, site, when):
    def work():
        heal_instagram(page, site)
        cancel_sharing_dialog(page)
        scope = composer_scope(page)
        if schedule_switch(scope) is None:
            raise StopSite("schedule", "переключатель Schedule content не нашёл. Share не нажимал")
        apply_instagram_schedule(page, scope, site, when)

    def ok():
        return not instagram_composer_open(page)

    retry_instagram(page, site, "schedule", work, ok, attempts=instagram_step_attempts(site))
    if not wait_instagram_composer_closed(page, site):
        raise StopSite("schedule", "окно постинга не закрылось после Schedule")


def fill_instagram(page, site, post, caption, when):
    global _popup_site
    _popup_site = site
    video = post["video"]
    step = "open"
    try:
        page.goto(site["url"], wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        reason = blocked_reason(page, site)
        if reason:
            raise StopSite("login", reason, verification=True)
        accept_popups(page, site)
        page.set_default_timeout(5000)
        step = "create"
        started = time.monotonic()
        if not open_instagram_create(page, site):
            raise StopSite("create", "кнопку новой публикации не нашёл")
        say(f"create: готово за {time.monotonic() - started:.1f} с")
        step = "file"
        started = time.monotonic()
        scope, field = wait_upload_form(page, site)
        say("форма:", scope.url if hasattr(scope, "url") else page.url)
        field.set_input_files(str(video_for_form(video)), timeout=120000)
        say(f"file: готово за {time.monotonic() - started:.1f} с")
        page.wait_for_timeout(1200)
        heal_instagram(page, site)
        step = "caption"
        instagram_progress_to_caption(page, site, video)
        caption_state = {"text": ""}

        def write_caption():
            caption_state["text"] = insert_caption(
                page, pick_editor(composer_scope(page), site), caption, close_suggest=False
            )
            dismiss_ig_hashtag_overlay(page, composer_scope(page), site)

        def caption_ok():
            try:
                got = norm(editor_text(pick_editor(composer_scope(page), site)))
            except StopSite:
                return False
            want = norm(caption_state["text"] or caption)
            return texts_match(want, got) or (want and want[:40] in got)

        retry_instagram(page, site, "caption_text", write_caption, caption_ok, attempts=instagram_step_attempts(site))
        put = caption_state["text"]
        say(f"текст в поле ({len(norm(put))} симв.)")
        heal_instagram(page, site)
        dismiss_ig_hashtag_overlay(page, composer_scope(page), site)
        if post.get("collaborators"):
            step = "collaborators"
            users = post["collaborators"]
            say("коллабораторы:", ", ".join(users))

            add_instagram_collaborators(page, composer_scope(page), site, users)
        if post.get("reel_tags"):
            step = "reel_tags"
            users = post["reel_tags"]
            say("теги на рилсе:", ", ".join(users))

            add_instagram_reel_tags(page, composer_scope(page), site, users)
            heal_instagram(page, site)
            dismiss_ig_hashtag_overlay(page, composer_scope(page), site)
        if not when:
            say("время не задано — отложку не трогаю, Share не нажимал")
            return
        step = "schedule"
        finish_instagram_schedule(page, site, when)
        say("отложка отправлена, окно постинга закрыто, вкладка остаётся открытой")
    except StopSite:
        raise
    except Exception as exc:
        raise StopSite(step, str(exc)) from exc


FILLERS = {
    "tiktok": fill_tiktok,
    "instagram": fill_instagram,
    "youtube": fill_youtube,
    "vk": fill_vk,
}


def connect(port, slowmo):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    endpoint = f"http://127.0.0.1:{int(port)}"
    kwargs = {"timeout": 15000}
    if slowmo:
        kwargs["slow_mo"] = int(slowmo)
    try:
        try:
            browser = pw.chromium.connect_over_cdp(endpoint, **kwargs)
        except TypeError:
            kwargs.pop("slow_mo", None)
            browser = pw.chromium.connect_over_cdp(endpoint, **kwargs)
    except Exception as exc:
        pw.stop()
        raise RuntimeError(
            f"Chrome не слушает порт {port}. Запусти его из панели и залогинься. ({exc})"
        ) from exc
    if not browser.contexts:
        pw.stop()
        raise RuntimeError("у Chrome нет окна. Запусти его из панели и залогинься.")
    return pw, browser.contexts[0]


def prepare_caption(cfg, site_cfg, post, adapt, site_id=None):
    limit = int(site_cfg.get("char_limit") or 4000)
    if site_id == "youtube":
        text = (post.get("youtube_description") or compose(post["caption"], post["tags"], limit))[:limit]
    else:
        text = compose(post["caption"], post["tags"], limit)
    if adapt:
        text = adapt_text(cfg, site_cfg, text)
    return text


def run(cfg, folder, site_ids, limit, adapt, dry, slowmo, when_text, every):
    sites = load_sites()
    posts = load_posts(folder, limit, cfg)
    if not posts:
        say(f"нет пар видео+текст в {folder}")
        say("жду файлы вида 001.mp4 и 001.txt (хэштеги — в 001.tags.txt)")
        return 1
    start = None
    if when_text:
        raw = datetime.fromisoformat(when_text)
        start = snap_five(raw)
        if start != raw.replace(second=0, microsecond=0):
            say(f"время {raw.strftime('%H:%M')} округлено до {start.strftime('%Y-%m-%d %H:%M')}: у TikTok шаг 5 минут")
    interval = int(every if every is not None else cfg.get("interval_minutes") or 60)
    say(f"постов: {len(posts)}; сети: {', '.join(site_ids)}; dry={dry}; adapt={adapt}")
    if dry:
        for index, post in enumerate(posts):
            moment = start + timedelta(minutes=interval * index) if start else None
            for site_id in site_ids:
                site = sites.get(site_id)
                if not site:
                    say(f"{site_id}: нет в sites.json")
                    continue
                if not site.get("ready"):
                    say(f"{site_id}: браузер ещё не включён, ниже только текст")
                text = prepare_caption(cfg, site, post, adapt, site_id) if site.get("char_limit") else post["caption"]
                say(f"--- {site_id} {post['stem']} {moment or 'без времени'} ---")
                if site_id == "youtube":
                    say(f"название: {post.get('youtube_title', '')}")
                say(text)
                say(f"видео: {post['video']}")
                cover = find_cover(post["video"])
                if cover:
                    say(f"обложка: {cover}")
                if site_id == "instagram":
                    if post.get("collaborators"):
                        say("коллабораторы:", ", ".join(post["collaborators"]))
                    if post.get("reel_tags"):
                        say("теги на рилсе:", ", ".join(post["reel_tags"]))
        return 0

    pw = None
    failed = 0
    try:
        pw, context = connect(cfg["port"], slowmo)
        context.set_default_timeout(15000)
        install_dialog_guard(context)
        for site_id in site_ids:
            site = sites.get(site_id)
            if not site:
                say(f"{site_id}: нет в sites.json")
                failed += 1
                continue
            filler = FILLERS.get(site_id)
            if not site.get("ready") or filler is None:
                say(f"{site_id}: ещё не включено. {site.get('note', '')}")
                continue
            for index, post in enumerate(posts):
                moment = start + timedelta(minutes=interval * index) if start else None
                page = None
                try:
                    site_run = site
                    if site_id == "vk":
                        vk_link = (cfg.get("vk_url") or "").strip()
                        want_slug = vk_community_slug(vk_link)
                        if not vk_link:
                            vk_link = vk_discover_url(context, want_slug)
                        if not vk_link and want_slug:
                            vk_link = vk_discover_url(context, "")
                        if vk_link:
                            vk_link = vk_normalize_url(vk_link)
                        if not vk_link:
                            failed += 1
                            cfg_path = cfg.get("_path") or str(ROOT / "config.json")
                            say(
                                f"{site_id} {post['stem']}: vk_url пустой (config: {cfg_path}). "
                                "Панель: «VK Клипы» → ссылка + имя канала → Сохранить"
                            )
                            continue
                        site_run = {
                            **site,
                            "url": vk_normalize_url(vk_link),
                            "vk_channel_name": (cfg.get("vk_channel_name") or site.get("vk_channel_name") or "").strip(),
                        }
                    text = prepare_caption(cfg, site_run, post, adapt, site_id)
                    say(f"=== {site_id} {post['stem']} ===")
                    if site_id == "youtube":
                        say(f"название: {post.get('youtube_title', '')}")
                    page = context.new_page()
                    install_dialog_guard(page)
                    page.bring_to_front()
                    filler(page, site_run, post, text, moment)
                except StopSite as exc:
                    failed += 1
                    say(f"{site_id} {post['stem']}: {exc.step}: {exc}")
                    if page is not None:
                        dump_error(page, site_id, post["stem"], exc.step, exc)
                    if exc.verification:
                        say(f"{site_id}: сеть просит проверку, остальные посты в неё не трогаю")
                        break
                except Exception as exc:
                    failed += 1
                    say(f"{site_id} {post['stem']}: {exc}")
                    if page is not None:
                        dump_error(page, site_id, post["stem"], "run", exc)
    finally:
        if pw is not None:
            pw.stop()
    say(f"готово, ошибок: {failed}")
    return 1 if failed else 0


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Заполнить формы поста, не публикуя их")
    parser.add_argument("--folder", help="папка с парами 001.mp4 и 001.txt")
    parser.add_argument("--sites", default="", help="через запятую: tiktok,instagram,youtube,vk")
    parser.add_argument("--adapt", action="store_true", help="сжать текст локальной моделью")
    parser.add_argument("--limit", type=int, default=None, help="сколько постов взять с начала")
    parser.add_argument("--dry", action="store_true", help="только показать тексты, браузер не трогать")
    parser.add_argument("--headed-slowmo", nargs="?", const=400, type=int, default=0, help="замедление, мс")
    parser.add_argument("--when", default="", help="время первого поста, 2026-10-27T18:00")
    parser.add_argument("--every", type=int, default=None, help="минут между постами")
    parser.add_argument("--config", default="", help="путь к config.json")
    parser.add_argument("--vk-url", default="", help="кабинет VK Видео (клипы сообщества)")
    parser.add_argument("--vk-channel-name", default="", help="имя канала в модалке «Новый клип»")
    return parser.parse_args(argv)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        args = parse_args(argv)
        cfg = load_config(args.config or None)
        if (args.vk_url or "").strip():
            cfg["vk_url"] = args.vk_url.strip()
        if (args.vk_channel_name or "").strip():
            cfg["vk_channel_name"] = args.vk_channel_name.strip()
        folder = abs_under(ROOT, args.folder or cfg.get("posts_folder") or "posts")
        raw_sites = args.sites or ",".join(cfg.get("sites") or ["tiktok"])
        site_ids = [part.strip().lower() for part in raw_sites.split(",") if part.strip()]
        limit = args.limit if args.limit is not None else cfg.get("limit")
        when_text = args.when or cfg.get("schedule_start") or ""
        slowmo = args.headed_slowmo or int(cfg.get("slowmo") or 0)
        adapt = bool(args.adapt)
        return run(cfg, folder, site_ids, limit, adapt, args.dry, slowmo, when_text, args.every)
    except SystemExit:
        raise
    except Exception as exc:
        say("ошибка:", exc)
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        say("остановлено")
        raise SystemExit(1)
