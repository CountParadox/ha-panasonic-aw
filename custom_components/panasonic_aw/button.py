"""Buttons for Panasonic AW cameras."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import NUDGE_SECONDS, NUDGE_SPEED
from .coordinator import PanasonicAwConfigEntry, PanasonicAwCoordinator
from .entity import PanasonicAwEntity


@dataclass(frozen=True, kw_only=True)
class AwButtonDescription(ButtonEntityDescription):
    press: Callable[[PanasonicAwCoordinator], Any]


def _nudge(**axes: float) -> Callable[[PanasonicAwCoordinator], Any]:
    return lambda c: c.async_move(duration=NUDGE_SECONDS, **axes)


BUTTONS: tuple[AwButtonDescription, ...] = (
    AwButtonDescription(key="pan_left", press=_nudge(pan=-NUDGE_SPEED)),
    AwButtonDescription(key="pan_right", press=_nudge(pan=NUDGE_SPEED)),
    AwButtonDescription(key="tilt_up", press=_nudge(tilt=NUDGE_SPEED)),
    AwButtonDescription(key="tilt_down", press=_nudge(tilt=-NUDGE_SPEED)),
    AwButtonDescription(key="zoom_in", press=_nudge(zoom=NUDGE_SPEED)),
    AwButtonDescription(key="zoom_out", press=_nudge(zoom=-NUDGE_SPEED)),
    AwButtonDescription(key="stop", press=lambda c: c.async_stop()),
    AwButtonDescription(key="home", press=lambda c: c.async_ptz("APC80008000")),
    AwButtonDescription(
        key="one_touch_autofocus", press=lambda c: c.async_cam("OSE:69:1")
    ),
    AwButtonDescription(
        key="save_preset", press=lambda c: c.async_preset_save(c.preset_slot)
    ),
    AwButtonDescription(key="auto_white_balance", press=lambda c: c.async_cam("OWS")),
    AwButtonDescription(key="auto_black_balance", press=lambda c: c.async_cam("OAS")),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanasonicAwConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(AwButton(coordinator, description) for description in BUTTONS)


class AwButton(PanasonicAwEntity, ButtonEntity):
    entity_description: AwButtonDescription

    def __init__(
        self, coordinator: PanasonicAwCoordinator, description: AwButtonDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press(self.coordinator)
