"""Base entity for Panasonic AW cameras."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import PanasonicAwCoordinator


class PanasonicAwEntity(CoordinatorEntity[PanasonicAwCoordinator]):
    """Common device info and availability."""

    _attr_has_entity_name = True
    # State key this entity needs; it is unavailable while the camera is in standby.
    _state_key: str | None = None
    _needs_power = True

    def __init__(self, coordinator: PanasonicAwCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        info = coordinator.client.info
        device_id = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=info.get("model"),
            name=entry.title,
            sw_version=info.get("firmware"),
            serial_number=info.get("serial"),
            configuration_url=f"http://{entry.data[CONF_HOST]}",
            connections=(
                {(CONNECTION_NETWORK_MAC, info["mac"])} if info.get("mac") else set()
            ),
        )

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self._needs_power and not self.coordinator.powered:
            return False
        if self._state_key is not None:
            return self._state_key in self.coordinator.data
        return True
