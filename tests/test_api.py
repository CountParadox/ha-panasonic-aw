"""Tests for the protocol client against a simulated AW camera."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import aiohttp
from aiohttp import web
import pytest

# Load api.py on its own so the tests do not need Home Assistant installed.
_SPEC = importlib.util.spec_from_file_location(
    "panasonic_aw_api",
    Path(__file__).parents[1] / "custom_components" / "panasonic_aw" / "api.py",
)
api = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = api
_SPEC.loader.exec_module(api)


class FakeCamera:
    """Minimal AW-HE38-like camera."""

    def __init__(self) -> None:
        self.power = 1
        self.pan, self.tilt = 0x8000, 0x8000
        self.zoom, self.focus, self.iris = 0x555, 0x800, 0xFFF
        self.autofocus, self.auto_iris, self.tally = 1, 1, 0
        self.last_preset = 0
        self.presets = {0, 1, 41}
        self.gain = 0x80
        self.raw_queries: list[str] = []
        self.log: list[str] = []

    async def ptz(self, request: web.Request) -> web.Response:
        self.raw_queries.append(request.rel_url.raw_query_string)
        cmd = request.query["cmd"]
        assert cmd.startswith("#")
        cmd = cmd[1:]
        self.log.append(cmd)
        return web.Response(text=self._ptz(cmd))

    def _ptz(self, cmd: str) -> str:
        if cmd == "O":
            return f"p{self.power}"
        if cmd in ("O0", "O1"):
            self.power = int(cmd[1])
            return f"p{self.power}"
        if cmd == "APC":
            return f"aPC{self.pan:04X}{self.tilt:04X}"
        if cmd.startswith("APC"):
            self.pan, self.tilt = int(cmd[3:7], 16), int(cmd[7:11], 16)
            return f"aPC{self.pan:04X}{self.tilt:04X}"
        if cmd == "GZ":
            return f"gz{self.zoom:03X}"
        if cmd.startswith("AXZ"):
            self.zoom = int(cmd[3:], 16)
            return f"axz{self.zoom:03X}"
        if cmd == "GF":
            return f"gf{self.focus:03X}"
        if cmd == "GI":
            return f"gi{self.iris:03X}{self.auto_iris}"
        if cmd == "D1":
            return f"d1{self.autofocus}"
        if cmd in ("D10", "D11"):
            self.autofocus = int(cmd[2])
            return f"d1{self.autofocus}"
        if cmd == "D3":
            return f"d3{self.auto_iris}"
        if cmd == "DA":
            return f"dA{self.tally}"
        if cmd == "S":
            return f"s{self.last_preset:02d}"
        if cmd[0] == "R" and cmd[1:].isdigit():
            self.last_preset = int(cmd[1:])
            return f"s{self.last_preset:02d}"
        if cmd[0] == "M" and cmd[1:].isdigit():
            self.presets.add(int(cmd[1:]))
            return f"s{int(cmd[1:]):02d}"
        if cmd.startswith("PE0"):
            block = int(cmd[3])
            bits = sum(1 << (p - block * 40) for p in self.presets if p // 40 == block)
            return f"pE0{block}{bits:010X}"
        if cmd.startswith(("PTS", "Z", "F")):
            return cmd[0].lower() + cmd[1:]
        return "eR1"  # D6, UPVS, INS: not supported by this fake

    async def cam(self, request: web.Request) -> web.Response:
        cmd = request.query["cmd"]
        self.log.append(cmd)
        if cmd == "QID":
            return web.Response(text="OID:AW-HE38")
        if cmd == "QSV":
            return web.Response(text="OSV:V01.05")
        if cmd == "QGU":
            return web.Response(text=f"OGU:{self.gain:02X}")
        if cmd.startswith("OGU:"):
            self.gain = int(cmd[4:], 16)
            return web.Response(text=f"OGU:{self.gain:02X}")
        if cmd == "QAW":
            return web.Response(text="OAW:0")
        if cmd == "QSF":
            return web.Response(text="OSF:3")
        return web.Response(text=f"ER1:{cmd[:3]}")

    async def getinfo(self, request: web.Request) -> web.Response:
        return web.Response(text="MAC=08-00-23-AA-BB-CC\r\nSERIAL=X1234\r\nNAME=Studio Cam\r\n")

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/cgi-bin/aw_ptz", self.ptz)
        app.router.add_get("/cgi-bin/aw_cam", self.cam)
        app.router.add_get("/cgi-bin/getinfo", self.getinfo)
        return app


@pytest.fixture
async def camera():
    fake = FakeCamera()
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    async with aiohttp.ClientSession() as session:
        client = api.PanasonicAwClient(session, "127.0.0.1", port, min_interval=0)
        yield fake, client
    await runner.cleanup()


async def test_hash_is_percent_encoded(camera):
    fake, client = camera
    assert await client.ptz("O") == "p1"
    assert fake.raw_queries[-1] == "cmd=%23O&res=1"


async def test_info(camera):
    _, client = camera
    info = await client.async_get_info()
    assert info == {
        "model": "AW-HE38",
        "firmware": "V01.05",
        "mac": "08:00:23:aa:bb:cc",
        "serial": "X1234",
        "name": "Studio Cam",
    }


async def test_update_reads_state_and_learns_unsupported(camera):
    fake, client = camera
    data = await client.async_update()
    assert data[api.KEY_POWER] == 1
    assert (data[api.KEY_PAN], data[api.KEY_TILT]) == (0x8000, 0x8000)
    assert data[api.KEY_ZOOM] == 0x555
    assert data[api.KEY_IRIS] == 0xFFF
    assert data[api.KEY_AUTOFOCUS] == 1
    assert data[api.KEY_PRESETS] == {0, 1, 41}
    assert data[api.KEY_GAIN] == "80"
    assert data[api.KEY_SCENE] == "3"
    assert {api.KEY_NIGHT, api.KEY_PRESET_SPEED, api.KEY_COLOR_BAR} <= client.unsupported

    fake.log.clear()
    await client.async_update(data)
    assert "D6" not in fake.log and "QBR" not in fake.log


async def test_standby_only_reads_power(camera):
    fake, client = camera
    fake.power = 0
    data = await client.async_update()
    assert data == {api.KEY_POWER: 0}
    assert not client.unsupported


async def test_move_and_presets(camera):
    fake, client = camera
    await client.move(pan=-100, tilt=100)
    assert fake.log[-1] == "PTS0199"
    await client.move(zoom=50)
    assert fake.log[-1] == "Z74"
    await client.stop()
    assert fake.log[-3:] == ["PTS5050", "Z50", "F50"]

    data: dict = {}
    assert api.ingest(await client.ptz("R07"), data)
    assert data[api.KEY_LAST_PRESET] == 7
    await client.ptz("M99")
    assert 99 in fake.presets


async def test_command_error(camera):
    _, client = camera
    with pytest.raises(api.PanasonicAwCommandError) as err:
        await client.cam("OXX:1")
    assert err.value.code == 1


def test_conversions():
    assert api.percent_to_position(0) == 0x555
    assert api.percent_to_position(100) == 0xFFF
    assert api.position_to_percent(0xFFF) == 100
    assert api.pan_to_degrees(0x8000) == 0
    assert api.tilt_to_degrees(api.degrees_to_tilt(90)) == 90
    assert abs(api.pan_to_degrees(api.degrees_to_pan(-175)) + 175) < 0.1
    assert api.speed_to_code(0) == "50"
    assert api.speed_to_code(100) == "99"
    assert api.speed_to_code(-100) == "01"


def test_ingest_camdata_lines():
    data: dict = {}
    for line in ["p1", "OID:AW-HE40", "OGU:0x80", "axz7FF", "d30", "OSD:3A:0x01", "rubbish"]:
        api.ingest(line, data)
    assert data[api.KEY_GAIN] == "80"
    assert data[api.KEY_ZOOM] == 0x7FF
    assert data[api.KEY_AUTO_IRIS] == 0
    assert api.hex_value(data[api.KEY_DNR]) == 1
    api.ingest("XSF:4", data)
    assert data[api.KEY_SCENE] == "3"
    api.ingest("DCB:1", data)
    assert data[api.KEY_COLOR_BAR] == "1"
    api.ingest("OSA:87: 0x10", data)
    assert data["OSA:87"] == "10"
