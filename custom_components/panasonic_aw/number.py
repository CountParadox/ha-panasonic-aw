"""Numbers for Panasonic AW cameras."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import DEGREE, PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    GAIN_0DB,
    GAIN_AUTO,
    KEY_FOCUS,
    KEY_GAIN,
    KEY_IRIS,
    KEY_PAN,
    KEY_PRESET_SPEED,
    KEY_TILT,
    KEY_ZOOM,
    degrees_to_pan,
    degrees_to_tilt,
    hex_value,
    pan_to_degrees,
    percent_to_position,
    position_to_percent,
    tilt_to_degrees,
)
from .coordinator import PanasonicAwConfigEntry, PanasonicAwCoordinator
from .entity import PanasonicAwEntity


def _gain_db(value: Any) -> float | None:
    raw = hex_value(value)
    if raw is None or raw == GAIN_AUTO:
        return None
    return float(raw - GAIN_0DB)


@dataclass(frozen=True, kw_only=True)
class AwNumberDescription(NumberEntityDescription):
    state_key: str
    to_native: Callable[[Any], float | None]
    set_value: Callable[[PanasonicAwCoordinator, float], Any]


NUMBERS: tuple[AwNumberDescription, ...] = (
    AwNumberDescription(
        key="zoom",
        state_key=KEY_ZOOM,
        native_min_value=0,
        native_max_value=100,
        native_step=0.1,
        native_unit_of_measurement=PERCENTAGE,
        to_native=position_to_percent,
        set_value=lambda c, v: c.async_ptz(f"AXZ{percent_to_position(v):03X}"),
    ),
    AwNumberDescription(
        key="focus",
        state_key=KEY_FOCUS,
        native_min_value=0,
        native_max_value=100,
        native_step=0.1,
        native_unit_of_measurement=PERCENTAGE,
        to_native=position_to_percent,
        set_value=lambda c, v: c.async_ptz(f"AXF{percent_to_position(v):03X}"),
    ),
    AwNumberDescription(
        key="iris",
        state_key=KEY_IRIS,
        native_min_value=0,
        native_max_value=100,
        native_step=0.1,
        native_unit_of_measurement=PERCENTAGE,
        to_native=position_to_percent,
        set_value=lambda c, v: c.async_ptz(f"AXI{percent_to_position(v):03X}"),
    ),
    AwNumberDescription(
        key="pan",
        state_key=KEY_PAN,
        native_min_value=-175,
        native_max_value=175,
        native_step=0.1,
        native_unit_of_measurement=DEGREE,
        to_native=pan_to_degrees,
        set_value=lambda c, v: c.async_move_absolute(pan_raw=degrees_to_pan(v)),
    ),
    AwNumberDescription(
        key="tilt",
        state_key=KEY_TILT,
        native_min_value=-30,
        native_max_value=90,
        native_step=0.1,
        native_unit_of_measurement=DEGREE,
        to_native=tilt_to_degrees,
        set_value=lambda c, v: c.async_move_absolute(tilt_raw=degrees_to_tilt(v)),
    ),
    AwNumberDescription(
        key="gain",
        state_key=KEY_GAIN,
        native_min_value=0,
        native_max_value=48,
        # The HE40 generation takes 3 dB steps up to 48 dB.
        native_step=3,
        native_unit_of_measurement="dB",
        to_native=_gain_db,
        set_value=lambda c, v: c.async_cam(f"OGU:{GAIN_0DB + round(v / 3) * 3:02X}"),
    ),
    AwNumberDescription(
        key="preset_speed",
        state_key=KEY_PRESET_SPEED,
        native_min_value=250,
        native_max_value=999,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        to_native=float,
        set_value=lambda c, v: c.async_ptz(f"UPVS{round(v):03d}"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanasonicAwConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        AwNumber(coordinator, description)
        for description in NUMBERS
        if description.state_key not in coordinator.client.unsupported
    )


class AwNumber(PanasonicAwEntity, NumberEntity):
    entity_description: AwNumberDescription
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator: PanasonicAwCoordinator, description: AwNumberDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._state_key = description.state_key

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.get(self._state_key)
        if value is None:
            return None
        return self.entity_description.to_native(value)

    async def async_set_native_value(self, value: float) -> None:
        await self.entity_description.set_value(self.coordinator, value)
