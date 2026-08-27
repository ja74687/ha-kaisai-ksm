"""Sensory integracji Kaisai KSM."""

from __future__ import annotations

import math

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CALCULATED,
    CODE_CURRENT,
    CODE_FLOW,
    CODE_INLET,
    CODE_OUTLET,
    CODE_VOLTAGE,
    CONF_PHASES,
    CONF_POWER_FACTOR,
    DEFAULT_PHASES,
    DEFAULT_POWER_FACTOR,
    DOMAIN,
    SENSORS,
    STATE_SENSORS,
)
from .coordinator import KaisaiCoordinator
from .entity import KaisaiEntity

# ciepło wlasciwe wody: 1 m3/h * 1 K = 1.163 kW
WATER_FACTOR = 1.163


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: KaisaiCoordinator = hass.data[DOMAIN][entry.entry_id]
    settings = {**entry.data, **entry.options}
    phases = settings.get(CONF_PHASES, DEFAULT_PHASES)
    power_factor = settings.get(CONF_POWER_FACTOR, DEFAULT_POWER_FACTOR)

    entities: list[SensorEntity] = []
    for device_key, device in coordinator.data.items():
        params = device.get("params", {})

        for code, description in SENSORS.items():
            if code in params:
                entities.append(KaisaiParamSensor(coordinator, device_key, code, description))

        for code, (name, icon) in STATE_SENSORS.items():
            if code in params:
                entities.append(KaisaiStateSensor(coordinator, device_key, code, name, icon))

        entities.append(KaisaiThermalPowerSensor(coordinator, device_key))
        entities.append(KaisaiElectricPowerSensor(coordinator, device_key, phases, power_factor))
        entities.append(KaisaiCopSensor(coordinator, device_key, phases, power_factor))

    async_add_entities(entities)


class KaisaiParamSensor(KaisaiEntity, SensorEntity):
    """Sensor odwzorowujacy jeden parametr z API."""

    def __init__(self, coordinator, device_key, code, description) -> None:
        super().__init__(coordinator, device_key)
        name, unit, device_class, state_class, icon, diagnostic = description
        self._code = code
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        if icon:
            self._attr_icon = icon
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_unique_id = f"{device_key}_{code}"

    @property
    def native_value(self):
        return self.param_value(self._code)


class KaisaiStateSensor(KaisaiEntity, SensorEntity):
    """Sensor dla parametrow z etykieta tekstowa (tryb, wl./wyl.)."""

    def __init__(self, coordinator, device_key, code, name, icon) -> None:
        super().__init__(coordinator, device_key)
        self._code = code
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{device_key}_{code}"

    @property
    def native_value(self):
        param = self.params.get(self._code)
        if not param:
            return None
        return param.get("value_label") or param.get("value")


class _KaisaiComputedSensor(KaisaiEntity, SensorEntity):
    """Baza dla sensorow wyliczanych."""

    _key = ""

    def __init__(self, coordinator, device_key) -> None:
        super().__init__(coordinator, device_key)
        name, unit, device_class, state_class, icon = CALCULATED[self._key]
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_icon = icon
        self._attr_unique_id = f"{device_key}_{self._key}"

    def _thermal_kw(self) -> float | None:
        flow = self.param_value(CODE_FLOW)
        inlet = self.param_value(CODE_INLET)
        outlet = self.param_value(CODE_OUTLET)
        if flow is None or inlet is None or outlet is None:
            return None
        try:
            flow = float(flow)
            delta = float(outlet) - float(inlet)
        except (TypeError, ValueError):
            return None
        if flow <= 0:
            return 0.0
        if delta <= 0:
            return 0.0
        return round(flow * WATER_FACTOR * delta, 2)

    def _electric_kw(self, phases: int, power_factor: float) -> float | None:
        voltage = self.param_value(CODE_VOLTAGE)
        current = self.param_value(CODE_CURRENT)
        if voltage is None or current is None:
            return None
        try:
            voltage = float(voltage)
            current = float(current)
        except (TypeError, ValueError):
            return None
        if current <= 0:
            return 0.0
        factor = math.sqrt(3) if phases == 3 else 1.0
        return round(voltage * current * factor * power_factor / 1000, 2)


class KaisaiThermalPowerSensor(_KaisaiComputedSensor):
    """Moc cieplna oddana do instalacji."""

    _key = "moc_cieplna"

    @property
    def native_value(self):
        return self._thermal_kw()


class KaisaiElectricPowerSensor(_KaisaiComputedSensor):
    """Pobor mocy liczony z napiecia i pradu."""

    _key = "moc_elektryczna"

    def __init__(self, coordinator, device_key, phases, power_factor) -> None:
        super().__init__(coordinator, device_key)
        self._phases = phases
        self._power_factor = power_factor

    @property
    def native_value(self):
        return self._electric_kw(self._phases, self._power_factor)


class KaisaiCopSensor(_KaisaiComputedSensor):
    """Chwilowy COP = moc cieplna / pobor mocy."""

    _key = "cop"

    def __init__(self, coordinator, device_key, phases, power_factor) -> None:
        super().__init__(coordinator, device_key)
        self._phases = phases
        self._power_factor = power_factor

    @property
    def native_value(self):
        thermal = self._thermal_kw()
        electric = self._electric_kw(self._phases, self._power_factor)
        if thermal is None or electric is None:
            return None
        # przy zatrzymanej sprezarce COP nie ma sensu
        if electric < 0.3 or thermal <= 0:
            return None
        return round(thermal / electric, 2)

    @property
    def extra_state_attributes(self):
        return {
            "moc_cieplna_kw": self._thermal_kw(),
            "moc_elektryczna_kw": self._electric_kw(self._phases, self._power_factor),
            "liczba_faz": self._phases,
            "wspolczynnik_mocy": self._power_factor,
        }
