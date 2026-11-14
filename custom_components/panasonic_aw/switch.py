"""Switches for Panasonic AW cameras."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    GAIN_0DB,
    GAIN_AUTO,
    KEY_AUTO_IRIS,
    KEY_AUTOFOCUS,
    KEY_COLOR_BAR,
    KEY_GAIN,
    KEY_INSTALL,
    KEY_NIGHT,
    KEY_POWER,
    KEY_TALLY,
    POWER_STANDBY,
    hex_value,
)
from .coordinator import PanasonicAwConfigEntry, PanasonicAwCoordinator
from .entity import PanasonicAwEntity


@dataclass(frozen=True, kw_only=True)
class AwSwitchDescription(SwitchEntityDescription):
    state_key: str
    interface: str  # "ptz" or "cam"
    on_command: str
    off_command: str
    on_value: int = 1
    needs_power: bool = True


SWITCHES: tuple[AwSwitchDescription, ...] = (
    AwSwitchDescription(
        key="power",
        state_key=KEY_POWER,
        interface="ptz",
        on_command="O1",
        off_command="O0",
        needs_power=False,
    ),
    AwSwitchDescription(
        key="tally",
        state_key=KEY_TALLY,
        interface="ptz",
        on_command="DA1",
        off_command="DA0",
    ),
    AwSwitchDescription(
        key="autofocus",
        state_key=KEY_AUTOFOCUS,
        interface="ptz",
        on_command="D11",
        off_command="D10",
    ),
    AwSwitchDescription(
        key="auto_iris",
        state_key=KEY_AUTO_IRIS,
        interface="ptz",
        on_command="D31",
        off_command="D30",
    ),
    AwSwitchDescription(
        key="auto_gain",
        state_key=KEY_GAIN,
        interface="cam",
        on_command=f"OGU:{GAIN_AUTO:02X}",
        off_command=f"OGU:{GAIN_0DB:02X}",
        on_value=GAIN_AUTO,
    ),
    AwSwitchDescription(
        key="night_mode",
        state_key=KEY_NIGHT,
        interface="ptz",
        on_command="D61",
        off_command="D60",
    ),
    AwSwitchDescription(
        key="color_bars",
        state_key=KEY_COLOR_BAR,
        interface="cam",
        on_command="DCB:1",
        off_command="DCB:0",
        entity_category=EntityCategory.CONFIG,
    ),
    AwSwitchDescription(
        key="ceiling_mount",
        state_key=KEY_INSTALL,
        interface="ptz",
        on_command="INS1",
        off_command="INS0",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanasonicAwConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        AwSwitch(coordinator, description)
        for description in SWITCHES
        if description.state_key not in coordinator.client.unsupported
    )


class AwSwitch(PanasonicAwEntity, SwitchEntity):
    entity_description: AwSwitchDescription

    def __init__(
        self, coordinator: PanasonicAwCoordinator, description: AwSwitchDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._state_key = description.state_key
        self._needs_power = description.needs_power

    @property
    def is_on(self) -> bool | None:
        description = self.entity_description
        value = self.coordinator.data.get(description.state_key)
        if description.state_key == KEY_POWER:
            return value is not None and value != POWER_STANDBY
        if description.interface == "cam":
            value = hex_value(value)
        return None if value is None else value == description.on_value

    async def _send(self, command: str) -> None:
        if self.entity_description.interface == "ptz":
            await self.coordinator.async_ptz(command)
        else:
            await self.coordinator.async_cam(command)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send(self.entity_description.on_command)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(self.entity_description.off_command)
