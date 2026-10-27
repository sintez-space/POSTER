from pathlib import Path

CALENDAR_ROOT = Path(__file__).resolve().parents[1]
POSTER_ROOT = CALENDAR_ROOT.parent
DATA_DIR = CALENDAR_ROOT / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
AVATARS_DIR = DATA_DIR / "avatars"
EXPORT_DIR = CALENDAR_ROOT / "export"
DB_PATH = DATA_DIR / "calendar.db"
SITES_JSON = POSTER_ROOT / "sites.json"
POSTER_POSTS = POSTER_ROOT / "posts"
SCHEDULE_JSON = EXPORT_DIR / "poster_schedule.json"


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    POSTER_POSTS.mkdir(parents=True, exist_ok=True)
