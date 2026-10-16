"""Commutateurs : voyant, fonction « health », X-FAN, turbo, I feel."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import YT1FFEntity
from .protocol import forced_params

SWITCHES = ("light", "health", "xfan", "turbo", "ifeel")


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(YT1FFSwitch(entry.runtime_data, key) for key in SWITCHES)


class YT1FFSwitch(YT1FFEntity, SwitchEntity):
    def __init__(self, controller, key: str) -> None:
        super().__init__(controller, key)
        self._key = key
        self._attr_translation_key = key

    @property
    def available(self) -> bool:
        # X-Fan et turbo sont imposés (éteints) dans certains modes : l'interrupteur est alors indisponible
        return super().available and self._key not in forced_params(self.controller.state.mode)

    @property
    def is_on(self) -> bool:
        return getattr(self.controller.state, self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_update(**{self._key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_update(**{self._key: False})
