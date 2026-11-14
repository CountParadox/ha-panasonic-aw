"""Selects for Panasonic AW cameras."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    KEY_DNR,
    KEY_LAST_PRESET,
    KEY_PRESETS,
    KEY_SCENE,
    KEY_WHITE_BALANCE,
    hex_value,
)
from .const import PRESET_COUNT
from .coordinator import PanasonicAwConfigEntry, PanasonicAwCoordinator
from .entity import PanasonicAwEntity


@dataclass(frozen=True, kw_only=True)
class AwSelectDescription(SelectEntityDescription):
    state_key: str
    interface: str  # "ptz" or "cam"
    command: str  # format string taking the numeric value
    values: dict[int, str]  # value the camera reports -> option
    command_offset: int = 0  # added to the reported value when sending
    aliases: dict[int, int] | None = None  # other reported values -> canonical


# Value tables are those of the AW-HE40 generation (HE38, HE40, HE42, UE70).
SELECTS: tuple[AwSelectDescription, ...] = (
    AwSelectDescription(
        key="scene",
        state_key=KEY_SCENE,
        interface="cam",
        # The query answers 0-3, the control command takes 1-4.
        command="XSF:{:X}",
        command_offset=1,
        values={0: "Manual 1", 1: "Manual 2", 2: "Manual 3", 3: "Full auto"},
    ),
    AwSelectDescription(
        key="white_balance",
        state_key=KEY_WHITE_BALANCE,
        interface="cam",
        command="OAW:{:X}",
        values={0: "ATW", 1: "AWB A", 2: "AWB B", 4: "3200K", 5: "5600K", 9: "VAR"},
        aliases={3: 0},
    ),
    AwSelectDescription(
        key="noise_reduction",
        state_key=KEY_DNR,
        interface="cam",
        command="OSD:3A:{:02X}",
        values={0: "Off", 1: "Low", 2: "High"},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanasonicAwConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [AwPresetSelect(coordinator)]
    entities.extend(
        AwSelect(coordinator, description)
        for description in SELECTS
        if description.state_key not in coordinator.client.unsupported
    )
    async_add_entities(entities)


class AwSelect(PanasonicAwEntity, SelectEntity):
    entity_description: AwSelectDescription

    def __init__(
        self, coordinator: PanasonicAwCoordinator, description: AwSelectDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._state_key = description.state_key
        self._attr_options = list(description.values.values())

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.get(self._state_key)
        if self.entity_description.interface == "cam":
            value = hex_value(value)
        description = self.entity_description
        if description.aliases:
            value = description.aliases.get(value, value)
        return description.values.get(value)

    async def async_select_option(self, option: str) -> None:
        description = self.entity_description
        value = next(k for k, v in description.values.items() if v == option)
        command = description.command.format(value + description.command_offset)
        if description.interface == "ptz":
            await self.coordinator.async_ptz(command)
        else:
            await self.coordinator.async_cam(command)


class AwPresetSelect(PanasonicAwEntity, SelectEntity):
    """Recall a preset; shows the preset recalled last.

    Lists the presets saved in the camera, or all of them when the camera does
    not report which ones are saved.
    """

    def __init__(self, coordinator: PanasonicAwCoordinator) -> None:
        super().__init__(coordinator, "preset")

    @staticmethod
    def _name(index: int) -> str:
        return f"Preset {index + 1}"

    @property
    def options(self) -> list[str]:
        saved = self.coordinator.data.get(KEY_PRESETS)
        indexes = set(saved) if saved else set(range(PRESET_COUNT))
        last = self.coordinator.data.get(KEY_LAST_PRESET)
        if last is not None:
            indexes.add(last)
        return [self._name(i) for i in sorted(indexes)]

    @property
    def current_option(self) -> str | None:
        last = self.coordinator.data.get(KEY_LAST_PRESET)
        return None if last is None else self._name(last)

    @property
    def extra_state_attributes(self) -> dict[str, list[int]] | None:
        saved = self.coordinator.data.get(KEY_PRESETS)
        if saved is None:
            return None
        return {"saved_presets": sorted(i + 1 for i in saved)}

    async def async_select_option(self, option: str) -> None:
        try:
            preset = int(option.rsplit(" ", 1)[-1])
        except ValueError as err:
            raise ServiceValidationError(f"Unknown preset: {option}") from err
        await self.coordinator.async_preset_recall(preset)
