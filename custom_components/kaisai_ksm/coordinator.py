"""Koordynator odpytywania portalu Kaisai KSM."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import KaisaiAuthError, KaisaiConnectionError, KaisaiKsmApi, parse_devices
from .const import DOMAIN

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
        return devices
