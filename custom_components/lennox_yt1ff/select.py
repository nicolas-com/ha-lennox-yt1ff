"""Sélecteur : source de la température affichée par l'unité intérieure."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import YT1FFEntity
from .protocol import Display


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([YT1FFDisplaySelect(entry.runtime_data)])


class YT1FFDisplaySelect(YT1FFEntity, SelectEntity):
    _attr_translation_key = "display"
    _attr_options = [d.name.lower() for d in Display]

    def __init__(self, controller) -> None:
        super().__init__(controller, "display")

    @property
    def current_option(self) -> str:
        return self.controller.state.display.name.lower()

    async def async_select_option(self, option: str) -> None:
        await self.controller.async_update(display=Display[option.upper()])
