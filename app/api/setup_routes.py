from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, SecretStr

from app.api.routes import SESSION_COOKIE, require_admin
from app.services.admin_auth import AdminAuthError, AdminAuthService
from app.services.setup import SetupError, SetupService

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


class AdminCreateRequest(BaseModel):
    username: str
    password: SecretStr
    password_confirmation: SecretStr


class AdminLoginRequest(BaseModel):
    username: str
    password: SecretStr


class TelegramConfigRequest(BaseModel):
    api_id: int
    api_hash: SecretStr


class StorageDiskRequest(BaseModel):
    mount_path: str


class JellyfinRequest(BaseModel):
    enabled: bool = False
    url: str | None = None
    api_key: SecretStr | None = None


def setup_service(request: Request) -> SetupService:
    return request.app.state.setup_service


def admin_service(request: Request) -> AdminAuthService:
    return request.app.state.admin_auth


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=request.app.state.settings.session_cookie_secure,
        samesite="strict",
        max_age=12 * 60 * 60,
        path="/",
    )


def setup_error(exc: SetupError | AdminAuthError) -> HTTPException:
    code = exc.status_code if isinstance(exc, SetupError) else 400
    return HTTPException(code, str(exc))


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request) -> Response:
    service = setup_service(request)
    if service.runtime.setup_completed:
        session = admin_service(request).session(request.cookies.get(SESSION_COOKIE))
        destination = "/settings" if session else "/login"
        return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="setup.html")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> Response:
    service = setup_service(request)
    if not service.runtime.setup_completed:
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    session = admin_service(request).session(request.cookies.get(SESSION_COOKIE))
    if not session:
        return RedirectResponse("/login?next=/settings", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="settings.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/") -> Response:
    service = setup_service(request)
    if not service.runtime.setup_completed:
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    destination = next if next in {"/", "/settings"} else "/"
    session = admin_service(request).session(request.cookies.get(SESSION_COOKIE))
    if session:
        return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next_path": destination},
    )


@router.get("/api/setup/status")
async def setup_status(request: Request) -> dict[str, object]:
    service = setup_service(request)
    admin = admin_service(request)
    session = admin.session(request.cookies.get(SESSION_COOKIE))
    base = {
        "setup_completed": service.runtime.setup_completed,
        "admin_configured": admin.configured
        or bool(service.settings.dashboard_username and service.settings.dashboard_password),
        "authenticated": session is not None,
        "csrf_token": session.csrf_token if session else None,
    }
    if session:
        base.update(await service.status())
        selected_root = base.get("storage_root")
        available_roots = storage_browser(request).roots()
        base["storage_available"] = any(
            disk["mount_path"] == selected_root and disk["writable"] for disk in available_roots
        )
    return base


@router.post("/api/setup/admin")
async def create_admin(
    payload: AdminCreateRequest, request: Request, response: Response
) -> dict[str, object]:
    service = setup_service(request)
    if service.runtime.setup_completed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Setup is already completed.")
    try:
        token, csrf = admin_service(request).create_admin(
            payload.username,
            payload.password.get_secret_value(),
            payload.password_confirmation.get_secret_value(),
        )
    except AdminAuthError as exc:
        raise setup_error(exc) from None
    set_session_cookie(response, request, token)
    return {"ok": True, "csrf_token": csrf}


@router.post("/api/admin/login")
async def admin_login(
    payload: AdminLoginRequest, request: Request, response: Response
) -> dict[str, object]:
    client = request.client.host if request.client else "unknown"
    try:
        token, csrf = admin_service(request).login(
            payload.username, payload.password.get_secret_value(), client
        )
    except AdminAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None
    set_session_cookie(response, request, token)
    return {"ok": True, "csrf_token": csrf}


@router.post("/api/admin/logout", dependencies=[Depends(require_admin)])
async def admin_logout(request: Request, response: Response) -> dict[str, bool]:
    admin_service(request).revoke(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/api/setup/telegram-config", dependencies=[Depends(require_admin)])
async def save_telegram(payload: TelegramConfigRequest, request: Request) -> dict[str, object]:
    try:
        await request.app.state.telegram_auth.cancel()
        result = setup_service(request).save_telegram(
            payload.api_id, payload.api_hash.get_secret_value()
        )
        result["restart_required"] = bool(
            request.app.state.telegram and request.app.state.telegram.connected
        )
        return result
    except SetupError as exc:
        raise setup_error(exc) from None


def storage_browser(request: Request):
    return request.app.state.storage_browser


@router.get("/api/storage/disks", dependencies=[Depends(require_admin)])
async def storage_disks(request: Request) -> dict[str, object]:
    browser = storage_browser(request)
    return {"available": browser.available, "disks": browser.roots()}


@router.post("/api/setup/storage-disk", dependencies=[Depends(require_admin)])
async def save_storage_disk(payload: StorageDiskRequest, request: Request) -> dict[str, object]:
    if request.app.state.queue.active_downloads:
        raise HTTPException(status.HTTP_409_CONFLICT, "Storage cannot change during a download.")
    browser = storage_browser(request)
    try:
        result = browser.prepare_disk(payload.mount_path)
        saved = setup_service(request).save_managed_storage()
        setup_service(request)._store_update(
            "storage",
            {
                "host_download_dir": result["host_download_dir"],
                "host_incomplete_dir": result["host_incomplete_dir"],
                "storage_root": result["storage_root"],
                "storage_display_name": result["display_name"],
            },
        )
        pending = request.app.state.settings.config_dir / "pending-download-host-dir"
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(result["storage_root"] + "\n", encoding="utf-8")
        pending.chmod(0o600)
        saved["host_download_dir"] = result["host_download_dir"]
        saved["host_incomplete_dir"] = result["host_incomplete_dir"]
        saved["temp_dir"] = result["temp_dir"]
        saved["storage_root"] = result["storage_root"]
        saved["storage_display_name"] = result["display_name"]
        saved["restart_required"] = True
        saved["deployment"] = "pending"
        return saved
    except (ValueError, SetupError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.post("/api/setup/jellyfin", dependencies=[Depends(require_admin)])
async def save_jellyfin(payload: JellyfinRequest, request: Request) -> dict[str, object]:
    try:
        return setup_service(request).save_jellyfin(
            payload.enabled,
            payload.url,
            payload.api_key.get_secret_value() if payload.api_key else None,
        )
    except SetupError as exc:
        raise setup_error(exc) from None


@router.post("/api/setup/jellyfin/test", dependencies=[Depends(require_admin)])
async def test_jellyfin(payload: JellyfinRequest, request: Request) -> dict[str, bool]:
    try:
        return await setup_service(request).test_jellyfin(
            payload.url, payload.api_key.get_secret_value() if payload.api_key else None
        )
    except SetupError as exc:
        raise setup_error(exc) from None


@router.post("/api/setup/validate", dependencies=[Depends(require_admin)])
async def validate_setup(request: Request) -> dict[str, object]:
    return await setup_service(request).validate()


@router.post("/api/setup/complete", dependencies=[Depends(require_admin)])
async def complete_setup(request: Request) -> dict[str, bool]:
    try:
        return await setup_service(request).complete()
    except SetupError as exc:
        raise setup_error(exc) from None
