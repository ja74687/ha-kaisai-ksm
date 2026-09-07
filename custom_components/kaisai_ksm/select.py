"""Encje select (tryb pracy) integracji Kaisai KSM."""

from __future__ import annotations

import logging
import time

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .api import as_int
from .const import DOMAIN, MODE_FALLBACK, SELECTS
from .coordinator import KaisaiCoordinator
from .entity import KaisaiEntity

_LOGGER = logging.getLogger(__name__)

# Tak samo jak przy nastawach temperatur: droga HA -> chmura -> bramka -> pompa
# trwa nawet kilka minut, wiec do czasu potwierdzenia pokazujemy wybor uzytkownika.
PENDING_TIMEOUT = 600
REFRESH_DELAYS = (15, 45, 90, 180, 300)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: KaisaiCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = []
    for device_key, device in coordinator.data.items():
        params = device.get("params", {})
        for code, description in SELECTS.items():
            param = params.get(code)
            if param and param.get("write"):
                entities.append(KaisaiSelect(coordinator, device_key, code, description))

    async_add_entities(entities)


class KaisaiSelect(KaisaiEntity, SelectEntity):
    """Parametr wybierany z listy, zapisywany przez API portalu."""

    def __init__(self, coordinator, device_key, code, description) -> None:
        super().__init__(coordinator, device_key)
        name, icon = description
        self._code = code
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{device_key}_{code}"
        self._pending: str | None = None
        self._pending_until: float = 0.0

    # ------------------------------------------------------------ slownik
    @property
    def _options_map(self) -> dict[int, str]:
        """Mapa {wartosc: etykieta}.

        Pierwszenstwo ma lista z portalu (device_definitions). Gdy jej nie ma,
        uzywamy mapy awaryjnej, uzupelnionej o etykiete biezacej wartosci -
        dzieki temu aktualny tryb zawsze jest na liscie, nawet jesli mapa
        awaryjna go nie zna.

        Numery trybow przepuszczamy przez as_int, bo portal potrafi oddac je
        jako napis. Wczesniej takie odczyty nie trafialy do mapy i encja
        pokazywala "unknown".
        """
        options: dict[int, str] = {}
        for raw_value, label in (self.device.get("options", {}).get(self._code) or {}).items():
            value = as_int(raw_value)
            if value is not None and isinstance(label, str):
                options[value] = label
        if not options:
            options = dict(MODE_FALLBACK)

        param = self.params.get(self._code) or {}
        value = as_int(param.get("value"))
        label = param.get("value_label")
        if value is not None and isinstance(label, str) and label:
            options[value] = label
        return options

    @property
    def options(self) -> list[str]:
        """Lista wyboru.

        Home Assistant pokazuje "unknown", gdy biezaca wartosc nie znajduje sie
        na liscie. Jesli portal przysyla etykiete, ktorej nie ma w definicjach,
        dopisujemy ja na koniec - lepiej pokazac prawdziwy tryb niz nic.
        """
        labels = list(self._options_map.values())
        current = self._portal_option()
        if isinstance(current, str) and current and current not in labels:
            labels.append(current)
        return labels

    # ------------------------------------------------------------- odczyt
    def _portal_option(self) -> str | None:
        param = self.params.get(self._code) or {}
        label = param.get("value_label")
        if isinstance(label, str) and label:
            return label
        value = as_int(param.get("value"))
        return self._options_map.get(value) if value is not None else None

    @property
    def _pending_active(self) -> bool:
        if self._pending is None:
            return False
        if time.monotonic() >= self._pending_until:
            _LOGGER.debug(
                "%s: portal nie potwierdzil trybu %s w zalozonym czasie",
                self._code,
                self._pending,
            )
            self._pending = None
            return False
        return True

    @property
    def current_option(self) -> str | None:
        if self._pending_active:
            return self._pending
        return self._portal_option()

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict[str, object] = {"oczekuje_na_potwierdzenie": self._pending_active}
        if self._pending_active:
            attrs["wartosc_zadana"] = self._pending
            attrs["wartosc_w_portalu"] = self._portal_option()
        if not self.device.get("options", {}).get(self._code):
            attrs["zrodlo_listy"] = "mapa awaryjna (portal nie oddal definicji)"
        else:
            attrs["zrodlo_listy"] = "definicje z portalu"
        # podglad surowych danych - przy rozjezdzie etykiet od razu widac,
        # co portal faktycznie przysyla
        param = self.params.get(self._code) or {}
        attrs["wartosc_surowa"] = param.get("value")
        attrs["etykieta_z_portalu"] = param.get("value_label")
        attrs["liczba_opcji"] = len(self.options)
        return attrs

    # -------------------------------------------------------------- zapis
    async def async_select_option(self, option: str) -> None:
        value = next(
            (v for v, label in self._options_map.items() if label == option), None
        )
        if value is None:
            raise HomeAssistantError(f"Nieznany tryb: {option}")

        device = self.device
        ok = await self.coordinator.api.async_set_param(
            device["gate_id"], device["device_id"], self._code, int(value)
        )
        if not ok:
            raise HomeAssistantError(
                "Portal Kaisai odrzucil zmiane trybu. Szczegoly w logu Home Assistant "
                "(wlacz poziom debug dla custom_components.kaisai_ksm)."
            )

        self._pending = option
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
        if self._pending is not None and self._portal_option() == self._pending:
            _LOGGER.debug("%s: portal potwierdzil tryb %s", self._code, self._pending)
            self._pending = None
        super()._handle_coordinator_update()
