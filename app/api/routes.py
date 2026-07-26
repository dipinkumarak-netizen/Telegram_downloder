from __future__ import annotations

import secrets
import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.database import Database
from app.queue_manager import QueueManager

router = APIRouter()
security = HTTPBasic(auto_error=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def services(request: Request) -> tuple[Settings, Database, QueueManager]:
    return request.app.state.settings, request.app.state.database, request.app.state.queue


def authenticate(
    request: Request, credentials: HTTPBasicCredentials | None = Depends(security)
) -> None:
    settings: Settings = request.app.state.settings
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


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(authenticate)])
async def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="dashboard.html")


@router.get("/api/status", dependencies=[Depends(authenticate)])
async def app_status(request: Request) -> dict[str, object]:
    settings, database, queue = services(request)
    disk = shutil.disk_usage(settings.download_root)
    return {
        "running": True,
        "telegram_connected": request.app.state.telegram.connected,
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


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "telegram_connected": request.app.state.telegram.connected,
    }
