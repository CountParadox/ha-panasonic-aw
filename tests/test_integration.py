"""Set the integration up inside Home Assistant against the simulated camera.

Skipped unless pytest-homeassistant-custom-component is installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aiohttp import web  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402
from test_api import FakeCamera  # noqa: E402

DOMAIN = "panasonic_aw"


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations, socket_enabled):
    """Load custom_components and allow the loopback test server."""


@pytest.fixture
async def fake_camera():
    fake = FakeCamera()
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fake.port = runner.addresses[0][1]
    yield fake
    await runner.cleanup()


async def _setup(hass: HomeAssistant, fake: FakeCamera) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Studio Cam",
        unique_id="08:00:23:aa:bb:cc",
        data={"host": "127.0.0.1", "port": fake.port},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant, fake_camera: FakeCamera) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake_camera.port}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Studio Cam"
    assert result["result"].unique_id == "08:00:23:aa:bb:cc"
    await hass.async_block_till_done()


async def test_config_flow_cannot_connect(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": 9}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_entities_and_actions(hass: HomeAssistant, fake_camera: FakeCamera) -> None:
    await _setup(hass, fake_camera)

    assert hass.states.get("camera.studio_cam") is not None
    assert hass.states.get("switch.studio_cam_power").state == "on"
    assert hass.states.get("switch.studio_cam_autofocus").state == "on"
    assert hass.states.get("switch.studio_cam_auto_gain").state == "on"
    assert hass.states.get("number.studio_cam_zoom").state == "0.0"
    assert hass.states.get("number.studio_cam_pan").state == "0.0"
    assert hass.states.get("select.studio_cam_scene").state == "Full auto"
    assert hass.states.get("select.studio_cam_white_balance").state == "ATW"
    # The fake rejects these, so no entity is created for them.
    assert hass.states.get("switch.studio_cam_night_mode") is None
    assert hass.states.get("switch.studio_cam_color_bars") is None
    assert hass.states.get("number.studio_cam_preset_speed") is None

    preset = hass.states.get("select.studio_cam_preset")
    assert preset.state == "Preset 1"
    assert preset.attributes["options"] == ["Preset 1", "Preset 2", "Preset 42"]
    assert preset.attributes["saved_presets"] == [1, 2, 42]

    target = {"entity_id": "camera.studio_cam"}
    await hass.services.async_call(
        DOMAIN, "preset_save", {**target, "preset": 7}, blocking=True
    )
    assert 6 in fake_camera.presets
    assert "Preset 7" in hass.states.get("select.studio_cam_preset").attributes["options"]

    await hass.services.async_call(
        DOMAIN, "preset_recall", {**target, "preset": 42}, blocking=True
    )
    assert fake_camera.last_preset == 41
    assert hass.states.get("select.studio_cam_preset").state == "Preset 42"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.studio_cam_preset", "option": "Preset 2"},
        blocking=True,
    )
    assert fake_camera.last_preset == 1

    await hass.services.async_call(DOMAIN, "ptz", {**target, "pan": -100}, blocking=True)
    assert fake_camera.log[-1] == "PTS0150"
    await hass.services.async_call(DOMAIN, "stop", target, blocking=True)
    assert "PTS5050" in fake_camera.log

    await hass.services.async_call(
        DOMAIN, "move_absolute", {**target, "pan": 0, "tilt": 90, "zoom": 100}, blocking=True
    )
    assert (fake_camera.pan, fake_camera.tilt, fake_camera.zoom) == (0x8000, 0x5555, 0xFFF)
    assert hass.states.get("number.studio_cam_tilt").state == "90.0"
    assert hass.states.get("number.studio_cam_zoom").state == "100.0"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.studio_cam_gain", "value": 12}, blocking=True
    )
    assert fake_camera.gain == 0x14
    assert hass.states.get("switch.studio_cam_auto_gain").state == "off"
    assert hass.states.get("number.studio_cam_gain").state == "12.0"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.studio_cam_autofocus"}, blocking=True
    )
    assert fake_camera.autofocus == 0

    response = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {**target, "interface": "cam", "command": "QID"},
        blocking=True,
        return_response=True,
    )
    assert response == {"camera.studio_cam": {"response": "OID:AW-HE38"}}

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.studio_cam_power"}, blocking=True
    )
    assert hass.states.get("switch.studio_cam_power").state == "off"
    assert hass.states.get("number.studio_cam_zoom").state == "unavailable"


async def test_unload(hass: HomeAssistant, fake_camera: FakeCamera) -> None:
    entry = await _setup(hass, fake_camera)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
