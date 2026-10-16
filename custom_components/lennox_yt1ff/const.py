"""Constantes de l'intégration Lennox YT1FF."""
from homeassistant.const import Platform

DOMAIN = "lennox_yt1ff"
PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.SELECT]

CONF_EMITTER = "emitter"
CONF_TEMP_SENSOR = "temperature_sensor"
CONF_FRAME_MODE = "frame_mode"
CONF_REPEAT = "repeat_count"
CONF_IFEEL_INTERVAL = "ifeel_interval"
DEFAULT_IFEEL_INTERVAL = 5  # minutes (≈ intervalle de la vraie télécommande)

FRAME_DUAL = "dual"      # 2 sous-trames, comme la vraie télécommande (défaut)
FRAME_SINGLE = "single"  # sous-trame 0 seule (protocole Gree classique)

DEFAULT_NAME = "Thermopompe Lennox"
SERVICE_GENERATE_PRONTO = "generate_pronto"
