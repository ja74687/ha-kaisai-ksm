"""Encja switch (wlacznik pompy) integracji Kaisai KSM."""

from __future__ import annotations

import logging
import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, ONOFF_FALLBACK, SWITCHES
from .coordinator import KaisaiCoordinator
from .entity import KaisaiEntity

_LOGGER = logging.getLogger(__name__)

PENDING_TIMEOUT = 600
REFRESH_DELAYS = (15, 45, 90, 180, 300)

# etykiety, po ktorych rozpoznajemy stan w liscie wartosci z portalu
ON_LABELS = ("on", "wl", "wl.", "wlaczony", "wlaczona")
OFF_LABELS = ("off", "wyl", "wyl.", "wylaczony", "wylaczona")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: KaisaiCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = []
    for device_key, device in coordinator.data.items():
        params = device.get("params", {})
        for code, description in SWITCHES.items():
            param = params.get(code)
            if param and param.get("write"):
                entities.append(KaisaiSwitch(coordinator, device_key, code, description))

    async_add_entities(entities)


def _normalise(label: str) -> str:
    return label.strip().lower().replace("ł", "l").replace("ą", "a").replace("ę", "e")


class KaisaiSwitch(KaisaiEntity, SwitchEntity):
    """Wlacznik jednostki, zapisywany przez API portalu.

    UWAGA: wylaczenie zatrzymuje i ogrzewanie, i grzanie CWU. Zima trzymanie
    pompy w OFF grozi wychlodzeniem instalacji - encja niczego nie blokuje,
    ale warto o tym pamietac przy pisaniu automatyzacji.
    """

    def __init__(self, coordinator, device_key, code, description) -> None:
        super().__init__(coordinator, device_key)
        name, icon = description
        self._code = code
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{device_key}_{code}"
        self._pending: bool | None = None
        self._pending_until: float = 0.0

    # ------------------------------------------------------------ slownik
    @property
    def _values(self) -> dict[str, int]:
        """Mapa {"on": wartosc, "off": wartosc}.

        Pierwszenstwo ma lista z portalu; gdy jej nie ma, wchodzi mapa
        awaryjna. Dodatkowo uczymy sie z biezacego odczytu - jesli portal
        podaje etykiete dla aktualnej wartosci, wiemy, ktora to strona.
        """
        values = dict(ONOFF_FALLBACK)

        options = self.device.get("options", {}).get(self._code) or {}
        for value, label in options.items():
            text = _normalise(str(label))
            if text in ON_LABELS:
                values["on"] = value
            elif text in OFF_LABELS:
                values["off"] = value

        param = self.params.get(self._code) or {}
        value = param.get("value")
        label = param.get("value_label")
        if isinstance(value, int) and isinstance(label, str):
            text = _normalise(label)
            if text in ON_LABELS:
                values["on"] = value
            elif text in OFF_LABELS:
                values["off"] = value

        return values

    # ------------------------------------------------------------- odczyt
    def _portal_state(self) -> bool | None:
        param = self.params.get(self._code) or {}
        label = param.get("value_label")
        if isinstance(label, str):
            text = _normalise(label)
            if text in ON_LABELS:
                return True
            if text in OFF_LABELS:
                return False
        value = param.get("value")
        if isinstance(value, int):
            values = self._values
            if value == values.get("on"):
                return True
            if value == values.get("off"):
                return False
        return None

    @property
    def _pending_active(self) -> bool:
        if self._pending is None:
            return False
        if time.monotonic() >= self._pending_until:
            _LOGGER.debug("%s: portal nie potwierdzil stanu w zalozonym czasie", self._code)
            self._pending = None
            return False
        return True

    @property
    def is_on(self) -> bool | None:
        if self._pending_active:
            return self._pending
        return self._portal_state()

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict[str, object] = {"oczekuje_na_potwierdzenie": self._pending_active}
        if self._pending_active:
            attrs["stan_w_portalu"] = self._portal_state()
        if not self.device.get("options", {}).get(self._code):
            attrs["zrodlo_wartosci"] = "mapa awaryjna (portal nie oddal definicji)"
        else:
            attrs["zrodlo_wartosci"] = "definicje z portalu"
        return attrs

    # -------------------------------------------------------------- zapis
    async def async_turn_on(self, **kwargs) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_write(False)

    async def _async_write(self, state: bool) -> None:
        value = self._values.get("on" if state else "off")
        if value is None:
            raise HomeAssistantError("Nie znam wartosci dla tego stanu")

        device = self.device
        ok = await self.coordinator.api.async_set_param(
            device["gate_id"], device["device_id"], self._code, int(value)
        )
        if not ok:
            raise HomeAssistantError(
                "Portal Kaisai odrzucil zmiane stanu pompy. Szczegoly w logu Home "
                "Assistant (wlacz poziom debug dla custom_components.kaisai_ksm)."
            )

        self._pending = state
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
        if self._pending is not None and self._portal_state() == self._pending:
            _LOGGER.debug("%s: portal potwierdzil stan %s", self._code, self._pending)
            self._pending = None
        super()._handle_coordinator_update()
