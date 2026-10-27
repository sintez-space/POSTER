"""Локальная панель: настройки, запуск своего Chrome, старт poster.py.

Слушает только 127.0.0.1. Пароли не спрашивает и никуда их не отправляет.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from poster import ROOT, abs_under, assert_local, find_cover, is_video, load_sites, natural_key, snap_five

PORT = 8766
PANEL_VERSION = "2026-03-27-sites-llm"
UI_SITE_ORDER = ("instagram", "tiktok", "youtube", "vk", "telegram", "max")
CONFIG_PATH = ROOT / "config.json"
HOSTS_VK = {
    "vk.com",
    "m.vk.com",
    "vk.ru",
    "vkvideo.ru",
    "m.vkvideo.ru",
    "cabinet.vkvideo.ru",
}

_lock = threading.Lock()
_proc = None
_lines = []
_exit = None


def chrome_candidates():
    local = os.environ.get("LOCALAPPDATA", "")
    found = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    if local:
        found.append(str(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    return [item for item in found if Path(item).exists()]


def read_config():
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        cfg = {}
    cfg.setdefault("chrome_path", "")
    cfg.setdefault("profile_dir", "chrome_profile")
    cfg.setdefault("port", 9333)
    cfg.setdefault("posts_folder", "posts")
    cfg.setdefault("lm_mode", "local")
    cfg.setdefault("lm_url", "http://localhost:1234/v1/chat/completions")
    cfg.setdefault("lm_api_url", "")
    cfg.setdefault("lm_api_key", "")
    cfg.setdefault("lm_model", "")
    cfg.setdefault("vk_url", "")
    cfg.setdefault("vk_channel_name", "")
    cfg.setdefault("youtube_title", "")
    cfg.setdefault("instagram_collaborators", "")
    cfg.setdefault("instagram_reel_tags", "")
    cfg.setdefault("schedule_start", "")
    cfg.setdefault("interval_minutes", 60)
    cfg.setdefault("adapt", False)
    cfg.setdefault("limit", 1)
    cfg.setdefault("slowmo", 0)
    cfg.setdefault("sites", ["tiktok"])
    if not cfg.get("chrome_path"):
        found = chrome_candidates()
        if found:
            cfg["chrome_path"] = found[0]
    return cfg


def check_vk(url):
    if not (url or "").strip():
        return ""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("ссылка VK должна начинаться с https://")
    if host not in HOSTS_VK and not host.endswith(".vk.com") and not host.endswith(".vkvideo.ru"):
        raise ValueError(
            f"ссылка VK должна быть на vk.com или vkvideo.ru (cabinet.vkvideo.ru ок), сейчас: {host}"
        )
    return url.strip()


def clean_config(data):
    """Оставляет только известные поля. Произвольные флаги Chrome отсюда не проходят."""
    current = read_config()
    sites = load_sites()
    port = int(data.get("port") or current.get("port") or 9333)
    if not 1024 <= port <= 65535:
        raise ValueError("порт отладки: число от 1024 до 65535")
    if "limit" in data and str(data.get("limit")) != "":
        limit = int(data["limit"])
    else:
        limit = int(current.get("limit") if current.get("limit") is not None else 1)
    if limit < 0:
        limit = 1
    interval = int(data["interval_minutes"]) if "interval_minutes" in data and str(data.get("interval_minutes")) != "" else int(current.get("interval_minutes") or 60)
    slowmo = int(data["slowmo"]) if "slowmo" in data and str(data.get("slowmo")) != "" else 0
    if limit < 0 or interval < 0 or not 0 <= slowmo <= 5000:
        raise ValueError("лимит, интервал или замедление вне диапазона")
    chosen = []
    for site_id in data.get("sites") or []:
        site_id = str(site_id).strip().lower()
        if site_id not in sites:
            raise ValueError(f"неизвестная сеть: {site_id}")
        if not sites[site_id].get("ready"):
            raise ValueError(f"сеть «{site_id}» пока недоступна")
        if site_id not in chosen:
            chosen.append(site_id)
    if not chosen:
        raise ValueError("выбери хотя бы одну сеть")
    if "vk_url" in data:
        raw_vk = str(data.get("vk_url") or "").strip()
        if raw_vk:
            vk_url = check_vk(raw_vk)
        else:
            vk_url = str(current.get("vk_url") or "").strip()
    else:
        vk_url = str(current.get("vk_url") or "").strip()
    if "vk_channel_name" in data:
        raw_name = str(data.get("vk_channel_name") or "").strip()
        vk_channel_name = raw_name or str(current.get("vk_channel_name") or "").strip()
    else:
        vk_channel_name = str(current.get("vk_channel_name") or "").strip()
    if not vk_channel_name:
        vk_channel_name = str(load_sites().get("vk", {}).get("vk_channel_name") or "").strip()
    if "vk" in chosen and not vk_url:
        raise ValueError(
            "для VK укажи ссылку на клипы сообщества в блоке «VK Клипы: страница сообщества» и сохрани"
        )
    if "vk" in chosen and not vk_channel_name:
        raise ValueError("для VK укажи имя канала (как в модалке «Новый клип»), например SINTEZ.SPACE")
    when = str(data.get("schedule_start") or "").strip()
    if when:
        when = snap_five(datetime.fromisoformat(when)).strftime("%Y-%m-%dT%H:%M")
    chrome_path = str(data.get("chrome_path") or "").strip()
    if chrome_path:
        exe = Path(chrome_path)
        if not exe.exists() or exe.name.lower() != "chrome.exe":
            raise ValueError("путь к Chrome должен указывать на существующий chrome.exe")
    profile = str(data.get("profile_dir") or "").strip() or "chrome_profile"
    if "\n" in profile or "\r" in profile or Path(profile).name.startswith("-"):
        raise ValueError("некорректная папка профиля")
    low = str(abs_under(ROOT, profile)).lower()
    if "camoufox" in low or "gemini_profile" in low:
        raise ValueError("нужна отдельная папка профиля, не профиль другого проекта")
    lm_mode = str(data.get("lm_mode") if "lm_mode" in data else current.get("lm_mode") or "local").strip().lower()
    if lm_mode not in {"local", "api"}:
        lm_mode = "local"
    lm_url = str(data.get("lm_url") if "lm_url" in data else current.get("lm_url") or "").strip()
    lm_api_url = str(data.get("lm_api_url") if "lm_api_url" in data else current.get("lm_api_url") or "").strip()
    lm_api_key = str(data.get("lm_api_key") if "lm_api_key" in data else current.get("lm_api_key") or "").strip()
    if lm_mode == "local":
        if not lm_url:
            lm_url = "http://localhost:1234/v1/chat/completions"
        try:
            assert_local(lm_url)
        except SystemExit as exc:
            raise ValueError(str(exc)) from exc
    else:
        if not lm_api_url:
            raise ValueError("для API укажи URL (OpenAI-совместимый /v1/chat/completions)")
        parsed_lm = urlparse(lm_api_url)
        if parsed_lm.scheme not in {"http", "https"} or not (parsed_lm.hostname or "").strip():
            raise ValueError("URL API ЛЛМ должен начинаться с http:// или https://")
    folder = str(data.get("posts_folder") or "posts").strip()
    if not folder:
        raise ValueError("укажи папку постов")
    return {
        "chrome_path": chrome_path,
        "profile_dir": profile,
        "port": port,
        "posts_folder": folder,
        "lm_mode": lm_mode,
        "lm_url": lm_url,
        "lm_api_url": lm_api_url,
        "lm_api_key": lm_api_key,
        "lm_model": str(data.get("lm_model") or "").strip(),
        "vk_url": vk_url,
        "vk_channel_name": vk_channel_name,
        "youtube_title": str(data.get("youtube_title") if "youtube_title" in data else current.get("youtube_title") or "").strip(),
        "instagram_collaborators": str(data.get("instagram_collaborators") if "instagram_collaborators" in data else current.get("instagram_collaborators") or "").strip(),
        "instagram_reel_tags": str(data.get("instagram_reel_tags") if "instagram_reel_tags" in data else current.get("instagram_reel_tags") or "").strip(),
        "schedule_start": when,
        "interval_minutes": interval,
        "adapt": bool(data.get("adapt")),
        "limit": limit,
        "slowmo": slowmo,
        "sites": chosen,
    }


def save_config(data):
    cfg = clean_config(data)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cfg


def profile_path(cfg):
    return abs_under(ROOT, cfg["profile_dir"])


def posts_path(cfg):
    return abs_under(ROOT, cfg["posts_folder"])


def list_posts(folder):
    stems = {}
    if not folder.exists():
        return []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith(".tags.txt"):
            stem, kind = path.name[: -len(".tags.txt")], "tags"
        elif path.name.endswith(".collaborators.txt"):
            stem, kind = path.name[: -len(".collaborators.txt")], "collaborators"
        elif path.name.endswith(".reel_tags.txt"):
            stem, kind = path.name[: -len(".reel_tags.txt")], "reel_tags"
        elif path.name.endswith(".youtube_title.txt"):
            stem, kind = path.name[: -len(".youtube_title.txt")], "youtube_title"
        elif path.name in {"instagram.txt", "youtube.txt"}:
            continue
        elif path.suffix.lower() == ".txt":
            stem, kind = path.stem, "text"
        elif is_video(path):
            stem, kind = path.stem, "video"
        else:
            continue
        row = stems.setdefault(
            stem,
            {
                "stem": stem,
                "video": False,
                "text": False,
                "tags": False,
                "collaborators": False,
                "reel_tags": False,
                "youtube_title": False,
                "cover": False,
            },
        )
        row[kind] = True
    out = [stems[key] for key in sorted(stems, key=lambda name: natural_key(Path(name)))]
    for row in out:
        for path in folder.iterdir():
            if path.is_file() and is_video(path) and path.stem == row["stem"]:
                row["cover"] = find_cover(path) is not None
                break
    return out


def chrome_status(port):
    try:
        with urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=1) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return {"up": True, "browser": data.get("Browser", "")}
    except Exception:
        return {"up": False, "browser": ""}


def meta():
    cfg = read_config()
    sites = load_sites()
    return {
        "panel_version": PANEL_VERSION,
        "config": cfg,
        "sites": [
            {"id": site_id, "ready": bool(sites[site_id].get("ready")), "note": sites[site_id].get("note", "")}
            for site_id in UI_SITE_ORDER
            if site_id in sites
        ],
        "chrome": chrome_status(cfg["port"]),
        "posts": list_posts(posts_path(cfg)),
    }


def start_chrome():
    cfg = read_config()
    status = chrome_status(cfg["port"])
    if status["up"]:
        return {"ok": True, "already": True, "chrome": status}
    exe = cfg.get("chrome_path") or ""
    if not exe:
        found = chrome_candidates()
        exe = found[0] if found else ""
    if not exe or not Path(exe).exists():
        raise RuntimeError("Chrome не найден. Укажи путь к chrome.exe и сохрани настройки.")
    profile = profile_path(cfg)
    profile.mkdir(parents=True, exist_ok=True)
    url = load_sites()["tiktok"]["url"]
    subprocess.Popen(
        [
            exe,
            f"--remote-debugging-port={int(cfg['port'])}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        status = chrome_status(cfg["port"])
        if status["up"]:
            return {"ok": True, "already": False, "chrome": status}
        threading.Event().wait(0.5)
    raise RuntimeError(
        f"порт {cfg['port']} не открылся. Закрой другие окна Chrome с этим профилем и повтори."
    )


def append_line(line):
    with _lock:
        _lines.append(line.rstrip("\n"))
        del _lines[:-400]


def read_log():
    with _lock:
        running = _proc is not None and _proc.poll() is None
        return {"lines": list(_lines), "running": running, "exit": _exit}


def pump(proc):
    global _exit
    try:
        for line in proc.stdout:
            append_line(line)
    finally:
        code = proc.wait()
        with _lock:
            _exit = code
        append_line(f"код завершения: {code}")


def start_run(dry):
    global _proc, _exit
    cfg = read_config()
    with _lock:
        if _proc is not None and _proc.poll() is None:
            raise RuntimeError("уже выполняется. Дождись окончания.")
        _lines.clear()
        _exit = None
    cmd = [
        sys.executable,
        "-u",
        str(ROOT / "poster.py"),
        "--folder",
        str(posts_path(cfg)),
        "--sites",
        ",".join(cfg["sites"]),
        "--limit",
        str(int(cfg["limit"])),
        "--every",
        str(int(cfg["interval_minutes"])),
    ]
    if cfg.get("schedule_start"):
        cmd += ["--when", cfg["schedule_start"]]
    if cfg.get("adapt"):
        cmd.append("--adapt")
    if dry:
        cmd.append("--dry")
    if int(cfg.get("slowmo") or 0):
        cmd += ["--headed-slowmo", str(int(cfg["slowmo"]))]
    cmd += ["--config", str(CONFIG_PATH.resolve())]
    vk_link = (cfg.get("vk_url") or "").strip()
    vk_name = (cfg.get("vk_channel_name") or "").strip()
    if vk_link:
        cmd += ["--vk-url", vk_link]
    if vk_name:
        cmd += ["--vk-channel-name", vk_name]
    if not dry and not cfg.get("schedule_start"):
        raise RuntimeError("для заполнения укажи дату и время первого поста")
    if "vk" in (cfg.get("sites") or []) and not (cfg.get("vk_url") or "").strip():
        raise RuntimeError(
            "для VK нужна ссылка vk_url: раскрой «VK Клипы», вставь URL кабинета (Клипы), нажми «Сохранить»"
        )
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    append_line("запуск: " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    with _lock:
        _proc = proc
    threading.Thread(target=pump, args=(proc,), daemon=True).start()
    return {"ok": True}


def open_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["explorer", str(path)])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("слишком большой запрос")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                self.send_file(ROOT / "panel.html", "text/html; charset=utf-8")
            elif path == "/api/meta":
                self.send_json(meta())
            elif path == "/api/log":
                self.send_json(read_log())
            else:
                self.send_json({"error": "нет такого адреса"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/config":
                saved = save_config(self.read_json())
                payload = meta()
                payload["config"] = saved
                self.send_json(payload)
            elif path == "/api/chrome":
                self.send_json(start_chrome())
            elif path == "/api/run":
                data = self.read_json()
                self.send_json(start_run(bool(data.get("dry"))))
            elif path == "/api/open/posts":
                open_dir(posts_path(read_config()))
                self.send_json({"ok": True})
            elif path == "/api/open/root":
                open_dir(ROOT)
                self.send_json({"ok": True})
            elif path == "/api/open/errors":
                open_dir(ROOT / "errors")
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "нет такого адреса"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"{url}  (панель {PANEL_VERSION})", flush=True)
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("панель остановлена", flush=True)


if __name__ == "__main__":
    main()
