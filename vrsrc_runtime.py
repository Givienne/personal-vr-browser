"""Private runtime credentials for the VRSRC app-key contract.

A build secret must be provisioned separately, on the maintainer's machine.
Short-lived API keys are held in memory only. No client/browser access.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import os
import secrets
import stat
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

KEY_ENDPOINT = "https://vrsrc.fyi/api/mirror/app-key"
MIRROR_ORIGIN = "https://go.srcdl1.xyz"

class AuthenticationUnavailable(RuntimeError):
    """Authentication failed or is rate-limited. Never include secret values."""

class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _utc_date(raw: str) -> datetime:
    if not isinstance(raw, str):
        raise ValueError("Invalid timestamp")
    date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("Timestamp must include a UTC offset")
    return date.astimezone(timezone.utc)


def load_install_id(filename: Path) -> str:
    """Generate once per installation; never derive it from host or user info."""
    filename = Path(filename)
    if filename.is_symlink():
        raise AuthenticationUnavailable("Install ID path must not be a symlink")
    if filename.exists():
        if filename.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise AuthenticationUnavailable("Stored install ID must be owner-only")
        value = filename.read_text("ascii").strip()
        if not (8 <= len(value) <= 64 and all(c.isascii() and (c.isalnum() or c in "_-") for c in value)):
            raise AuthenticationUnavailable("Stored install ID is invalid")
        return value
    filename.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(24)
    try:
        fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        return load_install_id(filename)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(value + "\n")
    return value


class RuntimeKeys:
    def __init__(self, *, client_id: str, build_secret: str, install_file: Path,
                 app_version: str = "1.2.3", opener=None, clock=None):
        self.client_id = client_id
        self._build_secret = build_secret
        self.install_file = Path(install_file)
        self.app_version = app_version
        self._opener = opener or build_opener(NoRedirects())
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._key = None
        self._refresh_after = datetime.min.replace(tzinfo=timezone.utc)
        self._expires_at = datetime.min.replace(tzinfo=timezone.utc)
        self._retry_after = datetime.min.replace(tzinfo=timezone.utc)
        self._suspended = False

    def ready(self) -> bool:
        return bool(self.client_id and self._build_secret)

    def invalidate(self, forbidden=False) -> None:
        with self._lock:
            self._key = None
            self._refresh_after = datetime.min.replace(tzinfo=timezone.utc)
            if forbidden:
                # Force one fresh request on next attempt, not a tight retry loop.
                self._expires_at = datetime.min.replace(tzinfo=timezone.utc)

    def get(self) -> str:
        with self._lock:
            now = self._clock()
            if not self.ready() or self._suspended:
                raise AuthenticationUnavailable("VRSRC credentials unavailable or suspended")
            if self._key and now < self._refresh_after and now < self._expires_at:
                return self._key
            if now < self._retry_after:
                if self._key and now < self._expires_at:
                    return self._key
                raise AuthenticationUnavailable("Runtime key request is cooling down")
            try:
                return self._request(now)
            except AuthenticationUnavailable:
                if self._key and now < self._expires_at and not self._suspended:
                    return self._key
                raise

    def _request(self, now: datetime) -> str:
        install_id = load_install_id(self.install_file)
        payload = json.dumps({"client_id": self.client_id,
                              "build_secret": self._build_secret,
                              "install_id": install_id,
                              "app_version": self.app_version}).encode("utf-8")
        request = Request(KEY_ENDPOINT, data=payload,
                          headers={"Content-Type": "application/json", "Accept": "application/json"},
                          method="POST")
        try:
            with self._opener.open(request, timeout=15) as response:
                if response.status != 200:
                    raise AuthenticationUnavailable("App-key endpoint returned non-200")
                data = json.loads(response.read(8193))
        except HTTPError as error:
            self._retry_after = now + timedelta(seconds=60)
            if error.code == 403:
                self._suspended = True  # retired, suspended, invalid; require operator action.
                self._key = None
                raise AuthenticationUnavailable("App credentials rejected (invalid, retired or suspended)") from None
            if error.code == 503:
                raise AuthenticationUnavailable("App key pending; retry no sooner than 60 seconds") from None
            raise AuthenticationUnavailable("App-key endpoint failed; retry later") from None
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            self._retry_after = now + timedelta(seconds=60)
            raise AuthenticationUnavailable("App-key request failed; retry later") from None
        if not isinstance(data, dict) or data.get("baseUri") != MIRROR_ORIGIN:
            self._retry_after = now + timedelta(seconds=60)
            raise AuthenticationUnavailable("Provider key response has unexpected mirror origin")
        key = data.get("apiKey")
        if not isinstance(key, str) or not key or any(ord(c) < 33 or ord(c) > 126 for c in key) or len(key) > 4096:
            raise AuthenticationUnavailable("Provider returned an invalid API key")
        try:
            expires = _utc_date(data["expiresAt"])
            refresh = _utc_date(data["refreshAfter"])
        except (ValueError, KeyError, TypeError):
            raise AuthenticationUnavailable("Provider returned invalid key lifetimes") from None
        if expires <= now or refresh > expires or expires - now > timedelta(days=31):
            raise AuthenticationUnavailable("Provider returned unusable key lifetimes")
        self._key = key
        self._expires_at = expires
        self._refresh_after = max(now + timedelta(seconds=60), refresh)
        self._retry_after = now
        return key


_instance = None
_instance_lock = threading.Lock()


def credentials_enabled():
    return (os.environ.get("VRSRC_ACCESS_APPROVED") == "1"
            and bool(os.environ.get("VRSRC_CLIENT_ID"))
            and bool(os.environ.get("VRSRC_BUILD_SECRET")))


def runtime_keys():
    global _instance
    if not credentials_enabled():
        raise AuthenticationUnavailable("VRSRC access not authorized")
    with _instance_lock:
        if _instance is None:
            _instance = RuntimeKeys(
                client_id=os.environ["VRSRC_CLIENT_ID"],
                build_secret=os.environ["VRSRC_BUILD_SECRET"],
                install_file=Path(os.environ.get("VRSRC_INSTALL_ID_FILE", "/srv/storage/vr-library/.vrsrc-install-id")),
                app_version=os.environ.get("VRSRC_APP_VERSION", "1.2.3"))
        return _instance
