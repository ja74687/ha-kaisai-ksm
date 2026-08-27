"""Encje number (nastawy temperatur) integracji Kaisai KSM."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NUMBERS
from .coordinator import KaisaiCoordinator
from .entity import KaisaiEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: KaisaiCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = []
    for device_key, device in coordinator.data.items():
        params = device.get("params", {})
        for code, description in NUMBERS.items():
            param = params.get(code)
            if param and param.get("write"):
                entities.append(KaisaiNumber(coordinator, device_key, code, description))

    async_add_entities(entities)


class KaisaiNumber(KaisaiEntity, NumberEntity):
    """Nastawa temperatury zapisywana przez API portalu."""

    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(self, coordinator, device_key, code, description) -> None:
        super().__init__(coordinator, device_key)
        name, unit, device_class, icon = description
        self._code = code
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_icon = icon
        self._attr_unique_id = f"{device_key}_{code}"

    @property
    def native_value(self) -> float | None:
        value = self.param_value(self._code)
        return float(value) if value is not None else None

    @property
    def native_min_value(self) -> float:
        param = self.params.get(self._code) or {}
        minimum = param.get("min")
        return float(minimum) if minimum is not None else 15.0

    @property
    def native_max_value(self) -> float:
        param = self.params.get(self._code) or {}
        maximum = param.get("max")
        return float(maximum) if maximum is not None else 65.0

    async def async_set_native_value(self, value: float) -> None:
        device = self.device
        ok = await self.coordinator.api.async_set_param(
            device["gate_id"], device["device_id"], self._code, int(value)
        )
        if not ok:
            raise HomeAssistantError(
                "Portal Kaisai odrzucil zapis nastawy. Szczegoly w logu Home Assistant "
                "(wlacz poziom debug dla custom_components.kaisai_ksm)."
            )
        await self.coordinator.async_request_refresh()
