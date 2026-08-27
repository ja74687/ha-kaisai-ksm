"""Encje number (nastawy temperatur) integracji Kaisai KSM."""

from __future__ import annotations

import logging
import time

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, NUMBERS
from .coordinator import KaisaiCoordinator
from .entity import KaisaiEntity

_LOGGER = logging.getLogger(__name__)

# Droga zmiany: HA -> chmura Kaisai -> bramka KSM -> pompa -> z powrotem do
# portalu. Zanim portal poda nowa wartosc, mija nawet kilka minut. Przez ten
# czas pokazujemy wartosc zadana, zeby kafelek nie wracal do starej.
PENDING_TIMEOUT = 600
# dodatkowe odswiezenia po zapisie, zeby zlapac potwierdzenie wczesniej
REFRESH_DELAYS = (15, 45, 90, 180, 300)


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
        self._pending: float | None = None
        self._pending_until: float = 0.0

    # ------------------------------------------------------------- odczyt
    def _portal_value(self) -> float | None:
        value = self.param_value(self._code)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def _pending_active(self) -> bool:
        if self._pending is None:
            return False
        if time.monotonic() >= self._pending_until:
            _LOGGER.debug(
                "%s: portal nie potwierdzil wartosci %s w zalozonym czasie",
                self._code,
                self._pending,
            )
            self._pending = None
            return False
        return True

    @property
    def native_value(self) -> float | None:
        if self._pending_active:
            return self._pending
        return self._portal_value()

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

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict[str, object] = {"oczekuje_na_potwierdzenie": self._pending_active}
        if self._pending_active:
            attrs["wartosc_zadana"] = self._pending
            attrs["wartosc_w_portalu"] = self._portal_value()
        return attrs

    # -------------------------------------------------------------- zapis
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

        # pokaz nowa wartosc od razu, zamiast czekac na potwierdzenie z chmury
        self._pending = float(value)
        self._pending_until = time.monotonic() + PENDING_TIMEOUT
        self.async_write_ha_state()

        for delay in REFRESH_DELAYS:
            async_call_later(self.hass, delay, self._async_scheduled_refresh)

    @callback
    def _async_scheduled_refresh(self, _now) -> None:
        if self._pending is not None:
            self.hass.async_create_task(self.coordinator.async_request_refresh())

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._pending is not None and self._portal_value() == self._pending:
            _LOGGER.debug("%s: portal potwierdzil wartosc %s", self._code, self._pending)
            self._pending = None
        super()._handle_coordinator_update()
