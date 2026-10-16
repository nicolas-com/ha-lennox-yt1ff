"""Configuration : choix de l'émetteur infrarouge (ex. Sofabaton X1S)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import infrared
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
)

from .const import (
    CONF_EMITTER,
    CONF_IFEEL_INTERVAL,
    CONF_REPEAT,
    CONF_TEMP_SENSOR,
    DEFAULT_IFEEL_INTERVAL,
    DEFAULT_NAME,
    DOMAIN,
)


class YT1FFConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        emitters = list(infrared.async_get_emitters(self.hass))
        if not emitters:
            return self.async_abort(reason="no_emitters")
        if user_input is not None:
            self._async_abort_entries_match({CONF_NAME: user_input[CONF_NAME]})
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_EMITTER): EntitySelector(
                    EntitySelectorConfig(domain="infrared", include_entities=emitters)
                ),
                vol.Optional(CONF_TEMP_SENSOR): EntitySelector(
                    EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return YT1FFOptionsFlow()


class YT1FFOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cur = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(CONF_REPEAT, default=cur.get(CONF_REPEAT, 0)): NumberSelector(
                    NumberSelectorConfig(min=0, max=3, step=1, mode="box")
                ),
                vol.Required(CONF_IFEEL_INTERVAL, default=cur.get(CONF_IFEEL_INTERVAL, DEFAULT_IFEEL_INTERVAL)): NumberSelector(
                    NumberSelectorConfig(min=1, max=10, step=1, mode="box", unit_of_measurement="min")
                ),
                vol.Optional(
                    CONF_TEMP_SENSOR, description={"suggested_value": cur.get(CONF_TEMP_SENSOR)}
                ): EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature")),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
