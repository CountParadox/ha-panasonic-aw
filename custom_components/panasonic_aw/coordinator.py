"""Data update coordinator for Panasonic AW cameras."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    KEY_PAN,
    KEY_POWER,
    KEY_PRESETS,
    KEY_TILT,
    POWER_ON,
    POWER_TRANSITION,
    PT_CENTRE,
    PanasonicAwAuthError,
    PanasonicAwClient,
    PanasonicAwError,
    ingest,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, MAX_MOVE_SECONDS

_LOGGER = logging.getLogger(__name__)

type PanasonicAwConfigEntry = ConfigEntry[PanasonicAwCoordinator]


class PanasonicAwCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll one camera and send commands to it."""

    config_entry: PanasonicAwConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PanasonicAwConfigEntry,
        client: PanasonicAwClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        # Preset number (1-100) the "Save preset" button writes to.
        self.preset_slot = 1

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.async_update(self.data)
        except PanasonicAwAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PanasonicAwError as err:
            raise UpdateFailed(str(err)) from err

    @property
    def powered(self) -> bool:
        return bool(self.data) and self.data.get(KEY_POWER) == POWER_ON

    def _apply(self, response: str) -> None:
        """Merge a command response into the state and tell the entities."""
        data = dict(self.data or {})
        if ingest(response, data):
            self.async_set_updated_data(data)
            if data.get(KEY_POWER) == POWER_TRANSITION:
                self.hass.loop.call_later(
                    8, lambda: self.hass.async_create_task(self.async_request_refresh())
                )

    async def async_ptz(self, command: str) -> str:
        """Send a pan/tilt head command."""
        try:
            response = await self.client.ptz(command)
        except PanasonicAwError as err:
            raise HomeAssistantError(str(err)) from err
        self._apply(response)
        return response

    async def async_cam(self, command: str) -> str:
        """Send a camera command."""
        try:
            response = await self.client.cam(command)
        except PanasonicAwError as err:
            raise HomeAssistantError(str(err)) from err
        self._apply(response)
        return response

    async def async_move(
        self,
        pan: float | None = None,
        tilt: float | None = None,
        zoom: float | None = None,
        focus: float | None = None,
        duration: float | None = None,
    ) -> None:
        """Continuous move (-100..100 per axis); stop again after ``duration``."""
        try:
            await self.client.move(pan, tilt, zoom, focus)
            if duration:
                await asyncio.sleep(min(duration, MAX_MOVE_SECONDS))
                await self.client.move(
                    pan=None if pan is None and tilt is None else 0,
                    tilt=None if pan is None and tilt is None else 0,
                    zoom=None if zoom is None else 0,
                    focus=None if focus is None else 0,
                )
        except PanasonicAwError as err:
            raise HomeAssistantError(str(err)) from err
        if duration:
            await self.async_request_refresh()

    async def async_stop(self) -> None:
        try:
            await self.client.stop()
        except PanasonicAwError as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_request_refresh()

    async def async_move_absolute(
        self, pan_raw: int | None = None, tilt_raw: int | None = None
    ) -> None:
        """Move to an absolute pan/tilt position (raw protocol units)."""
        data = self.data or {}
        pan_raw = data.get(KEY_PAN, PT_CENTRE) if pan_raw is None else pan_raw
        tilt_raw = data.get(KEY_TILT, PT_CENTRE) if tilt_raw is None else tilt_raw
        await self.async_ptz(f"APC{pan_raw:04X}{tilt_raw:04X}")

    async def async_preset_recall(self, preset: int) -> None:
        """Recall preset 1-100."""
        await self.async_ptz(f"R{preset - 1:02d}")

    async def async_preset_save(self, preset: int) -> None:
        """Save the current position as preset 1-100."""
        await self.async_ptz(f"M{preset - 1:02d}")
        self._mark_preset(preset - 1, True)

    async def async_preset_delete(self, preset: int) -> None:
        """Delete preset 1-100."""
        await self.async_ptz(f"C{preset - 1:02d}")
        self._mark_preset(preset - 1, False)

    def _mark_preset(self, index: int, saved: bool) -> None:
        data = dict(self.data or {})
        if KEY_PRESETS not in data:
            return
        presets = set(data[KEY_PRESETS])
        (presets.add if saved else presets.discard)(index)
        data[KEY_PRESETS] = presets
        self.async_set_updated_data(data)
