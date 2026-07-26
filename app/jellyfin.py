from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class JellyfinClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def refresh(self) -> bool:
        if not (
            self.settings.jellyfin_refresh_enabled
            and self.settings.jellyfin_url
            and self.settings.jellyfin_api_key
        ):
            return False
        url = f"{self.settings.jellyfin_url.rstrip('/')}/Library/Refresh"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    url,
                    headers={"X-Emby-Token": self.settings.jellyfin_api_key.get_secret_value()},
                )
                response.raise_for_status()
            logger.info("Jellyfin library refresh requested")
            return True
        except httpx.HTTPError as exc:
            logger.warning("Jellyfin refresh failed; download remains complete: %s", exc)
            return False
