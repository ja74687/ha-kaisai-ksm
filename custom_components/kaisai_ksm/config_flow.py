"""Kreator konfiguracji integracji Kaisai KSM."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import KaisaiAuthError, KaisaiConnectionError, KaisaiKsmApi, parse_devices
from .const import (
    CONF_HOST,
    CONF_PHASES,
    CONF_POWER_FACTOR,
    CONF_SCAN_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_PHASES,
    DEFAULT_POWER_FACTOR,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Optional(CONF_PHASES, default=DEFAULT_PHASES): vol.In([1, 3]),
        vol.Optional(CONF_POWER_FACTOR, default=DEFAULT_POWER_FACTOR): vol.All(
            vol.Coerce(float), vol.Range(min=0.5, max=1.0)
        ),
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=30, max=3600)
        ),
    }
)


class KaisaiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Obsluga dodawania integracji."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_create_clientsession(self.hass)
            api = KaisaiKsmApi(
                session=session,
                host=user_input.get(CONF_HOST, DEFAULT_HOST),
                email=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await api.async_login()
                data = await api.async_get_data()
                devices = parse_devices(data)
            except KaisaiAuthError:
                errors["base"] = "invalid_auth"
            except KaisaiConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Nieoczekiwany blad podczas konfiguracji")
                errors["base"] = "unknown"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    await self.async_set_unique_id(str(data.get("id")))
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Kaisai KSM ({user_input[CONF_EMAIL]})",
                        data=user_input,
                    )

        return self.async_show_form(
            step_id="user", data_schema=SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return KaisaiOptionsFlow()


class KaisaiOptionsFlow(OptionsFlow):
    """Zmiana ustawien po dodaniu integracji."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PHASES, default=current.get(CONF_PHASES, DEFAULT_PHASES)
                ): vol.In([1, 3]),
                vol.Optional(
                    CONF_POWER_FACTOR,
                    default=current.get(CONF_POWER_FACTOR, DEFAULT_POWER_FACTOR),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=1.0)),
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
