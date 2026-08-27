"""Wspolna klasa bazowa encji Kaisai KSM."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import KaisaiCoordinator


class KaisaiEntity(CoordinatorEntity[KaisaiCoordinator]):
    """Encja przypieta do jednego urzadzenia (pompy)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KaisaiCoordinator, device_key: str) -> None:
        super().__init__(coordinator)
        self._device_key = device_key

    @property
    def device(self) -> dict:
        return self.coordinator.data.get(self._device_key, {})

    @property
    def params(self) -> dict:
        return self.device.get("params", {})

    def param_value(self, code: str):
        param = self.params.get(code)
        return param.get("value") if param else None

    @property
    def available(self) -> bool:
        return super().available and bool(self.device)

    @property
    def device_info(self) -> DeviceInfo:
        device = self.device
        serial = device.get("serial_number") or self._device_key
        return DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer=MANUFACTURER,
            name=device.get("code") or "Kaisai KSM",
            model=device.get("label"),
            serial_number=device.get("serial_number"),
            configuration_url="https://sterowanie.kaisai.com",
        )
