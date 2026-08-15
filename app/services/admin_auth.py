from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import Settings
from app.services.settings_store import SettingsStore


class AdminAuthError(ValueError):
    pass


@dataclass(slots=True)
class AdminSession:
    csrf_token: str
    expires_at: float


class AdminAuthService:
    def __init__(
        self,
        store: SettingsStore,
        settings: Settings,
        *,
        session_ttl_seconds: int = 12 * 60 * 60,
        clock=time.monotonic,
    ) -> None:
        self.store = store
        self.settings = settings
        self._hasher = PasswordHasher()
        self._session_ttl = session_ttl_seconds
        self._clock = clock
        self._sessions: dict[str, AdminSession] = {}
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    @property
    def configured(self) -> bool:
        admin = self.store.load().get("admin", {})
        return bool(admin.get("username") and admin.get("password_hash"))

    def create_admin(self, username: str, password: str, confirmation: str) -> tuple[str, str]:
        if self.configured:
            raise AdminAuthError("Administrator account is already configured.")
        username = username.strip()
        if len(username) < 3 or len(username) > 64:
            raise AdminAuthError("Username must be between 3 and 64 characters.")
        if password != confirmation:
            raise AdminAuthError("Password confirmation does not match.")
        if len(password) < 12:
            raise AdminAuthError("Password must contain at least 12 characters.")
        self.store.update(
            "admin", {"username": username, "password_hash": self._hasher.hash(password)}
        )
        return self._issue_session()

    def login(self, username: str, password: str, rate_key: str) -> tuple[str, str]:
        self._check_rate_limit(rate_key)
        admin = self.store.load().get("admin", {})
        valid = False
        if admin.get("username") and secrets.compare_digest(username, admin["username"]):
            try:
                valid = self._hasher.verify(admin["password_hash"], password)
            except (InvalidHashError, VerifyMismatchError):
                valid = False
        elif self.settings.dashboard_username and self.settings.dashboard_password:
            valid = secrets.compare_digest(
                username, self.settings.dashboard_username
            ) and secrets.compare_digest(
                password,
                self.settings.dashboard_password.get_secret_value(),
            )
        if not valid:
            self._failures[rate_key].append(self._clock())
            raise AdminAuthError("Invalid administrator credentials.")
        self._failures.pop(rate_key, None)
        return self._issue_session()

    def session(self, token: str | None) -> AdminSession | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if session and session.expires_at > self._clock():
            return session
        self._sessions.pop(token, None)
        return None

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def _issue_session(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        self._sessions[token] = AdminSession(csrf, self._clock() + self._session_ttl)
        return token, csrf

    def _check_rate_limit(self, key: str) -> None:
        now = self._clock()
        failures = self._failures[key]
        while failures and failures[0] <= now - 300:
            failures.popleft()
        if len(failures) >= 5:
            raise AdminAuthError("Too many login attempts. Try again later.")
