"""Stale dla integracji Kaisai KSM."""

from __future__ import annotations

from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)

DOMAIN = "kaisai_ksm"
MANUFACTURER = "Kaisai"

DEFAULT_HOST = "https://sterowanie.kaisai.com"
DEFAULT_LOCALE = "pl"
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_PHASES = 3
DEFAULT_POWER_FACTOR = 0.95

CONF_HOST = "host"
CONF_PHASES = "phases"
CONF_POWER_FACTOR = "power_factor"
CONF_SCAN_INTERVAL = "scan_interval"

# --- kody parametrow uzywane w obliczeniach ---------------------------------
CODE_FLOW = "__rm_t39_water_flow_rate"
CODE_INLET = "__rm_inlet_water_temp"
CODE_OUTLET = "__rm_outlet_water_temp"
CODE_VOLTAGE = "__rm_t34_ac_input_voltage"
CODE_CURRENT = "__rm_t35_ac_input_current"
CODE_COMPRESSOR_FREQ = "__rm_compressor_operation_frequency"

# --- opisy sensorow ---------------------------------------------------------
# code: (nazwa, jednostka, device_class, state_class, ikona, diagnostyczny)
T = UnitOfTemperature.CELSIUS

SENSORS: dict[str, tuple] = {
    # temperatury uzytkowe
    "__rm_ambient_temp": ("Temperatura zewnetrzna", T, "temperature", "measurement", None, False),
    "__rm_water_tank_temp": ("Temperatura zasobnika CWU", T, "temperature", "measurement", None, False),
    "__rm_t07_buffer_tank_temp": ("Temperatura bufora", T, "temperature", "measurement", None, False),
    "__rm_inlet_water_temp": ("Temperatura wody - powrot", T, "temperature", "measurement", None, False),
    "__rm_outlet_water_temp": ("Temperatura wody - zasilanie", T, "temperature", "measurement", None, False),
    "__rm_t09_room_temp": ("Temperatura pomieszczenia", T, "temperature", "measurement", None, False),
    # obieg chlodniczy
    "__rm_t03_coil_temp": ("Temperatura parownika", T, "temperature", "measurement", None, True),
    "__rm_t05_suction_temp": ("Temperatura ssania", T, "temperature", "measurement", None, True),
    "__rm_t12_exhaust_temp": ("Temperatura tloczenia", T, "temperature", "measurement", None, True),
    "__rm_t14_distributor_tube_temp": ("Temperatura skraplacza", T, "temperature", "measurement", None, True),
    "__rm_t06_antifreeze_temp": ("Temperatura gazu z wymiennika", T, "temperature", "measurement", None, True),
    "__rm_t10_evi_inlet_temp": ("EVI - wlot", T, "temperature", "measurement", None, True),
    "__rm_t11_evi_outlet_temp": ("EVI - wylot", T, "temperature", "measurement", None, True),
    "__rm_t15_low_pressure": ("Cisnienie ssania", UnitOfPressure.BAR, "pressure", "measurement", None, True),
    # sprezarka i wentylatory
    "__rm_compressor_operation_frequency": (
        "Czestotliwosc sprezarki", UnitOfFrequency.HERTZ, "frequency", "measurement", "mdi:sine-wave", False,
    ),
    "__rm_t30_compressor_frequency": (
        "Czestotliwosc sprezarki (T30)", UnitOfFrequency.HERTZ, "frequency", "measurement", "mdi:sine-wave", True,
    ),
    "__rm_t32_max_freq_from_comp_driver": (
        "Maks. czestotliwosc sprezarki", UnitOfFrequency.HERTZ, "frequency", "measurement", None, True,
    ),
    "__rm_dc_fan_motor_1_speed": (
        "Wentylator 1", REVOLUTIONS_PER_MINUTE, None, "measurement", "mdi:fan", True,
    ),
    "__rm_dc_fan_motor_2_speed": (
        "Wentylator 2", REVOLUTIONS_PER_MINUTE, None, "measurement", "mdi:fan", True,
    ),
    "__rm_t29_target_speed_of_fan_motor": (
        "Wentylator - zadane obroty", REVOLUTIONS_PER_MINUTE, None, "measurement", "mdi:fan", True,
    ),
    # elektryka
    "__rm_t34_ac_input_voltage": (
        "Napiecie AC", UnitOfElectricPotential.VOLT, "voltage", "measurement", None, False,
    ),
    "__rm_t35_ac_input_current": (
        "Prad AC", UnitOfElectricCurrent.AMPERE, "current", "measurement", None, False,
    ),
    "__rm_t36_phase_current_of_compressor": (
        "Prad sprezarki", UnitOfElectricCurrent.AMPERE, "current", "measurement", None, True,
    ),
    "__rm_t37_dc_power_bus_voltage": (
        "Napiecie szyny DC", UnitOfElectricPotential.VOLT, "voltage", "measurement", None, True,
    ),
    "__rm_t38_ipm_temp": ("Temperatura modulu IPM", T, "temperature", "measurement", None, True),
    "__rm_t33_ipm_high_fault_temp": ("Limit temperatury IPM", T, "temperature", None, None, True),
    # hydraulika
    "__rm_t39_water_flow_rate": (
        "Przeplyw wody", UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR, None, "measurement", "mdi:water-pump", False,
    ),
    "__rm_t40_heating_returning_water_temp": ("CO - powrot", T, "temperature", "measurement", None, True),
    "__rm_t41_heating_leaving_water_temp": ("CO - zasilanie", T, "temperature", "measurement", None, True),
    "__rm_t42_mix_tube_outlet_water_temp": ("Zawor mieszajacy - wylot", T, "temperature", "measurement", None, True),
    "__rm_t43_dhw_returning_water_temp": ("CWU - powrot", T, "temperature", "measurement", None, True),
    "__rm_t44_dwh_leaving_water_temp": ("CWU - zasilanie", T, "temperature", "measurement", None, True),
}

# sensory wyliczane przez integracje (nie ma ich w API)
CALCULATED = {
    "moc_cieplna": ("Moc cieplna", UnitOfPower.KILO_WATT, "power", "measurement", "mdi:radiator"),
    "moc_elektryczna": ("Pobor mocy", UnitOfPower.KILO_WATT, "power", "measurement", "mdi:flash"),
    "cop": ("COP", None, None, "measurement", "mdi:chart-line"),
}

# parametry tekstowe/stanowe -> sensory
STATE_SENSORS = {
    "__rm_mode": ("Tryb pracy", "mdi:tune"),
    "__rm_on-off": ("Stan pompy", "mdi:power"),
}

# parametry zapisywalne -> encje number
NUMBERS = {
    "__rr_r01_hot_water_setpoint": ("Nastawa CWU", T, "temperature", "mdi:water-thermometer"),
    "__rr_r02_heating_target_temp": ("Nastawa grzania", T, "temperature", "mdi:thermometer"),
}
