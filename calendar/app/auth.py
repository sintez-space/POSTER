import hashlib
import secrets
import uuid

from . import db


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
        ("admin", "admin", "admin", "Администратор"),
        ("author", "author", "user", "Автор"),
    ]
    with db.connect() as conn:
        for login, password, role, name in demos:
            row = conn.execute("SELECT id FROM users WHERE login = ?", (login,)).fetchone()
            if row:
                continue
            conn.execute(
                "INSERT INTO users (id, login, password_hash, role, display_name) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), login, hash_password(password), role, name),
            )


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, db._utc_now()),
        )
    return token


def user_by_token(token: str | None):
    if not token:
        return None
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.login, u.role, u.display_name
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def login(login_name: str, password: str):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, password_hash, login, role, display_name FROM users WHERE login = ?",
            (login_name.strip(),),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    token = create_session(row["id"])
    return token, {
        "id": row["id"],
        "login": row["login"],
        "role": row["role"],
        "display_name": row["display_name"],
    }
