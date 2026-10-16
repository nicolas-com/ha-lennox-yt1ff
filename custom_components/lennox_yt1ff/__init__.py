"""Intégration Home Assistant : thermopompe Lennox pilotée par une télécommande YT1FF."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import infrared
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS, SERVICE_GENERATE_PRONTO
from .controller import YT1FFController
from .ir import YT1FFCommand
from .protocol import (
    MAX_TEMP,
    MIN_TEMP,
    SWING_CODES,
    Display,
    Fan,
    Mode,
    YT1FFState,
    build_pairs,
    decode_frames,
    describe_pronto,
    parse_pronto,
    pairs_to_pronto,
    pronto_frequency_word,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type YT1FFConfigEntry = ConfigEntry[YT1FFController]

PRONTO_SCHEMA = vol.Schema({vol.Required("pronto"): cv.string})

GENERATE_SCHEMA = vol.Schema(
    {
        vol.Optional("power", default=True): cv.boolean,
        vol.Optional("mode", default="cool"): vol.In([m.name.lower() for m in Mode]),
        vol.Optional("temperature", default=24): vol.All(vol.Coerce(int), vol.Range(min=MIN_TEMP, max=MAX_TEMP)),
        vol.Optional("fan", default="auto"): vol.In([f.name.lower() for f in Fan]),
        vol.Optional("swing", default="off"): vol.In(list(SWING_CODES)),
        vol.Optional("xfan"): cv.boolean,
        vol.Optional("turbo", default=False): cv.boolean,
        vol.Optional("health", default=True): cv.boolean,
        vol.Optional("light", default=True): cv.boolean,
        vol.Optional("ifeel", default=False): cv.boolean,
        vol.Optional("ambient_temperature"): vol.All(vol.Coerce(float), vol.Range(min=8, max=40)),
        vol.Optional("display", default="inside"): vol.In([d.name.lower() for d in Display]),
    }
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Enregistre le service de génération de codes Pronto (sans envoi)."""

    async def _generate(call: ServiceCall) -> dict[str, Any]:
        d = call.data
        mode = Mode[d["mode"].upper()]
        state = YT1FFState(
            power=d["power"], mode=mode, temperature=d["temperature"],
            fan=Fan[d["fan"].upper()], swing=d["swing"],
            xfan=d.get("xfan", mode in (Mode.COOL, Mode.DRY)),
            health=d["health"], turbo=d["turbo"], light=d["light"], ifeel=d["ifeel"], display=Display[d["display"].upper()],
        )
        pairs = build_pairs(state, d.get("ambient_temperature"))
        frames = decode_frames(pairs)
        return {
            "pronto": pairs_to_pronto(pairs),
            "frames_hex": [" ".join(f"{b:02X}" for b in f) for f in frames],
        }

    async def _decode(call: ServiceCall) -> dict[str, Any]:
        try:
            return describe_pronto(call.data["pronto"])
        except (ValueError, IndexError) as err:
            raise ServiceValidationError(f"Code Pronto non reconnu : {err}") from err

    async def _send_pronto(call: ServiceCall) -> None:
        """Envoie tel quel un code Pronto (capture de la vraie télécommande) via l'émetteur configuré."""
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("Aucune thermopompe Lennox YT1FF configurée")
        text = call.data["pronto"]
        try:
            pairs = parse_pronto(text)
            unit_us = pronto_frequency_word(text) * 0.241246
        except (ValueError, IndexError) as err:
            raise ServiceValidationError(f"Code Pronto non reconnu : {err}") from err
        timings = [(round(m * unit_us), round(s * unit_us)) for m, s in pairs]
        command = YT1FFCommand(timings, modulation=round(1_000_000 / unit_us))
        try:
            await infrared.async_send_command(hass, entries[0].runtime_data.emitter, command)
        except HomeAssistantError:
            raise
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Échec d'envoi IR : {err}") from err

    hass.services.async_register(
        DOMAIN, "decode_pronto", _decode, schema=PRONTO_SCHEMA, supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register(DOMAIN, "send_pronto", _send_pronto, schema=PRONTO_SCHEMA)

    hass.services.async_register(
        DOMAIN, SERVICE_GENERATE_PRONTO, _generate, schema=GENERATE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: YT1FFConfigEntry) -> bool:
    controller = YT1FFController(hass, entry)
    await controller.async_load()
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: YT1FFConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: YT1FFConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
