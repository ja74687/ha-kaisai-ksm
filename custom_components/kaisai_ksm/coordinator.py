"""Koordynator odpytywania portalu Kaisai KSM."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    KaisaiAuthError,
    KaisaiConnectionError,
    KaisaiKsmApi,
    extract_options,
    parse_devices,
)
from .const import DOMAIN, OPTION_CODES

_LOGGER = logging.getLogger(__name__)


class KaisaiCoordinator(DataUpdateCoordinator[dict]):
    """Pobiera stan wszystkich urzadzen jednym zapytaniem."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: KaisaiKsmApi,
        scan_interval: int,
    ) -> None:
        self.api = api
        self.entry = entry
        # {gate_id: {code: {wartosc: etykieta}}} - pobierane raz, listy sie nie zmieniaja
        self._options_cache: dict[int, dict[str, dict[int, str]]] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict:
        try:
            raw = await self.api.async_get_data()
        except KaisaiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KaisaiConnectionError as err:
            raise UpdateFailed(str(err)) from err

        devices = parse_devices(raw)
        if not devices:
            raise UpdateFailed("Portal nie zwrocil zadnego urzadzenia")

        await self._async_attach_options(devices)
        return devices

    async def _async_attach_options(self, devices: dict) -> None:
        """Dolacz listy wartosci dla parametrow wybieranych z listy.

        Definicje pobieramy raz na bramke - nie zmieniaja sie miedzy odczytami,
        a kazde dodatkowe zapytanie to kolejna sekunda opoznienia.
        """
        for device in devices.values():
            gate_id = device.get("gate_id")
            if gate_id is None:
                continue
            if gate_id not in self._options_cache:
                definitions = await self.api.async_get_definitions(gate_id)
                per_code: dict[str, dict[int, str]] = {}
                if definitions is not None:
                    for code in OPTION_CODES:
                        options = extract_options(definitions, code)
                        if options:
                            per_code[code] = options
                            _LOGGER.debug("Opcje dla %s: %s", code, options)
                        else:
                            _LOGGER.debug(
                                "Portal nie oddal listy wartosci dla %s - bedzie mapa awaryjna",
                                code,
                            )
                self._options_cache[gate_id] = per_code
            device["options"] = self._options_cache[gate_id]
