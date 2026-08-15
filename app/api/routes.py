from __future__ import annotations

import secrets
import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, SecretStr

from app.config import Settings
from app.database import Database
from app.queue_manager import QueueManager
from app.services.admin_auth import AdminAuthService
from app.services.telegram_auth import TelegramAuthError, TelegramAuthService

router = APIRouter()
security = HTTPBasic(auto_error=False)
SESSION_COOKIE = "tmd_admin_session"
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def services(request: Request) -> tuple[Settings, Database, QueueManager]:
    return request.app.state.settings, request.app.state.database, request.app.state.queue


def auth_service(request: Request) -> TelegramAuthService:
    return request.app.state.telegram_auth


class PhoneRequest(BaseModel):
    phone: str


class CodeRequest(BaseModel):
    code: SecretStr


class PasswordRequest(BaseModel):
    password: SecretStr


def auth_error(exc: TelegramAuthError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={"code": exc.code, "message": exc.message},
    )


def authenticate(
    request: Request, credentials: HTTPBasicCredentials | None = Depends(security)
) -> None:
    settings: Settings = request.app.state.settings
    admin: AdminAuthService | None = getattr(request.app.state, "admin_auth", None)
    if admin and admin.session(request.cookies.get(SESSION_COOKIE)):
        return
    if admin and admin.configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator login required",
        )
    if not settings.dashboard_username and not settings.dashboard_password:
        return
    valid = bool(
        credentials
        and settings.dashboard_username
        and settings.dashboard_password
        and secrets.compare_digest(credentials.username, settings.dashboard_username)
        and secrets.compare_digest(
            credentials.password, settings.dashboard_password.get_secret_value()
        )
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )


def require_admin(
    request: Request, credentials: HTTPBasicCredentials | None = Depends(security)
) -> None:
    admin: AdminAuthService | None = getattr(request.app.state, "admin_auth", None)
    session = admin.session(request.cookies.get(SESSION_COOKIE)) if admin else None
    if session:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(supplied, session.csrf_token):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
        return
    settings: Settings = request.app.state.settings
    if admin and not admin.configured and not (
        settings.dashboard_username and settings.dashboard_password
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Administrator login required")
    authenticate(request, credentials)


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request, credentials: HTTPBasicCredentials | None = Depends(security)
) -> Response:
    runtime = getattr(request.app.state, "runtime_settings", None)
    if runtime is not None and not runtime.setup_completed:
        return RedirectResponse("/setup", status_code=303)
    try:
        authenticate(request, credentials)
    except HTTPException:
        admin: AdminAuthService | None = getattr(request.app.state, "admin_auth", None)
        if admin and admin.configured:
            return RedirectResponse("/settings", status_code=303)
        raise
    return templates.TemplateResponse(request=request, name="dashboard.html")


@router.get("/api/status", dependencies=[Depends(authenticate)])
async def app_status(request: Request) -> dict[str, object]:
    settings, database, queue = services(request)
    disk = shutil.disk_usage(settings.download_root)
    return {
        "running": True,
        "telegram_connected": bool(
            request.app.state.telegram and request.app.state.telegram.connected
        ),
        "queue_size": queue.queue.qsize(),
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
        "stats": await database.stats(),
        "jobs": [
            {
                **asdict(job),
                "state": job.state.value,
                "cancel_requested": job.cancel_requested,
            }
            for job in await database.list_jobs()
        ],
        "events": await database.recent_events(),
    }


@router.get("/api/telegram/status", dependencies=[Depends(require_admin)])
async def telegram_status(request: Request) -> dict[str, object]:
    return await auth_service(request).status()


@router.post("/api/telegram/auth/send-code", dependencies=[Depends(require_admin)])
async def telegram_send_code(payload: PhoneRequest, request: Request) -> dict[str, object]:
    try:
        return await auth_service(request).send_code(payload.phone)
    except TelegramAuthError as exc:
        raise auth_error(exc) from None


@router.post("/api/telegram/auth/verify-code", dependencies=[Depends(require_admin)])
async def telegram_verify_code(payload: CodeRequest, request: Request) -> dict[str, object]:
    try:
        return await auth_service(request).verify_code(payload.code.get_secret_value())
    except TelegramAuthError as exc:
        raise auth_error(exc) from None


@router.post("/api/telegram/auth/verify-password", dependencies=[Depends(require_admin)])
async def telegram_verify_password(
    payload: PasswordRequest, request: Request
) -> dict[str, object]:
    try:
        return await auth_service(request).verify_password(payload.password.get_secret_value())
    except TelegramAuthError as exc:
        raise auth_error(exc) from None


@router.post("/api/telegram/auth/cancel", dependencies=[Depends(require_admin)])
async def telegram_cancel_auth(request: Request) -> dict[str, bool]:
    return await auth_service(request).cancel()


@router.post("/api/downloads/{job_id}/retry", dependencies=[Depends(authenticate)])
async def retry(job_id: int, request: Request) -> dict[str, bool]:
    _, database, queue = services(request)
    if not await database.retry(job_id):
        raise HTTPException(409, "Only failed or cancelled downloads can be retried")
    await queue.enqueue(job_id)
    return {"ok": True}


@router.post("/api/downloads/{job_id}/cancel", dependencies=[Depends(authenticate)])
async def cancel(job_id: int, request: Request) -> dict[str, bool]:
    _, database, _ = services(request)
    if not await database.request_cancel(job_id):
        raise HTTPException(409, "Only queued or downloading jobs can be cancelled")
    return {"ok": True}


@router.post("/api/history/clear", dependencies=[Depends(authenticate)])
async def clear_history(request: Request) -> dict[str, object]:
    _, database, queue = services(request)
    cleared = await database.clear_history()
    return {"ok": True, "cleared": cleared, "queue_size": queue.queue.qsize()}


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "telegram_connected": bool(
            request.app.state.telegram and request.app.state.telegram.connected
        ),
    }
