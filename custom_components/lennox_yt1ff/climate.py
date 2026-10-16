"""Entité climate : température, mode, ventilation, volet."""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_TEMP_SENSOR
from .entity import YT1FFEntity
from .protocol import MAX_TEMP, MIN_TEMP, SWING_CODES, Fan, Mode, forced_params

HVAC_TO_MODE = {
    HVACMode.AUTO: Mode.AUTO,
    HVACMode.COOL: Mode.COOL,
    HVACMode.DRY: Mode.DRY,
    HVACMode.FAN_ONLY: Mode.FAN,
    HVACMode.HEAT: Mode.HEAT,
}
MODE_TO_HVAC = {v: k for k, v in HVAC_TO_MODE.items()}
FAN_TO_HA = {Fan.AUTO: FAN_AUTO, Fan.LOW: FAN_LOW, Fan.MEDIUM: FAN_MEDIUM, Fan.HIGH: FAN_HIGH}
HA_TO_FAN = {v: k for k, v in FAN_TO_HA.items()}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([YT1FFClimate(entry.runtime_data)])


class YT1FFClimate(YT1FFEntity, ClimateEntity):
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 1
    _attr_precision = 1
    _attr_hvac_modes = [HVACMode.OFF, *HVAC_TO_MODE]
    _attr_fan_modes = list(HA_TO_FAN)
    _attr_swing_modes = list(SWING_CODES)
    _attr_translation_key = "lennox_yt1ff"

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if "temperature" not in forced_params(self.controller.state.mode):   # consigne fixe en mode auto
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    def __init__(self, controller) -> None:
        super().__init__(controller, "climate")
        self._sensor = controller.entry.options.get(CONF_TEMP_SENSOR, controller.entry.data.get(CONF_TEMP_SENSOR))
        self._current: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._sensor:
            self._read_sensor(self.hass.states.get(self._sensor))
            self.async_on_remove(
                async_track_state_change_event(self.hass, [self._sensor], self._sensor_changed)
            )

    def _read_sensor(self, st) -> None:
        try:
            self._current = float(st.state) if st and st.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN) else None
        except ValueError:
            self._current = None

    @callback
    def _sensor_changed(self, event: Event) -> None:
        self._read_sensor(event.data.get("new_state"))
        self.async_write_ha_state()

    # -- états --------------------------------------------------------------
    @property
    def hvac_mode(self) -> HVACMode:
        s = self.controller.state
        return MODE_TO_HVAC[s.mode] if s.power else HVACMode.OFF

    @property
    def target_temperature(self) -> float:
        return float(self.controller.state.temperature)

    @property
    def current_temperature(self) -> float | None:
        return self._current

    @property
    def fan_mode(self) -> str:
        return FAN_TO_HA[self.controller.state.fan]

    @property
    def swing_mode(self) -> str:
        return self.controller.state.swing

    # -- commandes ----------------------------------------------------------
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.controller.async_update(power=False)
            return
        # consigne, ventilation, X-Fan et volet : restaurés depuis la mémoire du mode
        await self.controller.async_update(power=True, mode=HVAC_TO_MODE[hvac_mode])

    async def async_set_temperature(self, **kwargs: Any) -> None:
        changes: dict[str, Any] = {}
        if (t := kwargs.get(ATTR_TEMPERATURE)) is not None:
            changes["temperature"] = int(round(min(max(t, MIN_TEMP), MAX_TEMP)))
        if (hm := kwargs.get("hvac_mode")) is not None:
            if hm == HVACMode.OFF:
                changes["power"] = False
            else:
                changes.update(power=True, mode=HVAC_TO_MODE[hm])
        if changes:
            await self.controller.async_update(**changes)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.controller.async_update(fan=HA_TO_FAN[fan_mode])

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.controller.async_update(swing=swing_mode)

    async def async_turn_on(self) -> None:
        await self.controller.async_update(power=True)

    async def async_turn_off(self) -> None:
        await self.controller.async_update(power=False)
