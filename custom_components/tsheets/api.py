"""Minimal async client for the QuickBooks Time (TSheets) REST API."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import API_BASE

PAGE_LIMIT = 200


class TSheetsError(Exception):
    """Generic API error."""


class TSheetsAuthError(TSheetsError):
    """The access token was rejected."""


def _as_dict(value: Any) -> dict:
    """TSheets returns [] instead of {} for empty result sets."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {str(i): v for i, v in enumerate(value)}
    return {}


def _error_text(data: Any, status: int) -> str:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        for group in _as_dict(data.get("results")).values():
            for item in _as_dict(group).values():
                if isinstance(item, dict) and int(item.get("_status_code", 200)) >= 300:
                    return str(
                        item.get("_status_extra")
                        or item.get("_status_message")
                        or f"HTTP {status}"
                    )
    return f"HTTP {status}"


class TSheetsApi:
    """Talks to rest.tsheets.com using an API access token."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json: Any = None,
    ) -> dict:
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with self._session.request(
                method,
                f"{API_BASE}/{path}",
                params=params,
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 401:
                    raise TSheetsAuthError("Access token rejected")
                if resp.status == 429:
                    raise TSheetsError("Rate limited by QuickBooks Time, try again shortly")
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = None
                if resp.status >= 400:
                    raise TSheetsError(_error_text(data, resp.status))
                return data if isinstance(data, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise TSheetsError(f"Cannot reach QuickBooks Time: {err}") from err

    async def _get_all(
        self, path: str, key: str, params: dict[str, str]
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        """GET every page; returns (results, supplemental_data)."""
        query = {**params, "limit": str(PAGE_LIMIT), "page": "1"}
        results: dict[str, dict] = {}
        supplemental: dict[str, dict] = {}
        for page in range(1, 100):
            query["page"] = str(page)
            data = await self._request("GET", path, params=query)
            results.update(_as_dict(_as_dict(data.get("results")).get(key)))
            for name, items in _as_dict(data.get("supplemental_data")).items():
                supplemental.setdefault(name, {}).update(_as_dict(items))
            if not data.get("more"):
                break
        return results, supplemental

    @staticmethod
    def _first_item(data: dict, key: str) -> dict:
        items = _as_dict(_as_dict(data.get("results")).get(key))
        if not items:
            raise TSheetsError("Empty response from QuickBooks Time")
        item = next(iter(items.values()))
        if int(item.get("_status_code", 200)) >= 300:
            raise TSheetsError(
                str(item.get("_status_extra") or item.get("_status_message") or "Request failed")
            )
        return item

    # ----- reads -------------------------------------------------------

    async def get_current_user(self) -> dict:
        data = await self._request("GET", "current_user")
        users = _as_dict(_as_dict(data.get("results")).get("users"))
        if not users:
            raise TSheetsError("current_user returned nothing")
        return next(iter(users.values()))

    async def get_users(self) -> dict[str, dict]:
        users, _ = await self._get_all("users", "users", {"active": "yes"})
        return users

    async def get_jobcodes(self) -> dict[str, dict]:
        jobcodes, _ = await self._get_all("jobcodes", "jobcodes", {"active": "both"})
        return jobcodes

    async def get_active_timesheets(
        self, start_date: str, user_ids: list[int] | None = None
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        params = {
            "on_the_clock": "yes",
            "start_date": start_date,
            "supplemental_data": "yes",
        }
        if user_ids:
            params["user_ids"] = ",".join(str(u) for u in user_ids)
        return await self._get_all("timesheets", "timesheets", params)

    async def get_timesheets_since(self, start_date: str) -> dict[str, dict]:
        """Every timesheet (open or finished) starting on/after start_date."""
        timesheets, _ = await self._get_all(
            "timesheets", "timesheets", {"start_date": start_date, "on_the_clock": "both"}
        )
        return timesheets

    async def get_geolocations(self, modified_since: str) -> dict[str, dict]:
        geos, _ = await self._get_all(
            "geolocations", "geolocations", {"modified_since": modified_since}
        )
        return geos

    async def url_exists(self, url: str) -> bool:
        """True if an (unauthenticated) GET of url returns 200."""
        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True
            ) as resp:
                return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    # ----- writes ------------------------------------------------------

    async def clock_in(
        self, user_id: int, jobcode_id: int, start: str, notes: str = ""
    ) -> dict:
        body = {
            "data": [
                {
                    "user_id": user_id,
                    "type": "regular",
                    "start": start,
                    "end": "",
                    "jobcode_id": jobcode_id,
                    "notes": notes,
                }
            ]
        }
        data = await self._request("POST", "timesheets", json=body)
        return self._first_item(data, "timesheets")

    async def clock_out(self, timesheet_id: int, end: str) -> dict:
        body = {"data": [{"id": timesheet_id, "end": end}]}
        data = await self._request("PUT", "timesheets", json=body)
        return self._first_item(data, "timesheets")
