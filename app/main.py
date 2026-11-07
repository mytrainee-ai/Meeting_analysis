"""HTTP entry point for Meeting Analysis."""

from __future__ import annotations

import json
import html
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from app.processing import process_meeting
from app.security import AuthGateMiddleware, client_is_loopback, hash_password, invitation_digest, normalize_email, valid_email, verify_password
from app.views import admin_page as render_admin_page, dashboard_page as render_dashboard_page, login_page as render_login_page, register_page as render_register_page, setup_page as render_setup_page

app = FastAPI(title="Meeting Analysis")

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 1024 * 1024
MAX_DURATION_SECONDS = 600
DATA_DIR = Path(os.getenv("MEETING_DATA_DIR", "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE_PATH = DATA_DIR / "meetings.sqlite3"
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60
MEDIA_RETENTION = timedelta(hours=24)


def ensure_session_secret() -> str:
    configured = os.getenv("APP_SECRET_KEY", "").strip()
    if configured:
        return configured
    env_path = PROJECT_ROOT / ".env"
    secret = secrets.token_urlsafe(48)
    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    secret_line = f"APP_SECRET_KEY={secret}"
    if re.search(r"(?m)^\s*APP_SECRET_KEY\s*=", env_text):
        env_text = re.sub(r"(?m)^\s*APP_SECRET_KEY\s*=.*$", secret_line, env_text, count=1)
    else:
        if env_text and not env_text.endswith(("\n", "\r")):
            env_text += "\n"
        env_text += secret_line + "\n"
    env_path.write_text(env_text, encoding="utf-8")
    os.environ["APP_SECRET_KEY"] = secret
    return secret


SESSION_SECRET = ensure_session_secret()


class UploadLimitMiddleware:
    """Bound the complete multipart body, including chunked requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/api/meetings/upload":
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for key, value in scope.get("headers", []) if key.lower() == b"content-length"),
            None,
        )
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    await self._too_large(send)
                    return
            except ValueError:
                await self._bad_request(send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_REQUEST_BYTES:
                    raise UploadBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except UploadBodyTooLarge:
            await self._too_large(send)

    @staticmethod
    async def _too_large(send: Send) -> None:
        body = '{"detail":"Размер файла превышает лимит 25 МБ."}'.encode("utf-8")
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _bad_request(send: Send) -> None:
        body = '{"detail":"Не удалось прочитать размер загрузки."}'.encode("utf-8")
        await send({"type": "http.response.start", "status": 400, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


class UploadBodyTooLarge(Exception):
    pass


app.add_middleware(UploadLimitMiddleware)
app.add_middleware(AuthGateMiddleware, database_path=DATABASE_PATH)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="meeting_analysis_session",
    max_age=SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
)


def initialize_storage() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                duration_seconds REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                owner_id TEXT,
                media_extension TEXT,
                title TEXT,
                meeting_date TEXT,
                agenda TEXT,
                notes TEXT,
                next_meeting_needed TEXT,
                next_meeting_date TEXT,
                next_meeting_topic TEXT,
                transcript TEXT,
                analysis_json TEXT,
                error_message TEXT,
                error_details TEXT,
                processed_at TEXT,
                media_deleted_at TEXT
            )"""
        )
        existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(meetings)")}
        for column, column_type in (
            ("owner_id", "TEXT"),
            ("media_extension", "TEXT"),
            ("title", "TEXT"),
            ("meeting_date", "TEXT"),
            ("agenda", "TEXT"),
            ("notes", "TEXT"),
            ("next_meeting_needed", "TEXT"),
            ("next_meeting_date", "TEXT"),
            ("next_meeting_topic", "TEXT"),
            ("transcript", "TEXT"),
            ("analysis_json", "TEXT"),
            ("error_message", "TEXT"),
            ("error_details", "TEXT"),
            ("processed_at", "TEXT"),
            ("media_deleted_at", "TEXT"),
        ):
            if column not in existing_columns:
                connection.execute(f"ALTER TABLE meetings ADD COLUMN {column} {column_type}")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )"""
        )
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        if "name" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS invitations (
                id TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL UNIQUE,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                last_used_at TEXT
            )"""
        )


def has_active_admin() -> bool:
    if not DATABASE_PATH.exists():
        return False
    with sqlite3.connect(DATABASE_PATH) as connection:
        return connection.execute(
            "SELECT 1 FROM users WHERE role = 'admin' AND is_active = 1 LIMIT 1"
        ).fetchone() is not None


def get_current_user(request: Request) -> dict[str, str | int] | None:
    user_id = request.session.get("user_id")
    if not user_id or not DATABASE_PATH.exists():
        return None
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, email, role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if row is None or not row["is_active"]:
        request.session.clear()
        return None
    return dict(row)


def require_user(request: Request) -> dict[str, str | int]:
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Требуется войти в систему.")
    return user


def require_admin(request: Request) -> dict[str, str | int]:
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Это действие доступно только администратору.")
    return user


def cleanup_expired_media() -> None:
    """Remove expired source videos while preserving transcripts and summaries."""
    if not DATABASE_PATH.exists():
        return
    now = datetime.now(timezone.utc)
    with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, created_at, status, media_extension FROM meetings WHERE media_deleted_at IS NULL"
        ).fetchall()
    for row in rows:
        if str(row["status"]).startswith("processing_"):
            continue
        try:
            created_at = datetime.fromisoformat(row["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if now < created_at.astimezone(timezone.utc) + MEDIA_RETENTION:
            continue
        try:
            meeting_id = str(uuid.UUID(row["id"]))
            extension = row["media_extension"] if row["media_extension"] in {".mp4", ".webm"} else ".mp4"
            (UPLOAD_DIR / f"{meeting_id}{extension}").unlink(missing_ok=True)
        except (OSError, ValueError):
            continue
        with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
            connection.execute(
                "UPDATE meetings SET media_deleted_at = ? WHERE id = ? AND media_deleted_at IS NULL",
                (now.isoformat(), meeting_id),
            )

    temporary_audio = DATA_DIR / "temporary_audio"
    if temporary_audio.exists():
        expiry = now.timestamp() - MEDIA_RETENTION.total_seconds()
        for path in temporary_audio.rglob("*"):
            if path.is_file():
                try:
                    if path.stat().st_mtime < expiry:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
        for path in sorted(temporary_audio.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass


def inspect_video(path: Path) -> tuple[float, str]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise HTTPException(status_code=503, detail="На сервере недоступна проверка видео.")

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=format_name,duration:format_tags=major_brand:stream=codec_type",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        metadata = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        metadata = {}

    media_format = metadata.get("format", {}).get("format_name", "")
    major_brand = metadata.get("format", {}).get("tags", {}).get("major_brand", "").lower()
    formats = media_format.split(",")
    # FFprobe reports MP4-family containers as mov,mp4,m4a,3gp,3g2,mj2.
    mp4_brands = ("mp4", "isom", "iso2", "iso3", "iso4", "iso5", "iso6", "avc1", "dash")
    if "webm" in formats:
        extension = ".webm"
        container_name = "WebM"
    elif "mov" in formats and major_brand.startswith(mp4_brands):
        extension = ".mp4"
        container_name = "MP4"
    else:
        raise HTTPException(status_code=422, detail="Поддерживаются только читаемые видео MP4 и WebM.")

    streams = metadata.get("streams", [])
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise HTTPException(status_code=422, detail=f"В видео {container_name} не найдена видеодорожка.")
    if not any(stream.get("codec_type") == "audio" for stream in streams):
        raise HTTPException(status_code=422, detail="В видео не найдена читаемая звуковая дорожка.")

    try:
        duration = float(metadata["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Не удалось определить длительность видео.") from None

    if duration <= 0:
        raise HTTPException(status_code=422, detail="Не удалось определить длительность видео.")
    if duration > MAX_DURATION_SECONDS:
        raise HTTPException(status_code=422, detail="Длительность видео превышает лимит 10 минут.")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="На сервере недоступна проверка звуковой дорожки.")
    try:
        audio_check = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise HTTPException(status_code=422, detail="Не удалось прочитать видеодорожку или звук.") from None
    if audio_check.returncode != 0:
        raise HTTPException(status_code=422, detail="Не удалось прочитать видеодорожку или звук.")
    return duration, extension


@app.on_event("startup")
def startup() -> None:
    initialize_storage()
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "UPDATE meetings SET status = ?, error_message = ? WHERE status IN (?, ?, ?)",
            (
                "failed_transcription",
                "Обработка была прервана перезапуском сервера. Её можно запустить повторно вручную.",
                "processing_transcription",
                "processing_analysis",
                "processing_audio_extraction",
            ),
        )
    cleanup_expired_media()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> Response:
    if not client_is_loopback(request.scope):
        raise HTTPException(status_code=403, detail="Первоначальная настройка доступна только локально.")
    if has_active_admin():
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(render_setup_page())


@app.post("/setup", response_class=HTMLResponse)
def setup_admin(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> Response:
    if not client_is_loopback(request.scope):
        raise HTTPException(status_code=403, detail="Первоначальная настройка доступна только локально.")
    origin = request.headers.get("origin", "")
    origin_url = urlsplit(origin)
    if origin_url.scheme != request.url.scheme or origin_url.netloc.lower() != request.url.netloc.lower():
        raise HTTPException(status_code=403, detail="Первоначальная настройка требует запроса с этого локального сайта.")
    if has_active_admin():
        return RedirectResponse("/login", status_code=303)
    email = normalize_email(email)
    if not valid_email(email):
        return HTMLResponse(render_setup_page("Введите корректный адрес электронной почты."), status_code=400)
    if len(password) < 8 or len(password.encode("utf-8")) > 1024:
        return HTMLResponse(render_setup_page("Пароль должен содержать не менее 8 символов."), status_code=400)

    user_id = str(uuid.uuid4())
    try:
        with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM users WHERE role = 'admin' AND is_active = 1").fetchone():
                return RedirectResponse("/login", status_code=303)
            connection.execute(
                "INSERT INTO users (id, email, password_hash, role, is_active, created_at) VALUES (?, ?, ?, 'admin', 1, ?)",
                (user_id, email, hash_password(password), datetime.now(timezone.utc).isoformat()),
            )
    except sqlite3.IntegrityError:
        return HTMLResponse(render_setup_page("Не удалось создать учётную запись."), status_code=409)
    request.session.clear()
    request.session["user_id"] = user_id
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if not has_active_admin():
        return RedirectResponse("/setup", status_code=303)
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(render_login_page())


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> Response:
    email = normalize_email(email)
    if len(password.encode("utf-8")) > 1024:
        return HTMLResponse(render_login_page("Неверная почта или пароль."), status_code=401)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, password_hash, is_active FROM users WHERE email = ?", (email,)
        ).fetchone()
    if row is None or not row["is_active"] or not verify_password(password, row["password_hash"]):
        return HTMLResponse(render_login_page("Неверная почта или пароль."), status_code=401)
    request.session.clear()
    request.session["user_id"] = row["id"]
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_page() -> HTMLResponse:
    return HTMLResponse(render_register_page())


@app.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    invite_code: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
) -> Response:
    name = name.strip()
    if not name or len(name) > 120:
        return HTMLResponse(render_register_page("Укажите имя (не более 120 символов)."), status_code=400)
    email = normalize_email(email)
    if not valid_email(email):
        return HTMLResponse(render_register_page("Введите корректный адрес электронной почты."), status_code=400)
    if len(password) < 8 or len(password.encode("utf-8")) > 1024:
        return HTMLResponse(render_register_page("Пароль должен содержать не менее 8 символов."), status_code=400)

    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            invitation = connection.execute(
                "SELECT id FROM invitations WHERE code_hash = ? AND revoked_at IS NULL",
                (invitation_digest(invite_code),),
            ).fetchone()
            if invitation is None:
                return HTMLResponse(render_register_page("Приглашение недействительно или отозвано."), status_code=400)
            connection.execute(
                "INSERT INTO users (id, name, email, password_hash, role, is_active, created_at) VALUES (?, ?, ?, ?, 'user', 1, ?)",
                (user_id, name, email, hash_password(password), now),
            )
            connection.execute("UPDATE invitations SET last_used_at = ? WHERE id = ?", (now, invitation[0]))
    except sqlite3.IntegrityError:
        return HTMLResponse(render_register_page("Этот адрес уже зарегистрирован."), status_code=409)
    request.session.clear()
    request.session["user_id"] = user_id
    return RedirectResponse("/", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> str:
    user = require_user(request)
    cleanup_expired_media()
    return render_dashboard_page(str(user["email"]), user["role"] == "admin")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request) -> str:
    user = require_admin(request)
    return render_admin_page(str(user["email"]))


@app.get("/api/admin/invitations")
def list_invitations(request: Request) -> list[dict[str, object]]:
    require_admin(request)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, created_at, revoked_at, last_used_at FROM invitations ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/admin/invitations", status_code=201)
def create_invitation(request: Request) -> dict[str, str]:
    user = require_admin(request)
    code = secrets.token_urlsafe(18)
    invitation_id = str(uuid.uuid4())
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "INSERT INTO invitations (id, code_hash, created_by, created_at) VALUES (?, ?, ?, ?)",
            (invitation_id, invitation_digest(code), user["id"], datetime.now(timezone.utc).isoformat()),
        )
    return {"id": invitation_id, "code": code}


@app.post("/api/admin/invitations/{invitation_id}/revoke")
def revoke_invitation(invitation_id: str, request: Request) -> dict[str, str]:
    require_admin(request)
    try:
        parsed_id = str(uuid.UUID(invitation_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Приглашение не найдено.") from None
    with sqlite3.connect(DATABASE_PATH) as connection:
        cursor = connection.execute(
            "UPDATE invitations SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), parsed_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Активное приглашение не найдено.")
    return {"status": "revoked"}


@app.get("/api/admin/users")
def list_users(request: Request) -> list[dict[str, object]]:
    require_admin(request)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, email, role, is_active, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/admin/users/{user_id}/active")
def set_user_active(user_id: str, request: Request, active: bool = Form(...)) -> dict[str, object]:
    require_admin(request)
    try:
        parsed_id = str(uuid.UUID(user_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Пользователь не найден.") from None
    with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        target = connection.execute("SELECT role FROM users WHERE id = ?", (parsed_id,)).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")
        if not active and target[0] == "admin":
            raise HTTPException(status_code=409, detail="Учётные записи администратора нельзя отключить этим действием.")
        connection.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(active), parsed_id))
    return {"id": parsed_id, "is_active": active}


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request) -> str:
    require_user(request)
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Загрузка встречи</title><style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#19251f;background:#f4f6f2;font-synthesis:none}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 12% 8%,#e3eee1 0,transparent 36%),#f4f6f2}
header{height:72px;border-bottom:1px solid #e4e9e2;display:flex;align-items:center;padding:0 max(24px,calc((100% - 1120px)/2));background:#ffffffb8}
.brand{font-size:15px;font-weight:700;letter-spacing:-.02em}.brand i{display:inline-grid;place-items:center;width:29px;height:29px;border-radius:9px;background:#1e6849;color:white;font-style:normal;margin-right:10px}
main{max-width:1120px;margin:70px auto;padding:0 24px;display:grid;grid-template-columns:1fr 420px;gap:84px;align-items:center}
.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.13em;font-weight:700;color:#448061}.copy h1{font-size:48px;line-height:1.08;letter-spacing:-.045em;margin:18px 0}.copy p{font-size:17px;line-height:1.65;color:#607067;max-width:520px;margin:0}
.checks{display:flex;gap:9px;flex-wrap:wrap;margin-top:30px}.checks span{font-size:12px;color:#59675e;background:#e9eee7;border-radius:999px;padding:9px 12px}
.card{background:white;border:1px solid #e3e8e1;border-radius:20px;padding:26px;box-shadow:0 18px 55px #253f2710}.card h2{font-size:19px;margin:0 0 7px;letter-spacing:-.02em}.hint{color:#77847b;font-size:13px;margin:0 0 21px}
.drop{min-height:190px;border:1.5px dashed #bdc9bf;border-radius:14px;background:#fafcf9;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:20px;transition:.15s}.drop.over{border-color:#39845e;background:#f2f8f3}.drop.disabled{opacity:.55;pointer-events:none}
.icon{width:42px;height:42px;border-radius:13px;background:#e8f2e9;color:#38734f;display:grid;place-items:center;font-size:21px;margin-bottom:12px}.drop strong{font-size:14px}.drop small{font-size:12px;color:#829087;margin-top:7px}input{display:none}
button,.choose{font:inherit;border:0;cursor:pointer}.choose{margin-top:16px;color:#287148;background:white;border:1px solid #dce7de;border-radius:9px;padding:9px 13px;font-size:12px;font-weight:650}
.fileline{display:none;margin-top:14px;padding:12px;background:#f4f7f3;border-radius:11px;align-items:center;gap:11px}.filebadge{width:34px;height:34px;background:#e4eee6;border-radius:9px;display:grid;place-items:center;color:#39714d;font-size:11px;font-weight:800}.filemeta{min-width:0;flex:1}.filemeta strong{font-size:12px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.filemeta small{font-size:11px;color:#7c8880}.remove{background:none;color:#829087;font-size:18px;padding:4px}
.progress{display:none;margin-top:15px}.progressrow{display:flex;justify-content:space-between;font-size:11px;color:#67766b;margin-bottom:8px}.track{height:6px;border-radius:8px;background:#edf1ec;overflow:hidden}.fill{height:100%;width:0;background:#3a8a5c;transition:width .15s}
.message{display:none;margin-top:14px;padding:12px;border-radius:10px;font-size:12px;line-height:1.5}.message.error{display:block;background:#fff2ef;color:#9c4339}.message.info{display:block;background:#eef4ff;color:#345885}.message.success{display:block;background:#edf7ee;color:#276440}.message a{color:inherit;font-weight:700}.back{font-size:13px;color:#287148;text-decoration:none}.cancel{width:100%;padding:11px;border:1px solid #dce7de;border-radius:10px;background:white;color:#47594c;font-size:13px;font-weight:650;margin-top:9px;cursor:pointer}
.consent-row{display:flex;gap:9px;align-items:flex-start;margin-top:16px;font-size:11px;line-height:1.5;color:#59675e}.consent-row input{display:block;flex:0 0 auto;width:15px;height:15px;margin:1px 0 0;accent-color:#246b49}.consent-row a{color:#287148}
@media(max-width:800px){main{grid-template-columns:1fr;gap:35px;margin:48px auto}.copy h1{font-size:38px}.card{max-width:520px;width:100%}}
</style></head><body><header><a href="/" class="brand"><i>М</i>Встречи · Все встречи</a></header>
<main><section class="copy"><a href="/" class="back">← Назад к встречам</a><div class="eyebrow" style="margin-top:32px">Meeting analysis</div><h1>Вернитесь к сути<br>вашей встречи</h1><p>Загрузите запись, чтобы подготовить её к обработке. Сначала мы проверим формат, длительность и наличие звука.</p><div class="checks"><span>MP4</span><span>WebM</span><span>До 25 МБ</span><span>До 10 минут</span></div></section>
<section class="card"><h2>Загрузить видео</h2><p class="hint">Выберите файл с записью встречи — загрузка начнётся автоматически после согласия</p>
<label class="drop" id="drop" for="file"><span class="icon">↑</span><strong>Перетащите видео сюда</strong><small>Файл MP4 или WebM, не больше 25 МБ</small><span class="choose">Выбрать файл</span></label><input id="file" type="file" accept="video/mp4,video/webm,.mp4,.webm">
<div class="fileline" id="fileline"><span class="filebadge" id="filebadge">ВИДЕО</span><span class="filemeta"><strong id="filename"></strong><small id="filesize"></small></span><button class="remove" id="remove" aria-label="Убрать файл">×</button></div>
<div class="progress" id="progress"><div class="progressrow"><span id="progresslabel">Загрузка файла</span><span id="percent">0%</span></div><div class="track"><div class="fill" id="fill"></div></div></div>
<label class="consent-row"><input id="cloudConsent" type="checkbox" required><span>После загрузки обработка начнётся автоматически: аудио и расшифровка будут переданы в облачный AI-сервис. Запросы могут тарифицироваться провайдером.</span></label>
<button class="cancel" id="cancel" type="button" hidden>Отменить загрузку</button><div class="message" id="message" role="status" aria-live="polite"></div>
</section></main>
<script>
const input=document.querySelector('#file'),drop=document.querySelector('#drop'),fileline=document.querySelector('#fileline'),cancel=document.querySelector('#cancel'),message=document.querySelector('#message'),cloudConsent=document.querySelector('#cloudConsent'),removeFile=document.querySelector('#remove');let selected=null,activeXhr=null;
function formatSize(n){return n<1048576?`${Math.ceil(n/1024)} КБ`:`${(n/1048576).toFixed(1)} МБ`}
function updateControls(uploading){input.disabled=uploading;cloudConsent.disabled=uploading;removeFile.disabled=uploading;drop.classList.toggle('disabled',uploading)}
function showError(text,clearSelection=false){message.className='message error';message.textContent=text;document.querySelector('#progress').style.display='none';if(clearSelection){selected=null;fileline.style.display='none';input.value=''}}
function setFile(file){if(activeXhr)return;message.className='message';message.textContent='';if(!file){selected=null;fileline.style.display='none';input.value='';return}if(file.size>25*1024*1024){showError('Размер файла превышает лимит 25 МБ.',true);return}selected=file;document.querySelector('#filename').textContent=file.name;document.querySelector('#filesize').textContent=formatSize(file.size);document.querySelector('#filebadge').textContent=file.name.toLowerCase().endsWith('.webm')?'WEBM':'MP4';fileline.style.display='flex';if(cloudConsent.checked)startUpload();else{message.className='message info';message.textContent='Отметьте согласие — загрузка начнётся автоматически.'}}
function startUpload(){if(!selected||!cloudConsent.checked||activeXhr)return;const file=selected,form=new FormData();form.append('file',file);form.append('cloud_processing_consent','true');cancel.hidden=false;message.className='message';document.querySelector('#progress').style.display='block';document.querySelector('#progresslabel').textContent='Загрузка файла';updateControls(true);const xhr=new XMLHttpRequest();activeXhr=xhr;xhr.open('POST','/api/meetings/upload');xhr.upload.onprogress=e=>{if(e.lengthComputable){const pct=Math.round(e.loaded/e.total*100);document.querySelector('#fill').style.width=pct+'%';document.querySelector('#percent').textContent=pct+'%'}};xhr.onload=()=>{activeXhr=null;cancel.hidden=true;let body={};try{body=JSON.parse(xhr.responseText)}catch{}if(xhr.status>=200&&xhr.status<300){document.querySelector('#progresslabel').textContent='Запускаем обработку';document.querySelector('#fill').style.width='100%';document.querySelector('#percent').textContent='100%';location.href='/'}else{updateControls(false);showError(body.detail||'Не удалось загрузить видео. Выберите файл ещё раз и повторите попытку.',true)}};xhr.onerror=()=>{activeXhr=null;cancel.hidden=true;updateControls(false);showError('Не удалось связаться с сервером. Проверьте подключение и выберите файл ещё раз.',true)};xhr.onabort=()=>{activeXhr=null;cancel.hidden=true;updateControls(false);document.querySelector('#fill').style.width='0';document.querySelector('#percent').textContent='0%';showError('Загрузка отменена. Выберите файл ещё раз, чтобы начать заново.',true)};xhr.send(form)}
input.addEventListener('change',()=>setFile(input.files[0]));removeFile.onclick=()=>setFile(null);
for(const name of ['dragenter','dragover'])drop.addEventListener(name,e=>{e.preventDefault();drop.classList.add('over')});for(const name of ['dragleave','drop'])drop.addEventListener(name,e=>{e.preventDefault();drop.classList.remove('over')});drop.addEventListener('drop',e=>setFile(e.dataTransfer.files[0]));
cloudConsent.addEventListener('change',()=>{if(cloudConsent.checked&&selected)startUpload()});cancel.addEventListener('click',()=>{if(activeXhr)activeXhr.abort()});</script></body></html>"""


@app.post("/api/meetings/upload", status_code=201)
async def upload_meeting(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    cloud_processing_consent: bool = Form(...),
) -> dict[str, object]:
    # AuthGateMiddleware rejects anonymous requests before FastAPI parses this multipart body.
    user = require_user(request)
    if not cloud_processing_consent:
        raise HTTPException(status_code=422, detail="Подтвердите согласие на автоматическую облачную обработку.")
    cleanup_expired_media()
    if not file.filename:
        raise HTTPException(status_code=422, detail="Не удалось прочитать имя загруженного файла.")

    meeting_id = str(uuid.uuid4())
    staging_path = UPLOAD_DIR / f"{meeting_id}.upload"
    destination: Path | None = None
    size = 0
    try:
        with staging_path.open("xb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise HTTPException(status_code=413, detail="The video exceeds the 25 MB limit.")
                target.write(chunk)

        if size == 0:
            raise HTTPException(status_code=422, detail="The selected file is empty.")

        duration, extension = inspect_video(staging_path)
        if await request.is_disconnected():
            raise HTTPException(status_code=499, detail="Загрузка отменена.")
        destination = UPLOAD_DIR / f"{meeting_id}{extension}"
        staging_path.replace(destination)
        created_at = datetime.now(timezone.utc).isoformat()
        initialize_storage()
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.execute(
                "INSERT INTO meetings (id, filename, size_bytes, duration_seconds, status, created_at, owner_id, media_extension, title, meeting_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (meeting_id, Path(file.filename).name, size, duration, "processing_audio_extraction", created_at, user["id"], extension, Path(file.filename).name, created_at[:10]),
            )
        background_tasks.add_task(process_meeting, meeting_id, DATA_DIR)
        return {
            "id": meeting_id,
            "filename": Path(file.filename).name,
            "size_bytes": size,
            "duration_seconds": duration,
            "status": "processing_audio_extraction",
            "created_at": created_at,
        }
    except Exception:
        staging_path.unlink(missing_ok=True)
        if destination is not None:
            destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.get("/api/meetings")
def list_meetings(request: Request) -> list[dict[str, object]]:
    user = require_user(request)
    cleanup_expired_media()
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        if user["role"] == "admin":
            rows = connection.execute(
                """SELECT m.id, m.filename, COALESCE(NULLIF(m.title, ''), m.filename) AS title,
                          m.meeting_date, m.agenda, m.analysis_json, m.size_bytes, m.duration_seconds,
                          m.status, m.created_at, m.media_deleted_at, m.error_message,
                          COALESCE(NULLIF(u.name, ''), u.email, '—') AS owner_name
                   FROM meetings m LEFT JOIN users u ON u.id = m.owner_id ORDER BY m.created_at DESC"""
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, filename, COALESCE(NULLIF(title, ''), filename) AS title, meeting_date, agenda, analysis_json, size_bytes, duration_seconds, status, created_at, media_deleted_at, error_message FROM meetings WHERE owner_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
    items: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        raw_analysis = item.pop("analysis_json")
        agenda = item.pop("agenda") or ""
        try:
            analysis = json.loads(raw_analysis) if raw_analysis else {}
        except (TypeError, json.JSONDecodeError):
            analysis = {}
        if not isinstance(analysis, dict):
            analysis = {}
        topics = analysis.get("topics") if isinstance(analysis.get("topics"), list) else []
        first_topic = topics[0].get("topic") if topics and isinstance(topics[0], dict) else ""
        agenda_line = str(agenda).strip().splitlines()[0] if str(agenda).strip() else ""
        item["topic"] = first_topic or agenda_line or None
        item["key_results"] = {
            "summary": len(analysis.get("summary_points", [])) if isinstance(analysis.get("summary_points"), list) else 0,
            "decisions": len(analysis.get("decisions", [])) if isinstance(analysis.get("decisions"), list) else 0,
            "tasks": len(analysis.get("tasks", [])) if isinstance(analysis.get("tasks"), list) else 0,
            "questions": len(analysis.get("open_questions", [])) if isinstance(analysis.get("open_questions"), list) else 0,
            "errors": int(str(item["status"]).startswith("failed_")),
        }
        items.append(item)
    return items


@app.get("/api/meetings/summary")
def meeting_summary(request: Request) -> dict[str, int]:
    user = require_user(request)
    with sqlite3.connect(DATABASE_PATH) as connection:
        if user["role"] == "admin":
            rows = connection.execute("SELECT status, analysis_json FROM meetings").fetchall()
        else:
            rows = connection.execute(
                "SELECT status, analysis_json FROM meetings WHERE owner_id = ?", (user["id"],)
            ).fetchall()

    totals = {"processed_meetings": 0, "decisions": 0, "tasks": 0, "open_questions": 0, "errors": 0}
    for status, analysis_json in rows:
        if status == "completed":
            totals["processed_meetings"] += 1
        if str(status).startswith("failed_"):
            totals["errors"] += 1
        if not analysis_json:
            continue
        try:
            analysis = json.loads(analysis_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(analysis, dict):
            for field in ("decisions", "tasks", "open_questions"):
                items = analysis.get(field)
                if isinstance(items, list):
                    totals[field] += len(items)
    return totals


@app.get("/api/meetings/{meeting_id}")
def get_meeting(meeting_id: str, request: Request) -> dict[str, object]:
    user = require_user(request)
    cleanup_expired_media()
    try:
        parsed_id = str(uuid.UUID(meeting_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Meeting not found.") from None

    if not DATABASE_PATH.exists():
        raise HTTPException(status_code=404, detail="Meeting not found.")
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, filename, title, meeting_date, agenda, notes, next_meeting_needed, next_meeting_date, next_meeting_topic, size_bytes, duration_seconds, status, created_at, transcript, analysis_json, error_message, error_details, processed_at, media_deleted_at, owner_id FROM meetings WHERE id = ?",
            (parsed_id,),
        ).fetchone()
    if row is None or (user["role"] != "admin" and row["owner_id"] != user["id"]):
        raise HTTPException(status_code=404, detail="Meeting not found.")
    meeting = dict(row)
    meeting.pop("owner_id")
    analysis_json = meeting.pop("analysis_json")
    meeting["title"] = meeting["title"] or meeting["filename"]
    meeting["analysis"] = json.loads(analysis_json) if analysis_json else None
    return meeting


@app.get("/api/meetings/{meeting_id}/logs")
def get_meeting_logs(meeting_id: str, request: Request) -> dict[str, object]:
    """Return sanitized diagnostics for an owned meeting, never process-wide logs."""
    user = require_user(request)
    try:
        parsed_id = str(uuid.UUID(meeting_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Meeting not found.") from None
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT status, error_message, error_details, owner_id FROM meetings WHERE id = ?",
            (parsed_id,),
        ).fetchone()
    if row is None or (user["role"] != "admin" and row["owner_id"] != user["id"]):
        raise HTTPException(status_code=404, detail="Meeting not found.")
    try:
        details = json.loads(row["error_details"]) if row["error_details"] else None
    except (TypeError, json.JSONDecodeError):
        details = None
    return {"status": row["status"], "message": row["error_message"], "details": details}


def _required_text(payload: dict[str, object], key: str, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or len(value) > max_length:
        raise HTTPException(status_code=422, detail=f"Поле «{key}» заполнено некорректно.")
    return value.strip()


def _optional_date(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"Поле «{key}» должно содержать дату.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Поле «{key}» содержит некорректную дату.") from None


def _clean_analysis(value: object) -> dict[str, list[dict[str, str]]]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="Результаты обработки имеют неверный формат.")
    fields = {
        "summary_points": "point",
        "topics": "topic",
        "decisions": "decision",
        "tasks": "action",
        "open_questions": "question",
    }
    cleaned: dict[str, list[dict[str, str]]] = {}
    for group, primary in fields.items():
        items = value.get(group, [])
        if not isinstance(items, list) or len(items) > 100:
            raise HTTPException(status_code=422, detail="Проверьте список результатов встречи.")
        cleaned[group] = []
        keys = (primary, "responsible", "deadline", "evidence") if group == "tasks" else (primary, "evidence")
        for item in items:
            if not isinstance(item, dict):
                raise HTTPException(status_code=422, detail="Проверьте формат результатов встречи.")
            clean_item: dict[str, str] = {}
            for key in keys:
                text = item.get(key, "")
                if not isinstance(text, str) or len(text) > 5000:
                    raise HTTPException(status_code=422, detail="Один из пунктов результата слишком длинный.")
                clean_item[key] = text.strip()
            if clean_item[primary]:
                cleaned[group].append(clean_item)
    return cleaned


@app.put("/api/meetings/{meeting_id}")
async def update_meeting(meeting_id: str, request: Request) -> dict[str, object]:
    user = require_user(request)
    try:
        parsed_id = str(uuid.UUID(meeting_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Встреча не найдена.") from None
    try:
        payload = await request.json()
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Не удалось прочитать изменения.") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Изменения встречи имеют неверный формат.")

    title = _required_text(payload, "title", 250)
    if not title:
        raise HTTPException(status_code=422, detail="Укажите название встречи.")
    meeting_date = _optional_date(payload, "meeting_date")
    agenda = _required_text(payload, "agenda", 20000)
    notes = _required_text(payload, "notes", 20000)
    next_meeting_needed = payload.get("next_meeting_needed")
    if not isinstance(next_meeting_needed, str) or next_meeting_needed not in {"yes", "no", "unspecified"}:
        raise HTTPException(status_code=422, detail="Укажите, нужна ли следующая встреча.")
    next_meeting_date = _optional_date(payload, "next_meeting_date")
    next_meeting_topic = _required_text(payload, "next_meeting_topic", 5000)
    transcript = _required_text(payload, "transcript", 100000)
    analysis = _clean_analysis(payload.get("analysis"))

    with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT owner_id, status FROM meetings WHERE id = ?", (parsed_id,)).fetchone()
        if row is None or (user["role"] != "admin" and row["owner_id"] != user["id"]):
            raise HTTPException(status_code=404, detail="Встреча не найдена.")
        if str(row["status"]).startswith("processing_"):
            raise HTTPException(status_code=409, detail="Нельзя менять встречу во время обработки.")
        connection.execute(
            """UPDATE meetings SET title = ?, meeting_date = ?, agenda = ?, notes = ?,
               next_meeting_needed = ?, next_meeting_date = ?, next_meeting_topic = ?,
               transcript = ?, analysis_json = ? WHERE id = ?""",
            (title, meeting_date, agenda, notes, next_meeting_needed, next_meeting_date,
             next_meeting_topic, transcript, json.dumps(analysis, ensure_ascii=False), parsed_id),
        )
    return {"id": parsed_id, "status": "saved"}


@app.post("/api/meetings/{meeting_id}/process", status_code=202)
def start_processing(meeting_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    user = require_user(request)
    cleanup_expired_media()
    try:
        parsed_id = str(uuid.UUID(meeting_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Meeting not found.") from None

    with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT status, transcript, owner_id, media_extension FROM meetings WHERE id = ?", (parsed_id,)).fetchone()
        if row is None or (user["role"] != "admin" and row["owner_id"] != user["id"]):
            raise HTTPException(status_code=404, detail="Meeting not found.")
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise HTTPException(status_code=503, detail="AI-провайдер не настроен. Проверьте локальный файл .env.")
        allowed_statuses = {"uploaded", "failed_transcription", "failed_analysis"}
        if row["status"] not in allowed_statuses:
            raise HTTPException(status_code=409, detail="Эту встречу сейчас нельзя запустить повторно.")
        extension = row["media_extension"] if row["media_extension"] in {".mp4", ".webm"} else ".mp4"
        if not (UPLOAD_DIR / f"{parsed_id}{extension}").is_file():
            raise HTTPException(status_code=409, detail="Исходный видеофайл недоступен для обработки.")
        status = "processing_analysis" if row["transcript"] else "processing_audio_extraction"
        connection.execute(
            "UPDATE meetings SET status = ?, error_message = NULL, error_details = NULL WHERE id = ?",
            (status, parsed_id),
        )

    background_tasks.add_task(process_meeting, parsed_id, DATA_DIR)
    return {"id": parsed_id, "status": status}


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: str, request: Request) -> dict[str, str]:
    require_admin(request)
    try:
        parsed_id = str(uuid.UUID(meeting_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Встреча не найдена.") from None

    with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
        row = connection.execute("SELECT status, media_extension FROM meetings WHERE id = ?", (parsed_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Встреча не найдена.")
    if str(row[0]).startswith("processing_"):
        raise HTTPException(status_code=409, detail="Нельзя удалить встречу во время обработки.")

    extension = row[1] if row[1] in {".mp4", ".webm"} else ".mp4"
    video_path = UPLOAD_DIR / f"{parsed_id}{extension}"
    try:
        video_path.unlink(missing_ok=True)
        temp_root = DATA_DIR / "temporary_audio"
        if temp_root.exists():
            for path in temp_root.iterdir():
                if path.name.startswith(f"meeting-{parsed_id}-") and path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
    except OSError:
        raise HTTPException(status_code=500, detail="Не удалось удалить все файлы встречи. Повторите попытку.") from None

    with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
        connection.execute("DELETE FROM meetings WHERE id = ?", (parsed_id,))
    return {"status": "deleted"}


@app.get("/meetings/{meeting_id}", response_class=HTMLResponse)
def meeting_page(request: Request, meeting_id: str) -> str:
    user = require_user(request)
    try:
        meeting = get_meeting(meeting_id, request)
    except HTTPException as error:
        raise error
    safe_id = str(uuid.UUID(meeting_id))
    admin_button = '<button id="deleteMeeting" class="secondary">Удалить встречу</button>' if user["role"] == "admin" else ""
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Встреча — обработка записи</title>
<style>
:root{{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#19251f;background:#f4f6f2}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(ellipse at 12% 8%,#e3eee1 0,transparent 36%),#f4f6f2;min-height:100vh}}
header{{height:72px;border-bottom:1px solid #e4e9e2;display:flex;align-items:center;padding:0 max(24px,calc((100% - 920px)/2));background:#ffffffb8}}.brand{{font-size:15px;font-weight:700}}.brand i{{display:inline-grid;place-items:center;width:29px;height:29px;border-radius:9px;background:#1e6849;color:white;font-style:normal;margin-right:10px}}
main{{max-width:920px;margin:48px auto;padding:0 24px 60px}}a{{color:#287148;text-decoration:none;font-size:13px}}h1{{font-size:34px;letter-spacing:-.035em;margin:20px 0 8px}}.meta{{color:#78847b;font-size:13px;margin-bottom:25px}}.card{{background:#fff;border:1px solid #e3e8e1;border-radius:16px;padding:23px;margin-top:16px;box-shadow:0 12px 38px #253f2709}}.card h2{{font-size:17px;margin:0 0 13px;letter-spacing:-.02em}}
.notice{{background:#f3f7f0;border-left:3px solid #6b9d71;padding:13px 15px;font-size:13px;line-height:1.6;color:#536357;border-radius:0 9px 9px 0}}.consent{{display:flex;gap:10px;align-items:flex-start;margin-top:15px;font-size:12px;line-height:1.5;color:#59675e}}.consent input{{margin-top:3px;accent-color:#246b49}}button{{margin-top:16px;border:0;border-radius:9px;padding:12px 17px;background:#246b49;color:#fff;font-weight:700;cursor:pointer}}button.secondary{{background:#edf2ec;color:#45574b}}button:disabled{{opacity:.45;cursor:not-allowed}}
.status{{padding:13px 15px;border-radius:10px;background:#edf2ec;color:#516257;font-size:13px;line-height:1.5}}.status.ok{{background:#edf7ee;color:#276440}}.status.fail{{background:#fff2ef;color:#9c4339}}.error{{color:#9c4339;font-size:13px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.result-section{{background:white;border:1px solid #e6ebe4;border-radius:12px;padding:17px;margin-top:14px}}.result-section h3{{font-size:15px;margin:0 0 11px}}.result-item{{font-size:13px;line-height:1.6;margin:0 0 12px}}blockquote{{margin:5px 0 0;padding-left:10px;border-left:2px solid #d9e5d9;color:#77847b;font-size:12px}}.empty{{font-size:13px;color:#78847b;font-style:italic}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:13px;line-height:1.7;color:#3c4b40;margin:0}}.transcript{{max-height:460px;overflow:auto}}.muted{{color:#78847b;font-size:12px}}
.edit-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.edit-field{{display:flex;flex-direction:column;gap:6px;color:#56645a;font-size:12px;font-weight:650}}.edit-field.full{{grid-column:1/-1}}.edit-field input,.edit-field select,.edit-field textarea{{width:100%;font:inherit;font-size:13px;font-weight:400;color:#25352b;border:1px solid #dce4dc;border-radius:9px;padding:10px;background:#fcfdfb}}.edit-field textarea{{min-height:88px;resize:vertical;line-height:1.5}}.edit-field textarea.tall{{min-height:170px}}.edit-help{{font-size:11px;color:#7a867d;font-weight:400}}.save-row{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}#saveState{{font-size:12px;color:#657469}}#saveState.error{{color:#9c4339}}
@media(max-width:650px){{main{{margin-top:28px}}h1{{font-size:27px}}.grid,.edit-grid{{grid-template-columns:1fr}}.edit-field.full{{grid-column:auto}}.card{{padding:18px}}}}
</style></head><body><header><div class="brand"><i>М</i>Встречи</div></header><main>
<a href="/">← Все встречи</a><h1 id="title">Встреча</h1>{admin_button}<div class="meta" id="meta"></div>
<section class="card" id="editor"><h2>Данные встречи и результаты</h2><div class="edit-grid">
<label class="edit-field">Название встречи<input id="editTitle" maxlength="250"></label><label class="edit-field">Дата встречи<input id="editMeetingDate" type="date"></label>
<label class="edit-field full">Повестка<textarea id="editAgenda" maxlength="20000"></textarea></label><label class="edit-field full">Заметки и выводы<textarea id="editNotes" maxlength="20000"></textarea></label>
<label class="edit-field">Нужна ли следующая встреча<select id="editNextNeeded"><option value="unspecified">Не указано</option><option value="yes">Да</option><option value="no">Нет</option></select></label><label class="edit-field">Дата следующей встречи<input id="editNextDate" type="date"></label>
<label class="edit-field full">Тема следующей встречи<input id="editNextTopic" maxlength="5000"></label>
<label class="edit-field full">Расшифровка<textarea class="tall" id="editTranscript" maxlength="100000"></textarea></label>
<label class="edit-field">Краткое содержание<textarea id="editSummary"></textarea><span class="edit-help">Каждый пункт — с новой строки; цитату можно добавить после |</span></label>
<label class="edit-field">Основные темы<textarea id="editTopics"></textarea><span class="edit-help">Каждая тема — с новой строки; цитату можно добавить после |</span></label>
<label class="edit-field">Решения<textarea id="editDecisions"></textarea><span class="edit-help">Каждое решение — с новой строки; цитату можно добавить после |</span></label>
<label class="edit-field">Открытые вопросы<textarea id="editQuestions"></textarea><span class="edit-help">Каждый вопрос — с новой строки; цитату можно добавить после |</span></label>
<label class="edit-field full">Задачи<textarea id="editTasks"></textarea><span class="edit-help">Каждая задача — с новой строки: действие | ответственный | срок | подтверждающая цитата</span></label>
</div><div class="save-row"><button id="saveEdits" type="button">Сохранить изменения</button><span id="saveState" role="status"></span></div></section>
<section class="card" id="actionCard"><h2>Передача аудио для обработки</h2><div class="notice">Для распознавания аудио из видео и анализа его расшифровки данные будут отправлены в OpenAI API. Запросы оплачиваются по тарифам API; точную сумму перед запуском приложение не рассчитывает. Аудиофайл после запроса удаляется из временного каталога приложения.</div><label class="consent"><input type="checkbox" id="consent"><span>Понимаю, что аудио и его расшифровка будут переданы облачному AI-провайдеру, и подтверждаю запуск обработки.</span></label><button id="process" disabled>Начать обработку</button><p class="muted">При сбое можно запустить повтор вручную. Если расшифровка уже сохранена, повторится только анализ.</p></section>
<div class="status" id="status">Загружаем статус встречи…</div><p class="error" id="error"></p>
<section id="results" hidden><div class="grid"><div class="result-section"><h3>Краткое содержание</h3><div id="summary"></div></div><div class="result-section"><h3>Основные темы</h3><div id="topics"></div></div><div class="result-section"><h3>Решения</h3><div id="decisions"></div></div><div class="result-section"><h3>Задачи</h3><div id="tasks"></div></div><div class="result-section"><h3>Открытые вопросы</h3><div id="questions"></div></div></div><div class="card"><h2>Расшифровка речи</h2><div class="transcript"><pre id="transcript"></pre></div></div></section>
</main><script>
const id='{safe_id}', isAdmin={str(user['role'] == 'admin').lower()}, statusBox=document.querySelector('#status'), errorBox=document.querySelector('#error'), processButton=document.querySelector('#process'), consent=document.querySelector('#consent'), actionCard=document.querySelector('#actionCard');let pollTimer=null;
let editorDirty=false;const saveButton=document.querySelector('#saveEdits'),saveState=document.querySelector('#saveState');
const labels={{uploaded:'Видео загружено и проверено.',processing_audio_extraction:'Извлекаем аудио из видео…',processing_transcription:'Распознаём речь…',processing_analysis:'AI-анализ встречи…',completed:'Обработка завершена.',failed_transcription:'Не удалось получить расшифровку.',failed_analysis:'Расшифровка сохранена, но анализ не завершился. Повтор обработает сохранённый текст.',idle:'Загружаем статус встречи…'}};
function node(tag,text,className){{const element=document.createElement(tag);if(text)element.textContent=text;if(className)element.className=className;return element}}
function renderList(target,items,key,empty){{const root=document.querySelector(target);root.replaceChildren();if(!Array.isArray(items)||!items.length){{root.append(node('p',empty,'empty'));return}}for(const item of items){{const block=node('p',item[key],'result-item');if(item.evidence)block.append(node('blockquote',`«${{item.evidence}}»`));root.append(block)}}}}
function renderTasks(items){{const root=document.querySelector('#tasks');root.replaceChildren();if(!Array.isArray(items)||!items.length){{root.append(node('p','Не обнаружено по расшифровке.','empty'));return}}for(const task of items){{const block=node('p',task.action,'result-item');const details=[task.responsible&&`Ответственный: ${{task.responsible}}`,task.deadline&&`Срок: ${{task.deadline}}`].filter(Boolean).join(' · ');if(details)block.append(node('div',details,'muted'));if(task.evidence)block.append(node('blockquote',`«${{task.evidence}}»`));root.append(block)}}}}
function editorItems(items,key){{return Array.isArray(items)?items.map(item=>String(item[key]||'')+(item.evidence?' | '+item.evidence:'')).join('\\n'):''}}
function parseEditorItems(text,key){{return text.split(/\\r?\\n/).map(line=>line.trim()).filter(Boolean).map(line=>{{const [primary,...evidence]=line.split('|');return {{[key]:primary.trim(),evidence:evidence.join('|').trim()}}}})}}
function fillEditor(meeting){{const values={{editTitle:meeting.title||meeting.filename,editMeetingDate:meeting.meeting_date||'',editAgenda:meeting.agenda||'',editNotes:meeting.notes||'',editNextNeeded:meeting.next_meeting_needed||'unspecified',editNextDate:meeting.next_meeting_date||'',editNextTopic:meeting.next_meeting_topic||'',editTranscript:meeting.transcript||''}};for(const [key,value] of Object.entries(values))document.querySelector('#'+key).value=value;const a=meeting.analysis||{{}};document.querySelector('#editSummary').value=editorItems(a.summary_points,'point');document.querySelector('#editTopics').value=editorItems(a.topics,'topic');document.querySelector('#editDecisions').value=editorItems(a.decisions,'decision');document.querySelector('#editQuestions').value=editorItems(a.open_questions,'question');document.querySelector('#editTasks').value=Array.isArray(a.tasks)?a.tasks.map(task=>[task.action||'',task.responsible||'',task.deadline||'',task.evidence||''].join(' | ')).join('\\n'):''}}
function currentEdits(){{const value=id=>document.querySelector('#'+id).value;const tasks=value('editTasks').split(/\\r?\\n/).map(line=>line.trim()).filter(Boolean).map(line=>{{const [action='',responsible='',deadline='',...evidence]=line.split('|');return {{action:action.trim(),responsible:responsible.trim(),deadline:deadline.trim(),evidence:evidence.join('|').trim()}}}});return {{title:value('editTitle'),meeting_date:value('editMeetingDate'),agenda:value('editAgenda'),notes:value('editNotes'),next_meeting_needed:value('editNextNeeded'),next_meeting_date:value('editNextDate'),next_meeting_topic:value('editNextTopic'),transcript:value('editTranscript'),analysis:{{summary_points:parseEditorItems(value('editSummary'),'point'),topics:parseEditorItems(value('editTopics'),'topic'),decisions:parseEditorItems(value('editDecisions'),'decision'),open_questions:parseEditorItems(value('editQuestions'),'question'),tasks}}}}}}
document.querySelector('#editor').addEventListener('input',()=>{{editorDirty=true;saveState.textContent='Есть несохранённые изменения';saveState.className=''}});
async function refresh(){{try{{const response=await fetch(`/api/meetings/${{id}}`);if(!response.ok)throw new Error('Не удалось загрузить встречу.');const meeting=await response.json();document.querySelector('#title').textContent=meeting.title||meeting.filename;const size=(meeting.size_bytes/1048576).toFixed(1),duration=Math.floor(meeting.duration_seconds),time=`${{Math.floor(duration/60)}}:${{String(duration%60).padStart(2,'0')}}`;document.querySelector('#meta').textContent=`${{size}} МБ · ${{time}}${{meeting.meeting_date?' · '+meeting.meeting_date:''}}`;
const active=meeting.status.startsWith('processing_'), failed=meeting.status.startsWith('failed_');if(!editorDirty)fillEditor(meeting);document.querySelectorAll('#editor input,#editor select,#editor textarea').forEach(field=>field.disabled=active);saveButton.disabled=active;statusBox.textContent=labels[meeting.status]||'Состояние встречи неизвестно.';statusBox.className='status '+(meeting.status==='completed'?'ok':failed?'fail':'');errorBox.textContent=meeting.error_message||(meeting.media_deleted_at?'Исходное видео удалено по сроку хранения. Сохранённые текстовые результаты доступны ниже.':'');actionCard.hidden=meeting.media_deleted_at||!(meeting.status==='uploaded'||failed);if(meeting.media_deleted_at)statusBox.textContent='Исходное видео удалено через 24 часа; текстовые результаты сохранены.';processButton.textContent=failed?'Повторить обработку':'Начать обработку';processButton.disabled=active||!consent.checked;
if(active&&!pollTimer)pollTimer=setInterval(refresh,2500);if(!active&&pollTimer){{clearInterval(pollTimer);pollTimer=null}}
if(meeting.status==='completed'&&meeting.analysis){{document.querySelector('#results').hidden=false;renderList('#summary',meeting.analysis.summary_points,'point','Не удалось подготовить подтверждённое содержание.');renderList('#topics',meeting.analysis.topics,'topic','Не удалось определить темы.');renderList('#decisions',meeting.analysis.decisions,'decision','Решения не обнаружены по расшифровке.');renderTasks(meeting.analysis.tasks);renderList('#questions',meeting.analysis.open_questions,'question','Открытые вопросы не обнаружены по расшифровке.');document.querySelector('#transcript').textContent=meeting.transcript||''}}
}}catch(error){{statusBox.textContent=error.message;statusBox.className='status fail'}}}}
saveButton.addEventListener('click',async()=>{{saveButton.disabled=true;saveState.textContent='Сохраняем…';saveState.className='';try{{const response=await fetch(`/api/meetings/${{id}}`,{{method:'PUT',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(currentEdits())}});const body=await response.json();if(!response.ok)throw new Error(body.detail||'Не удалось сохранить изменения.');editorDirty=false;saveState.textContent='Изменения сохранены';await refresh()}}catch(error){{saveState.textContent=error.message;saveState.className='error';saveButton.disabled=false}}}});
consent.addEventListener('change',()=>{{processButton.disabled=!consent.checked}});processButton.addEventListener('click',async()=>{{processButton.disabled=true;errorBox.textContent='';try{{const response=await fetch(`/api/meetings/${{id}}/process`,{{method:'POST'}});const body=await response.json();if(!response.ok)throw new Error(body.detail||'Не удалось запустить обработку.');await refresh()}}catch(error){{errorBox.textContent=error.message;processButton.disabled=!consent.checked}}}});const deleteButton=document.querySelector('#deleteMeeting');if(isAdmin&&deleteButton)deleteButton.addEventListener('click',async()=>{{if(!confirm('Удалить встречу и связанные с ней материалы?'))return;const response=await fetch(`/api/meetings/${{id}}`,{{method:'DELETE'}});if(response.ok)location.href='/';else{{const body=await response.json();errorBox.textContent=body.detail||'Не удалось удалить встречу.'}}}});refresh();
</script></body></html>"""
