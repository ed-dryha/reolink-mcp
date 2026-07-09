"""CameraManager: registry of camera name -> Host, lazy connect, guaranteed
logout on shutdown.

This is the **only** module in the codebase that imports `reolink_aio`
(ARCHITECTURE.md's mock-seam design) — every tool depends on this manager
for connection lifecycle and error translation, never constructing `Host`
directly.

Session-lifecycle rules (CONN-03, D-06, ARCHITECTURE.md Patterns 1-3,
Anti-Patterns 1-3):
  - One `Host` per camera for the process lifetime — never per call.
  - Lazy connect: `Host` is constructed and logged in only on the first
    `get()` for that camera name, never during `CameraManager.__init__`.
  - No server-side keepalive/relogin loop — `reolink-aio`'s `login()`
    self-heals on every `send()` call; duplicating that here would worsen
    shared-session contention (HDWR-03).
  - `close_all()` guarantees `logout()` is attempted for every connected
    camera and never lets one failure block the others.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from reolink_aio.api import Host

from reolink_mcp.config import CameraConfig
from reolink_mcp.errors import (
    CameraError,
    UnknownCameraError,
    classify_reolink_error,
    unknown_camera_message,
)

logger = logging.getLogger(__name__)


@dataclass
class CameraHandle:
    """A connected camera: name -> Host + channel resolution.

    `channel` is always 0 in Phase 1 (standalone cameras only); NVR/
    multi-channel support is a config-layer change for a future version
    (ARCHITECTURE.md Pattern 3).
    """

    name: str
    host: Host
    channel: int
    connected: bool = True
    states_polled_at: datetime | None = None


class CameraManager:
    """Registry of configured cameras, lazily connecting `Host` instances on
    first use and guaranteeing exception-tolerant logout on shutdown."""

    def __init__(self, cameras: dict[str, CameraConfig]) -> None:
        # No I/O here (D-06) — pure registry setup.
        self._configs = cameras
        self._handles: dict[str, CameraHandle] = {}
        self._locks: dict[str, asyncio.Lock] = {
            name: asyncio.Lock() for name in cameras
        }

    async def get(self, name: str) -> CameraHandle:
        """Return the cached `CameraHandle` for `name`, connecting on first
        use. Raises `UnknownCameraError` for an unconfigured name, or
        `CameraError` (translated via `classify_reolink_error`) on connect
        failure. A failed connect is never cached — the next call retries
        cleanly."""
        if name not in self._configs:
            raise UnknownCameraError(
                unknown_camera_message(name, list(self._configs))
            )

        async with self._locks[name]:
            if name in self._handles:
                return self._handles[name]

            cfg = self._configs[name]
            # Pitfall D (01-RESEARCH.md): the library's own default is 30s,
            # tuned for a background poller — far too long for an
            # interactive tool call.
            host = Host(
                host=cfg.host,
                username=cfg.username,
                password=cfg.password.get_secret_value(),
                timeout=10,
            )
            try:
                await host.get_host_data()
            except Exception as exc:
                raise CameraError(
                    classify_reolink_error(exc, name, cfg.host)
                ) from exc

            handle = CameraHandle(name=name, host=host, channel=0, connected=True)
            self._handles[name] = handle
            return handle

    def configured_names(self) -> list[str]:
        """Every configured camera name, no I/O — used so `list_cameras`
        (Plan 03) can enumerate all cameras even before any connect
        attempt (D-07)."""
        return list(self._configs)

    def configured_host(self, name: str) -> str:
        """The configured host/IP for `name`, no I/O — always available
        regardless of connection state."""
        return self._configs[name].host

    async def close_all(self) -> None:
        """Attempt `logout()` for every currently connected handle.
        Exception-tolerant: one camera's logout failure never blocks the
        others (Pitfall 5)."""
        await asyncio.gather(
            *(self._logout_one(handle) for handle in self._handles.values()),
            return_exceptions=True,
        )

    async def _logout_one(self, handle: CameraHandle) -> None:
        try:
            await handle.host.logout()
        except Exception as exc:
            logger.warning("logout failed for camera '%s': %r", handle.name, exc)
