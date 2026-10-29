import hashlib
import json
import secrets
import uuid
from contextvars import ContextVar

from . import db

view_as_id: ContextVar[str] = ContextVar("view_as_id", default="")

STAFF_ROLES = ("admin", "observer")


def is_admin(user) -> bool:
    return bool(user) and user.get("role") == "admin"


def is_staff(user) -> bool:
    return bool(user) and user.get("role") in STAFF_ROLES


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hex_digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return secrets.compare_digest(check.hex(), hex_digest)


def ensure_demo_users():
    demos = [
        ("admin", "admin@poster.local", "admin", "admin", "Администратор"),
        ("author", "author@poster.local", "author", "user", "Автор"),
    ]
    now = db._utc_now()
    with db.connect() as conn:
        for login_name, email, password, role, name in demos:
            row = conn.execute("SELECT id FROM users WHERE login = ?", (login_name,)).fetchone()
            if row:
                conn.execute("UPDATE users SET is_approved = 1 WHERE id = ?", (row["id"],))
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO users (id, login, email, password_hash, role, display_name, is_approved, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (str(uuid.uuid4()), login_name, email, hash_password(password), role, name, now),
            )


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, db._utc_now()),
        )
    return token


def _brief_from_row(row) -> dict:
    return {
        "id": row["id"],
        "login": row["login"],
        "email": row["email"] or "",
        "role": row["role"],
        "display_name": row["display_name"],
        "is_approved": int(row["is_approved"]) if row["is_approved"] is not None else 1,
    }


def session_user(token: str | None):
    """Пользователь по cookie, без подмены «глазами участника»."""
    if not token:
        return None
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.login, u.email, u.role, u.display_name, u.is_approved
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
    if not row:
        return None
    user = _brief_from_row(row)
    if user["role"] != "admin" and not user["is_approved"]:
        return None
    return user


def user_brief(user_id: str):
    uid = (user_id or "").strip()
    if not uid:
        return None
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT id, login, email, role, display_name, is_approved
            FROM users WHERE id = ?
            """,
            (uid,),
        ).fetchone()
    if not row:
        return None
    return _brief_from_row(row)


def user_by_token(token: str | None):
    real = session_user(token)
    if not real:
        return None
    target = (view_as_id.get() or "").strip()
    if not target or target == real["id"] or real["role"] not in STAFF_ROLES:
        return real
    other = user_brief(target)
    if not other:
        return real
    other["viewing_as"] = True
    return other


def login(login_or_email: str, password: str):
    ident = (login_or_email or "").strip()
    if not ident:
        return None, "введите логин или почту"
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT id, password_hash, login, email, role, display_name, is_approved
            FROM users
            WHERE lower(login) = lower(?) OR lower(COALESCE(email, '')) = lower(?)
            """,
            (ident, ident),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None, "неверный логин/почта или пароль"
    is_app = int(row["is_approved"]) if row["is_approved"] is not None else 1
    if row["role"] != "admin" and not is_app:
        return None, "Ваша заявка отправлена и ожидает подтверждения администратором."
    token = create_session(row["id"])
    return (
        token,
        {
            "id": row["id"],
            "login": row["login"],
            "email": row["email"] or "",
            "role": row["role"],
            "display_name": row["display_name"],
        },
    ), None


def register_user(first_name: str, last_name: str, email: str, password: str):
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    em = (email or "").strip().lower()
    pw = password or ""
    if not fn or not ln:
        raise ValueError("Укажите Имя и Фамилию")
    if not em or "@" not in em:
        raise ValueError("Укажите корректную почту (email)")
    if len(pw) < 4:
        raise ValueError("Пароль должен быть не короче 4 символов")

    display_name = f"{fn} {ln}".strip()
    uid = str(uuid.uuid4())
    now = db._utc_now()
    prof = {"first_name": fn, "last_name": ln, "email": em}

    with db.connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE lower(login) = lower(?) OR lower(COALESCE(email, '')) = lower(?)",
            (em, em),
        ).fetchone()
        if exists:
            raise ValueError("Пользователь с такой почтой уже зарегистрирован")
        conn.execute(
            """
            INSERT INTO users (
                id, login, email, password_hash, role, display_name,
                is_approved, created_at, profile_json
            ) VALUES (?, ?, ?, ?, 'user', ?, 0, ?, ?)
            """,
            (uid, em, em, hash_password(pw), display_name, now, json.dumps(prof, ensure_ascii=False)),
        )
    return {"id": uid, "email": em, "display_name": display_name, "is_approved": False}
