"""Локальная панель: настройки, запуск своего Chrome, старт poster.py.

Слушает только 127.0.0.1. Пароли не спрашивает и никуда их не отправляет.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
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
_runs = {}


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
    cfg.setdefault("carousel_folder", "")
    cfg.setdefault("hashtag_pool", "")
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
    ensure_jobs(cfg)
    return cfg


def new_job_id():
    return uuid.uuid4().hex[:8]


def ensure_jobs(cfg):
    jobs = cfg.get("jobs")
    if isinstance(jobs, list) and jobs:
        cfg["active_job"] = str(cfg.get("active_job") or jobs[0].get("id") or "")
        return cfg
    job_id = new_job_id()
    cfg["jobs"] = [
        {
            "id": job_id,
            "name": "Работа 1",
            "posts_folder": cfg.get("posts_folder") or "posts",
            "carousel_folder": cfg.get("carousel_folder") or "",
            "hashtag_pool": cfg.get("hashtag_pool") or "",
            "schedule_start": cfg.get("schedule_start") or "",
            "interval_minutes": int(cfg.get("interval_minutes") or 60),
            "limit": int(cfg.get("limit") if cfg.get("limit") is not None else 1),
            "sites": list(cfg.get("sites") or ["tiktok"]),
        }
    ]
    cfg["active_job"] = job_id
    return cfg


def active_job(cfg):
    jobs = cfg.get("jobs") or []
    wanted = str(cfg.get("active_job") or "")
    for job in jobs:
        if job.get("id") == wanted:
            return job
    return jobs[0] if jobs else None


def normalize_job(raw, sites, fallback_name):
    if not isinstance(raw, dict):
        raise ValueError("работа задана неверно")
    name = str(raw.get("name") or fallback_name).strip() or fallback_name
    chosen = []
    for site_id in raw.get("sites") or []:
        site_id = str(site_id).strip().lower()
        if site_id not in sites:
            raise ValueError(f"в «{name}» неизвестная сеть: {site_id}")
        if not sites[site_id].get("ready"):
            raise ValueError(f"сеть «{site_id}» пока недоступна")
        if site_id not in chosen:
            chosen.append(site_id)
    if not chosen:
        raise ValueError(f"в работе «{name}» выбери хотя бы одну сеть")
    try:
        limit = int(raw.get("limit") if raw.get("limit") not in (None, "") else 1)
    except (TypeError, ValueError):
        limit = 1
    if limit < 0:
        limit = 1
    try:
        interval = int(raw.get("interval_minutes") if raw.get("interval_minutes") not in (None, "") else 60)
    except (TypeError, ValueError):
        interval = 60
    if interval < 0:
        raise ValueError(f"в «{name}» интервал вне диапазона")
    when = str(raw.get("schedule_start") or "").strip()
    if when:
        when = snap_five(datetime.fromisoformat(when)).strftime("%Y-%m-%dT%H:%M")
    job_id = str(raw.get("id") or "").strip() or new_job_id()
    return {
        "id": job_id,
        "name": name[:40],
        "posts_folder": str(raw.get("posts_folder") or "").strip(),
        "carousel_folder": str(raw.get("carousel_folder") or "").strip(),
        "hashtag_pool": str(raw.get("hashtag_pool") or "").strip(),
        "schedule_start": when,
        "interval_minutes": interval,
        "limit": limit,
        "sites": chosen,
    }


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
    raw_jobs = data.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        current_jobs = current.get("jobs") or []
        raw_jobs = [
            {
                "id": (current_jobs[0].get("id") if current_jobs else "") or new_job_id(),
                "name": "Работа 1",
                "posts_folder": data.get("posts_folder", current.get("posts_folder")),
                "carousel_folder": data.get("carousel_folder", current.get("carousel_folder")),
                "hashtag_pool": data.get("hashtag_pool", current.get("hashtag_pool")),
                "schedule_start": data.get("schedule_start", current.get("schedule_start")),
                "interval_minutes": data.get("interval_minutes", current.get("interval_minutes")),
                "limit": data.get("limit", current.get("limit")),
                "sites": data.get("sites") or current.get("sites") or [],
            }
        ]
    jobs = []
    seen_ids = set()
    for index, raw in enumerate(raw_jobs):
        job = normalize_job(raw, sites, f"Работа {index + 1}")
        if job["id"] in seen_ids:
            job["id"] = new_job_id()
        seen_ids.add(job["id"])
        jobs.append(job)
    if any("vk" in job["sites"] for job in jobs) and not vk_url:
        raise ValueError(
            "для VK укажи ссылку на клипы сообщества в блоке «VK Клипы: страница сообщества» и сохрани"
        )
    if any("vk" in job["sites"] for job in jobs) and not vk_channel_name:
        raise ValueError("для VK укажи имя канала (как в модалке «Новый клип»), например SINTEZ.SPACE")
    active_id = str(data.get("active_job") or current.get("active_job") or "").strip()
    if active_id not in seen_ids:
        active_id = jobs[0]["id"]
    current_job = next(job for job in jobs if job["id"] == active_id)
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
    return {
        "chrome_path": chrome_path,
        "profile_dir": profile,
        "port": port,
        "posts_folder": current_job["posts_folder"],
        "carousel_folder": current_job["carousel_folder"],
        "hashtag_pool": current_job["hashtag_pool"],
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
        "schedule_start": current_job["schedule_start"],
        "interval_minutes": current_job["interval_minutes"],
        "adapt": bool(data.get("adapt")),
        "limit": current_job["limit"],
        "slowmo": slowmo,
        "sites": current_job["sites"],
        "jobs": jobs,
        "active_job": active_id,
    }


def save_config(data):
    cfg = clean_config(data)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cfg


def profile_path(cfg):
    return abs_under(ROOT, cfg["profile_dir"])


def posts_path(cfg):
    raw = (cfg.get("posts_folder") or "").strip()
    if not raw:
        raise RuntimeError("у этой работы не задана папка постов")
    return abs_under(ROOT, raw)


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


def run_snapshot():
    with _lock:
        out = {}
        for job_id, run in _runs.items():
            proc = run.get("proc")
            out[job_id] = {
                "running": proc is not None and proc.poll() is None,
                "exit": run.get("exit"),
            }
        return out


def meta():
    cfg = read_config()
    sites = load_sites()
    try:
        posts = list_posts(posts_path(cfg))
    except Exception:
        posts = []
    return {
        "panel_version": PANEL_VERSION,
        "config": cfg,
        "sites": [
            {"id": site_id, "ready": bool(sites[site_id].get("ready")), "note": sites[site_id].get("note", "")}
            for site_id in UI_SITE_ORDER
            if site_id in sites
        ],
        "chrome": chrome_status(cfg["port"]),
        "posts": posts,
        "runs": run_snapshot(),
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


def append_line(job_id, line):
    with _lock:
        run = _runs.get(job_id)
        if run is None:
            return
        run["lines"].append(line.rstrip("\n"))
        del run["lines"][:-400]


def read_log(job_id):
    with _lock:
        run = _runs.get(job_id)
        if run is None:
            return {"lines": [], "running": False, "exit": None, "job_id": job_id}
        proc = run.get("proc")
        return {
            "lines": list(run["lines"]),
            "running": proc is not None and proc.poll() is None,
            "exit": run.get("exit"),
            "job_id": job_id,
        }


def running_job_id():
    with _lock:
        for job_id, run in _runs.items():
            proc = run.get("proc")
            if proc is not None and proc.poll() is None:
                return job_id
    return ""


def pump(job_id, proc):
    try:
        for line in proc.stdout:
            append_line(job_id, line)
    finally:
        code = proc.wait()
        with _lock:
            run = _runs.get(job_id)
            stopped = bool(run and run.get("stopped"))
            if run is not None:
                run["exit"] = code
        append_line(job_id, "остановлено" if stopped else f"код завершения: {code}")


def job_by_id(cfg, job_id):
    for job in cfg.get("jobs") or []:
        if job.get("id") == job_id:
            return job
    return None


def start_run(dry, job_id):
    cfg = read_config()
    job = job_by_id(cfg, job_id) or active_job(cfg)
    if not job:
        raise RuntimeError("нет такой работы")
    busy = running_job_id()
    if busy:
        other = job_by_id(cfg, busy)
        other_name = (other or {}).get("name") or "другая работа"
        if busy == job["id"]:
            raise RuntimeError(f"«{job['name']}» уже выполняется")
        raise RuntimeError(f"сейчас идёт «{other_name}». Останови её, потом запускай эту.")
    posts_raw = (job.get("posts_folder") or "").strip()
    carousel_raw = (job.get("carousel_folder") or "").strip()
    if not posts_raw and not carousel_raw:
        raise RuntimeError("укажи папку постов или папку каруселей — работа берёт материалы только оттуда")
    if posts_raw:
        folder = abs_under(ROOT, posts_raw)
    else:
        folder = ROOT / ".jobs" / job["id"]
        folder.mkdir(parents=True, exist_ok=True)
    if not dry and not job.get("schedule_start"):
        raise RuntimeError("для заполнения укажи дату и время первого поста")
    if "vk" in (job.get("sites") or []) and not (cfg.get("vk_url") or "").strip():
        raise RuntimeError(
            "для VK нужна ссылка vk_url: раскрой «VK Клипы», вставь URL кабинета (Клипы), нажми «Сохранить»"
        )
    cmd = [
        sys.executable,
        "-u",
        str(ROOT / "poster.py"),
        "--folder",
        str(folder),
        "--sites",
        ",".join(job["sites"]),
        "--limit",
        str(int(job["limit"])),
        "--every",
        str(int(job["interval_minutes"])),
        "--when",
        job.get("schedule_start") or "",
        "--carousel-folder",
        carousel_raw,
    ]
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
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["POSTER_HASHTAG_POOL"] = job.get("hashtag_pool") or ""
    env["POSTER_CAROUSEL_FOLDER"] = carousel_raw
    with _lock:
        _runs[job["id"]] = {"proc": None, "lines": [], "exit": None, "stopped": False, "name": job["name"]}
    append_line(job["id"], f"работа «{job['name']}»: папка постов {folder}")
    if carousel_raw:
        append_line(job["id"], f"карусели: {carousel_raw}")
    append_line(job["id"], "запуск: " + " ".join(cmd))
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
        _runs[job["id"]]["proc"] = proc
    threading.Thread(target=pump, args=(job["id"], proc), daemon=True).start()
    return {"ok": True, "job_id": job["id"]}


def stop_run(job_id):
    cfg = read_config()
    job = job_by_id(cfg, job_id) or active_job(cfg)
    if not job:
        raise RuntimeError("нет такой работы")
    with _lock:
        run = _runs.get(job["id"])
        proc = run.get("proc") if run else None
        if proc is None or proc.poll() is not None:
            raise RuntimeError(f"«{job['name']}» сейчас не выполняется")
        run["stopped"] = True
        pid = proc.pid
    append_line(job["id"], "останавливаю…")
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return {"ok": True, "job_id": job["id"]}


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
                job_id = (parse_qs(urlparse(self.path).query).get("job") or [""])[0]
                if not job_id:
                    job_id = (active_job(read_config()) or {}).get("id") or ""
                self.send_json(read_log(job_id))
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
                self.send_json(start_run(bool(data.get("dry")), str(data.get("job_id") or "")))
            elif path == "/api/stop":
                data = self.read_json()
                self.send_json(stop_run(str(data.get("job_id") or "")))
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
