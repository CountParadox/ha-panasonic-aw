"""Async client for Panasonic AW-series PTZ cameras (HTTP CGI protocol).

This module only depends on aiohttp so it can be tested without Home Assistant.

Protocol summary
----------------
* Pan/tilt head commands:  GET /cgi-bin/aw_ptz?cmd=%23<CMD>&res=1
* Camera (image) commands: GET /cgi-bin/aw_cam?cmd=<CMD>&res=1
* Batch status:            GET /live/camdata.html
* JPEG snapshot:           GET /cgi-bin/camera?resolution=<width>
* Device info:             GET /cgi-bin/getinfo?FILE=1
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

# --- State keys -----------------------------------------------------------
KEY_POWER = "power"  # 0 standby, 1 on, 3 transitioning
KEY_PAN = "pan"  # raw 0x0000-0xFFFF, centre 0x8000
KEY_TILT = "tilt"  # raw 0x0000-0xFFFF, centre 0x8000
KEY_ZOOM = "zoom"  # raw 0x555-0xFFF
KEY_FOCUS = "focus"  # raw 0x555-0xFFF
KEY_IRIS = "iris"  # raw 0x555-0xFFF
KEY_AUTOFOCUS = "autofocus"  # 0/1
KEY_AUTO_IRIS = "auto_iris"  # 0/1
KEY_NIGHT = "night"  # 0 day, 1 night
KEY_TALLY = "tally"  # 0/1
KEY_LAST_PRESET = "last_preset"  # 0-99 (zero based)
KEY_INSTALL = "install"  # 0 desktop, 1 hanging
KEY_PRESET_SPEED = "preset_speed"  # 250-999
KEY_PRESETS = "presets"  # set of saved preset numbers (zero based)

# Camera (aw_cam) keys are the response command itself, value is a hex string.
KEY_GAIN = "OGU"
KEY_WHITE_BALANCE = "OAW"
KEY_SCENE = "OSF"
KEY_COLOR_BAR = "OBR"
KEY_DNR = "OSD:3A"

POWER_STANDBY = 0
POWER_ON = 1
POWER_TRANSITION = 3

POS_MIN = 0x555
POS_MAX = 0xFFF
PT_CENTRE = 0x8000
# From the specification: tilt +90 deg is 5555h and pan -175 deg is 2D09h.
PT_COUNTS_PER_DEGREE = 10923 / 90
GAIN_AUTO = 0x80
GAIN_0DB = 0x08

# state key -> aw_ptz query command
PTZ_QUERIES: dict[str, str] = {
    KEY_PAN: "APC",
    KEY_ZOOM: "GZ",
    KEY_FOCUS: "GF",
    KEY_IRIS: "GI",
    KEY_AUTOFOCUS: "D1",
    KEY_AUTO_IRIS: "D3",
    KEY_TALLY: "DA",
    KEY_LAST_PRESET: "S",
    KEY_NIGHT: "D6",
    KEY_PRESET_SPEED: "UPVS",
    KEY_INSTALL: "INS",
}

# state key -> aw_cam query command
CAM_QUERIES: dict[str, str] = {
    KEY_GAIN: "QGU",
    KEY_WHITE_BALANCE: "QAW",
    KEY_SCENE: "QSF",
    KEY_COLOR_BAR: "QBR",
    KEY_DNR: "QSD:3A",
}

_CAM_SUBCOMMANDS = {"OSA", "OSD", "OSE", "OSG", "OSI", "OSJ", "OSL"}
_ERROR_RE = re.compile(r"^[A-Za-z]?[eE][rR]([123])")
_HEX3 = r"([0-9A-Fa-f]{3})"

_PTZ_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^p([0-3])$"), "power"),
    (re.compile(r"^aPC([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})$"), "pt"),
    (re.compile(rf"^(?:gz|axz){_HEX3}$"), KEY_ZOOM),
    (re.compile(rf"^(?:gf|axf){_HEX3}$"), KEY_FOCUS),
    (re.compile(rf"^(?:gi|axi){_HEX3}([01])?$"), "iris"),
    (re.compile(r"^d1([01])$"), KEY_AUTOFOCUS),
    (re.compile(r"^d3([01])$"), KEY_AUTO_IRIS),
    (re.compile(r"^d6([01])$"), KEY_NIGHT),
    (re.compile(r"^dA([01])$"), KEY_TALLY),
    (re.compile(r"^s(\d\d)$"), KEY_LAST_PRESET),
    (re.compile(r"^iNS([01])$"), KEY_INSTALL),
    (re.compile(r"^uPVS(\d{3})$"), KEY_PRESET_SPEED),
    (re.compile(r"^pE(0[0-2])([0-9A-Fa-f]{10})$"), "preset_entries"),
]


class PanasonicAwError(Exception):
    """Base error."""


class PanasonicAwConnectionError(PanasonicAwError):
    """The camera could not be reached."""


class PanasonicAwAuthError(PanasonicAwError):
    """The camera rejected the credentials."""


class PanasonicAwCommandError(PanasonicAwError):
    """The camera answered with an error (ER1 unsupported, ER2 busy, ER3 out of range)."""

    def __init__(self, command: str, response: str, code: int) -> None:
        reason = {1: "unsupported command", 2: "busy", 3: "value out of range"}.get(
            code, "error"
        )
        super().__init__(f"Camera rejected '{command}': {reason} ({response})")
        self.command = command
        self.response = response
        self.code = code


# --- Conversions ----------------------------------------------------------
def percent_to_position(percent: float) -> int:
    """Convert 0-100 % to a zoom/focus/iris position (0x555-0xFFF)."""
    percent = min(100.0, max(0.0, percent))
    return POS_MIN + round(percent / 100 * (POS_MAX - POS_MIN))


def position_to_percent(raw: int) -> float:
    """Convert a zoom/focus/iris position to 0-100 %."""
    raw = min(POS_MAX, max(POS_MIN, raw))
    return round((raw - POS_MIN) / (POS_MAX - POS_MIN) * 100, 1)


def pan_to_degrees(raw: int) -> float:
    return round((raw - PT_CENTRE) / PT_COUNTS_PER_DEGREE, 1)


def degrees_to_pan(degrees: float) -> int:
    return min(0xFFFF, max(0, round(PT_CENTRE + degrees * PT_COUNTS_PER_DEGREE)))


def tilt_to_degrees(raw: int) -> float:
    """Positive is up (lower raw values are up)."""
    return round((PT_CENTRE - raw) / PT_COUNTS_PER_DEGREE, 1)


def degrees_to_tilt(degrees: float) -> int:
    return min(0xFFFF, max(0, round(PT_CENTRE - degrees * PT_COUNTS_PER_DEGREE)))


def speed_to_code(percent: float) -> str:
    """Convert a -100..100 speed to the protocol's 01-99 code (50 is stop)."""
    percent = min(100.0, max(-100.0, percent))
    return f"{50 + round(percent * 0.49):02d}"


def hex_value(value: Any) -> int | None:
    """Parse a camera hex value such as '80', '0x80' or '1'."""
    if value is None:
        return None
    try:
        return int(str(value), 16)
    except ValueError:
        return None


def ingest(line: str, data: dict[str, Any]) -> bool:
    """Parse one response line into ``data``. Returns True if it was understood."""
    line = line.strip()
    if not line or _ERROR_RE.match(line):
        return False

    for pattern, kind in _PTZ_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue
        if kind == "power":
            data[KEY_POWER] = int(match.group(1))
        elif kind == "pt":
            data[KEY_PAN] = int(match.group(1), 16)
            data[KEY_TILT] = int(match.group(2), 16)
        elif kind == "iris":
            data[KEY_IRIS] = int(match.group(1), 16)
            if match.group(2) is not None:
                data[KEY_AUTO_IRIS] = int(match.group(2))
        elif kind == "preset_entries":
            block = int(match.group(1))
            bits = int(match.group(2), 16)
            presets = {p for p in data.get(KEY_PRESETS, set()) if p // 40 != block}
            presets.update(block * 40 + i for i in range(40) if bits >> i & 1)
            data[KEY_PRESETS] = {p for p in presets if p < 100}
        elif kind in (KEY_ZOOM, KEY_FOCUS):
            data[kind] = int(match.group(1), 16)
        else:
            data[kind] = int(match.group(1))
        return True

    # Camera command responses: "OGU:80", "OSD:3A:01", "OID:AW-HE38"
    if ":" in line and line[:1].isupper():
        parts = line.split(":")
        if parts[0] in _CAM_SUBCOMMANDS and len(parts) >= 3:
            key, value = f"{parts[0]}:{parts[1]}", ":".join(parts[2:])
        else:
            key, value = parts[0], ":".join(parts[1:])
        value = value.strip()
        if value.lower().startswith("0x"):
            value = value[2:]
        if key == "DCB":
            # Colour bars answer the control command as DCB:n, the query as OBR:n.
            key = KEY_COLOR_BAR
        elif key == "XSF":
            # Scene control uses 1-4, the query (OSF) answers 0-3.
            scene = hex_value(value)
            if scene is None:
                return False
            key, value = KEY_SCENE, str(scene - 1)
        data[key] = value
        return True
    return False


class PanasonicAwClient:
    """Talk to one camera."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = 80,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 5.0,
        min_interval: float = 0.04,
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        self._username = username or None
        self._password = password or ""
        self._timeout = timeout
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_command = 0.0
        self._digest: dict[str, str] | None = None
        self._nonce_count = 0
        self._camdata: bool | None = None
        self._poll_count = 0
        self.unsupported: set[str] = set()
        self.info: dict[str, str] = {}

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # --- HTTP -------------------------------------------------------------
    def _digest_header(self, path_qs: str) -> str:
        assert self._digest is not None
        d = self._digest
        self._nonce_count += 1
        nc = f"{self._nonce_count:08x}"
        cnonce = os.urandom(8).hex()
        ha1 = hashlib.md5(
            f"{self._username}:{d.get('realm', '')}:{self._password}".encode()
        ).hexdigest()
        ha2 = hashlib.md5(f"GET:{path_qs}".encode()).hexdigest()
        nonce = d.get("nonce", "")
        if "auth" in d.get("qop", ""):
            response = hashlib.md5(
                f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode()
            ).hexdigest()
            extra = f', qop=auth, nc={nc}, cnonce="{cnonce}"'
        else:
            response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
            extra = ""
        header = (
            f'Digest username="{self._username}", realm="{d.get("realm", "")}", '
            f'nonce="{nonce}", uri="{path_qs}", response="{response}"{extra}'
        )
        if "opaque" in d:
            header += f', opaque="{d["opaque"]}"'
        return header

    async def _request(self, path_qs: str, *, binary: bool = False) -> Any:
        url = URL(f"{self.base_url}{path_qs}", encoded=True)
        try:
            async with asyncio.timeout(self._timeout):
                for attempt in range(2):
                    headers: dict[str, str] = {}
                    auth = None
                    if self._username:
                        if self._digest:
                            headers["Authorization"] = self._digest_header(path_qs)
                        else:
                            auth = aiohttp.BasicAuth(self._username, self._password)
                    async with self._session.get(
                        url, headers=headers, auth=auth
                    ) as resp:
                        if resp.status == 401:
                            challenge = resp.headers.get("WWW-Authenticate", "")
                            if (
                                attempt == 0
                                and self._username
                                and challenge.lower().startswith("digest")
                            ):
                                self._digest = dict(
                                    re.findall(r'(\w+)="?([^",]+)"?', challenge[7:])
                                )
                                self._nonce_count = 0
                                continue
                            raise PanasonicAwAuthError("Authentication failed")
                        if resp.status >= 400:
                            raise PanasonicAwError(
                                f"HTTP {resp.status} for {path_qs}"
                            )
                        if binary:
                            return await resp.read()
                        return (await resp.text(errors="replace")).strip()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise PanasonicAwConnectionError(
                f"Cannot reach camera at {self.host}: {err or type(err).__name__}"
            ) from err
        raise PanasonicAwAuthError("Authentication failed")

    async def _command(self, path: str, wire_cmd: str, shown: str) -> str:
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_command)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await self._request(
                    f"/cgi-bin/{path}?cmd={quote(wire_cmd, safe=':')}&res=1"
                )
            finally:
                self._last_command = time.monotonic()
        _LOGGER.debug("%s %s -> %s", path, shown, response)
        if match := _ERROR_RE.match(response):
            raise PanasonicAwCommandError(shown, response, int(match.group(1)))
        return response

    async def ptz(self, command: str) -> str:
        """Send a pan/tilt head command (without the leading '#')."""
        command = command.lstrip("#")
        return await self._command("aw_ptz", f"#{command}", f"#{command}")

    async def cam(self, command: str) -> str:
        """Send a camera command, e.g. 'QGU' or 'OGU:80'."""
        return await self._command("aw_cam", command, command)

    async def snapshot(self, resolution: int = 1280) -> bytes:
        return await self._request(
            f"/cgi-bin/camera?resolution={resolution}", binary=True
        )

    # --- Identification ---------------------------------------------------
    async def async_get_info(self) -> dict[str, str]:
        """Model, firmware, MAC and serial (as far as the camera reports them)."""
        info: dict[str, str] = {}
        data: dict[str, Any] = {}
        try:
            ingest(await self.cam("QID"), data)
        except PanasonicAwCommandError:
            # Not every model answers QID; make sure it is a camera at all.
            ingest(await self.ptz("O"), data)
            if KEY_POWER not in data:
                raise PanasonicAwError("Device did not answer like an AW camera")
        if data.get("OID"):
            info["model"] = data["OID"]
        try:
            ingest(await self.cam("QSV"), data)
            if data.get("OSV"):
                info["firmware"] = data["OSV"]
        except PanasonicAwError:
            pass
        try:
            text = await self._request("/cgi-bin/getinfo?FILE=1")
            for line in text.splitlines():
                key, sep, value = line.partition("=")
                key, value = key.strip().upper(), value.strip()
                if not sep or not value:
                    continue
                if key == "MAC":
                    info["mac"] = value.replace("-", ":").lower()
                elif key == "SERIAL":
                    info["serial"] = value
                elif key == "NAME":
                    info["name"] = value
                elif key == "VERSION" and "firmware" not in info:
                    info["firmware"] = value
        except PanasonicAwError:
            pass
        self.info = info
        return info

    # --- Polling ----------------------------------------------------------
    async def async_update(self, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read the camera state."""
        data: dict[str, Any] = {}
        if previous and KEY_PRESETS in previous:
            data[KEY_PRESETS] = set(previous[KEY_PRESETS])

        ingest(await self.ptz("O"), data)
        if data.get(KEY_POWER) != POWER_ON:
            return data

        # One request for most of the state, where the model offers it.
        if self._camdata is not False:
            try:
                text = await self._request("/live/camdata.html")
                understood = sum(ingest(line, data) for line in text.splitlines())
                self._camdata = understood > 0
            except PanasonicAwConnectionError:
                raise
            except PanasonicAwError:
                self._camdata = False
            data[KEY_POWER] = POWER_ON

        queries = [(k, c, self.ptz) for k, c in PTZ_QUERIES.items()]
        queries += [(k, c, self.cam) for k, c in CAM_QUERIES.items()]
        if self._poll_count % 6 == 0 or KEY_PRESETS not in data:
            data.pop(KEY_PRESETS, None)
            queries += [(f"PE0{i}", f"PE0{i}", self.ptz) for i in range(3)]
        self._poll_count += 1

        for key, command, send in queries:
            if key in data or key in self.unsupported:
                continue
            try:
                ingest(await send(command), data)
            except PanasonicAwCommandError as err:
                if err.code == 1:
                    _LOGGER.debug("Camera does not support %s", command)
                    self.unsupported.add(key)
            except PanasonicAwConnectionError:
                raise
            except PanasonicAwError:
                self.unsupported.add(key)
        return data

    # --- Motion helpers ---------------------------------------------------
    async def move(
        self,
        pan: float | None = None,
        tilt: float | None = None,
        zoom: float | None = None,
        focus: float | None = None,
    ) -> None:
        """Continuous move; speeds are -100..100, 0 stops that axis."""
        if pan is not None or tilt is not None:
            await self.ptz(f"PTS{speed_to_code(pan or 0)}{speed_to_code(tilt or 0)}")
        if zoom is not None:
            await self.ptz(f"Z{speed_to_code(zoom)}")
        if focus is not None:
            await self.ptz(f"F{speed_to_code(focus)}")

    async def stop(self) -> None:
        await self.move(pan=0, tilt=0, zoom=0, focus=0)
