"""Employee photos for the kiosk.

Photos live in /config/www/tsheets/ as <user_id>.<ext> (uploaded from the options
flow) or <first_last>.<ext> (dropped in by hand). They're served at /tsheets_photos/.
A photo here wins over the QuickBooks Time profile image.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import DOMAIN, PHOTO_DIR, PHOTO_EXTENSIONS
from .data import Employee

PHOTO_URL = "/tsheets_photos"
_REGISTERED = f"{DOMAIN}_photo_path_registered"


class PhotoStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.dir = Path(hass.config.path("www", PHOTO_DIR))
        self._urls: dict[int, str] = {}

    async def async_setup(self) -> None:
        """Create the folder and serve it (once per HA run)."""
        await self.hass.async_add_executor_job(lambda: self.dir.mkdir(parents=True, exist_ok=True))
        if not self.hass.data.get(_REGISTERED):
            await self.hass.http.async_register_static_paths(
                [StaticPathConfig(PHOTO_URL, str(self.dir), False)]
            )
            self.hass.data[_REGISTERED] = True

    def url(self, user_id: int, employee: Employee | None) -> str | None:
        return self._urls.get(user_id) or (employee.picture if employee else None)

    async def async_scan(self, users: dict[int, Employee]) -> None:
        def _scan() -> dict[int, str]:
            files: dict[str, tuple[str, int]] = {}
            if self.dir.is_dir():
                for f in self.dir.iterdir():
                    ext = f.suffix.lstrip(".").lower()
                    if f.is_file() and ext in PHOTO_EXTENSIONS:
                        files[f.stem.lower()] = (f.name, int(f.stat().st_mtime))
            urls: dict[int, str] = {}
            for uid, emp in users.items():
                for stem in (str(uid), slugify(emp.name)):
                    if stem in files:
                        name, mtime = files[stem]
                        # ?v= busts the browser cache when a photo is replaced
                        urls[uid] = f"{PHOTO_URL}/{name}?v={mtime}"
                        break
            return urls

        self._urls = await self.hass.async_add_executor_job(_scan)

    def _remove_sync(self, user_id: int, employee: Employee | None) -> None:
        stems = {str(user_id)} | ({slugify(employee.name)} if employee else set())
        for f in self.dir.iterdir() if self.dir.is_dir() else []:
            if f.stem.lower() in stems and f.suffix.lstrip(".").lower() in PHOTO_EXTENSIONS:
                f.unlink()

    def save_sync(self, user_id: int, employee: Employee | None, source: Path) -> None:
        """Copy an uploaded file in as <user_id>.<ext> (runs in executor)."""
        ext = source.suffix.lstrip(".").lower()
        if ext not in PHOTO_EXTENSIONS:
            raise ValueError(ext)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._remove_sync(user_id, employee)
        shutil.copyfile(source, self.dir / f"{user_id}.{ext}")

    async def async_remove(self, user_id: int, employee: Employee | None) -> None:
        await self.hass.async_add_executor_job(self._remove_sync, user_id, employee)
