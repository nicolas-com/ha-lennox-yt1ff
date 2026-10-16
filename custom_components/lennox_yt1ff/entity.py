"""Entité de base."""
from __future__ import annotations

from homeassistant.helpers.entity import Entity

from .controller import YT1FFController


class YT1FFEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = True

    def __init__(self, controller: YT1FFController, key: str) -> None:
        self.controller = controller
        self._attr_unique_id = f"{controller.entry.entry_id}_{key}"
        self._attr_device_info = controller.device_info

    @property
    def available(self) -> bool:
        return self.controller.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.add_listener(self.async_write_ha_state))
