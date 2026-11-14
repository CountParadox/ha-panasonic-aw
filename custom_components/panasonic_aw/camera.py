"""Camera entity (snapshot + RTSP stream) and the PTZ/preset actions."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import voluptuous as vol

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    PanasonicAwError,
    degrees_to_pan,
    degrees_to_tilt,
    percent_to_position,
)
from .const import (
    CONF_RTSP_PORT,
    CONF_SNAPSHOT_RESOLUTION,
    DEFAULT_RTSP_PORT,
    DEFAULT_SNAPSHOT_RESOLUTION,
    MAX_MOVE_SECONDS,
    PRESET_COUNT,
    SERVICE_MOVE_ABSOLUTE,
    SERVICE_PRESET_DELETE,
    SERVICE_PRESET_RECALL,
    SERVICE_PRESET_SAVE,
    SERVICE_PTZ,
    SERVICE_SEND_COMMAND,
    SERVICE_STOP,
)
from .coordinator import PanasonicAwConfigEntry, PanasonicAwCoordinator
from .entity import PanasonicAwEntity

_SPEED = vol.All(vol.Coerce(float), vol.Range(min=-100, max=100))
_PRESET = {
    vol.Required("preset"): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=PRESET_COUNT)
    )
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanasonicAwConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([AwCamera(entry.runtime_data)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_PTZ,
        {
            vol.Optional("pan"): _SPEED,
            vol.Optional("tilt"): _SPEED,
            vol.Optional("zoom"): _SPEED,
            vol.Optional("focus"): _SPEED,
            vol.Optional("duration"): vol.All(
                vol.Coerce(float), vol.Range(min=0.05, max=MAX_MOVE_SECONDS)
            ),
        },
        "async_ptz",
    )
    platform.async_register_entity_service(SERVICE_STOP, None, "async_stop")
    platform.async_register_entity_service(
        SERVICE_PRESET_RECALL, _PRESET, "async_preset_recall"
    )
    platform.async_register_entity_service(
        SERVICE_PRESET_SAVE, _PRESET, "async_preset_save"
    )
    platform.async_register_entity_service(
        SERVICE_PRESET_DELETE, _PRESET, "async_preset_delete"
    )
    platform.async_register_entity_service(
        SERVICE_MOVE_ABSOLUTE,
        {
            vol.Optional("pan"): vol.All(
                vol.Coerce(float), vol.Range(min=-180, max=180)
            ),
            vol.Optional("tilt"): vol.All(
                vol.Coerce(float), vol.Range(min=-90, max=90)
            ),
            vol.Optional("zoom"): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=100)
            ),
        },
        "async_move_absolute",
    )
    platform.async_register_entity_service(
        SERVICE_SEND_COMMAND,
        {
            vol.Required("interface"): vol.In(["ptz", "cam"]),
            vol.Required("command"): cv.string,
        },
        "async_send_command",
        supports_response=SupportsResponse.OPTIONAL,
    )


class AwCamera(PanasonicAwEntity, Camera):
    """Live view of the camera."""

    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM
    _needs_power = False

    def __init__(self, coordinator: PanasonicAwCoordinator) -> None:
        PanasonicAwEntity.__init__(self, coordinator, "camera")
        Camera.__init__(self)
        self._attr_translation_key = None

    @property
    def _options(self) -> dict[str, Any]:
        return dict(self.coordinator.config_entry.options)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        if not self.coordinator.powered:
            return None
        try:
            return await self.coordinator.client.snapshot(
                self._options.get(CONF_SNAPSHOT_RESOLUTION, DEFAULT_SNAPSHOT_RESOLUTION)
            )
        except PanasonicAwError:
            return None

    async def stream_source(self) -> str | None:
        data = self.coordinator.config_entry.data
        port = self._options.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)
        auth = ""
        if data.get(CONF_USERNAME):
            auth = (
                f"{quote(data[CONF_USERNAME], safe='')}:"
                f"{quote(data.get(CONF_PASSWORD, ''), safe='')}@"
            )
        return f"rtsp://{auth}{data[CONF_HOST]}:{port}/mediainput/h264/stream_1"

    # --- Actions ----------------------------------------------------------
    async def async_ptz(
        self,
        pan: float | None = None,
        tilt: float | None = None,
        zoom: float | None = None,
        focus: float | None = None,
        duration: float | None = None,
    ) -> None:
        if pan is None and tilt is None and zoom is None and focus is None:
            raise HomeAssistantError("Give at least one of pan, tilt, zoom or focus")
        await self.coordinator.async_move(pan, tilt, zoom, focus, duration)

    async def async_stop(self) -> None:
        await self.coordinator.async_stop()

    async def async_preset_recall(self, preset: int) -> None:
        await self.coordinator.async_preset_recall(preset)

    async def async_preset_save(self, preset: int) -> None:
        await self.coordinator.async_preset_save(preset)

    async def async_preset_delete(self, preset: int) -> None:
        await self.coordinator.async_preset_delete(preset)

    async def async_move_absolute(
        self,
        pan: float | None = None,
        tilt: float | None = None,
        zoom: float | None = None,
    ) -> None:
        if pan is not None or tilt is not None:
            await self.coordinator.async_move_absolute(
                None if pan is None else degrees_to_pan(pan),
                None if tilt is None else degrees_to_tilt(tilt),
            )
        if zoom is not None:
            await self.coordinator.async_ptz(f"AXZ{percent_to_position(zoom):03X}")

    async def async_send_command(self, interface: str, command: str) -> ServiceResponse:
        """Send any raw protocol command and return the camera's answer."""
        if interface == "ptz":
            response = await self.coordinator.async_ptz(command)
        else:
            response = await self.coordinator.async_cam(command)
        return {"response": response}
