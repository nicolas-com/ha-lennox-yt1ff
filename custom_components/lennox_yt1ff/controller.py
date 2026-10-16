"""Contrôleur : mémorise l'état de la télécommande virtuelle et envoie les codes IR."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components import infrared
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import CONF_EMITTER, CONF_IFEEL_INTERVAL, CONF_REPEAT, CONF_TEMP_SENSOR, DEFAULT_IFEEL_INTERVAL, DOMAIN
from .ir import YT1FFCommand
from .protocol import (
    CARRIER_HZ,
    Display,
    Fan,
    Mode,
    IFEEL_MAX_C,
    IFEEL_MIN_C,
    YT1FFState,
    build_ifeel_pairs,
    build_pairs,
    normalize,
    pairs_to_us,
    recall,
    remember,
)

_LOGGER = logging.getLogger(__name__)


def _state_to_dict(s: YT1FFState) -> dict[str, Any]:
    return {
        "power": s.power, "mode": int(s.mode), "temperature": s.temperature,
        "fan": int(s.fan), "swing": s.swing, "xfan": s.xfan, "health": s.health,
        "light": s.light, "display": int(s.display), "turbo": s.turbo, "ifeel": s.ifeel,
    }


def _state_from_dict(d: dict[str, Any]) -> YT1FFState:
    try:
        return YT1FFState(
            power=bool(d["power"]), mode=Mode(d["mode"]), temperature=int(d["temperature"]),
            fan=Fan(d["fan"]), swing=d["swing"], xfan=bool(d["xfan"]), health=bool(d["health"]),
            light=bool(d["light"]), display=Display(d["display"]), turbo=bool(d.get("turbo", False)),
            ifeel=bool(d.get("ifeel", False)),
        )
    except (KeyError, ValueError, TypeError):
        return YT1FFState()


class YT1FFController:
    """Un contrôleur par entrée de configuration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.state = YT1FFState()
        self.memory: dict[str, dict[str, Any]] = {}   # réglages retenus par mode
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Lennox",
            model="Thermopompe murale (télécommande YT1FF)",
        )

    # -- cycle de vie -------------------------------------------------------
    async def async_load(self) -> None:
        if (data := await self._store.async_load()) is not None:
            self.state = normalize(_state_from_dict(data))
            if isinstance(data.get("memory"), dict):
                self.memory = data["memory"]

        self.entry.async_on_unload(
            async_track_state_change_event(self.hass, [self.emitter], self._emitter_changed)
        )
        # « I feel » : envoi périodique de la température ambiante du capteur Home Assistant
        self.entry.async_on_unload(
            async_track_time_interval(self.hass, self._ifeel_tick, timedelta(minutes=self.ifeel_interval))
        )

    @property
    def temp_sensor(self) -> str | None:
        return self.entry.options.get(CONF_TEMP_SENSOR, self.entry.data.get(CONF_TEMP_SENSOR))

    @property
    def ifeel_interval(self) -> int:
        return max(1, int(self.entry.options.get(CONF_IFEEL_INTERVAL, DEFAULT_IFEEL_INTERVAL)))

    def ambient_temperature(self) -> float | None:
        """Température du capteur en °C ; None si indisponible ou hors plage."""
        if not (sensor := self.temp_sensor) or (st := self.hass.states.get(sensor)) is None:
            return None
        if st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            value = float(st.state)
            unit = st.attributes.get("unit_of_measurement", UnitOfTemperature.CELSIUS)
            value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        except (ValueError, TypeError, HomeAssistantError):
            return None
        value = round(value, 1)
        return value if IFEEL_MIN_C <= value <= IFEEL_MAX_C else None

    async def _ifeel_tick(self, _now: datetime) -> None:
        """Envoie seule la trame I feel, tant que l'unité est allumée et que I feel est actif."""
        if not (self.state.ifeel and self.state.power):
            return
        if (temp := self.ambient_temperature()) is None:
            _LOGGER.warning("I feel actif mais température du capteur %s indisponible", self.temp_sensor)
            return
        await self._transmit(build_ifeel_pairs(temp))

    @property
    def emitter(self) -> str:
        return self.entry.data[CONF_EMITTER]

    @property
    def repeat(self) -> int:
        return int(self.entry.options.get(CONF_REPEAT, 0))

    @property
    def available(self) -> bool:
        st = self.hass.states.get(self.emitter)
        return st is not None and st.state != "unavailable"

    # -- écouteurs ----------------------------------------------------------
    @callback
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def _remove() -> None:
            self._listeners.remove(cb)

        return _remove

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    @callback
    def _emitter_changed(self, _event: Event) -> None:
        self._notify()

    # -- envoi --------------------------------------------------------------
    async def async_update(self, **changes: Any) -> None:
        """Modifie l'état puis émet la trame IR correspondante.

        Au changement de mode, consigne, ventilation, X-Fan et volet sont restaurés depuis la mémoire
        de ce mode ; les changements explicites passés en argument l'emportent.
        """
        new = self.state
        if (mode := changes.get("mode")) is not None and mode != new.mode:
            new = new.with_(**recall(self.memory, mode))
        await self.async_send(new.with_(**changes))

    def _dump(self) -> dict[str, Any]:
        return {**_state_to_dict(self.state), "memory": self.memory}

    async def _transmit(self, pairs: list[tuple[int, int]]) -> None:
        command = YT1FFCommand(pairs_to_us(pairs), modulation=CARRIER_HZ, repeat_count=self.repeat)
        async with self._lock:
            try:
                await infrared.async_send_command(self.hass, self.emitter, command)
            except HomeAssistantError:
                raise
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(f"Échec d'envoi IR via {self.emitter}: {err}") from err

    async def async_send(self, new: YT1FFState) -> None:
        # Les minuteries de l'unité ne sont pas utilisées : toujours désactivées.
        new = normalize(new.with_(timer_on_minutes=0, timer_off_minutes=0))
        if new.ifeel and self.ambient_temperature() is None:
            raise HomeAssistantError(
                "« I feel » nécessite un capteur de température ambiante valide (options de l'intégration)"
            )
        ambient = self.ambient_temperature() if new.ifeel else None
        await self._transmit(build_pairs(new, ambient))
        self.state = new
        remember(self.memory, new)
        self._store.async_delay_save(self._dump, 1)
        self._notify()
