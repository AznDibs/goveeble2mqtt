"""The Govee BLE2MQTT integration."""
from __future__ import annotations
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import Platform
from .const import DOMAIN
from .govee_controller import GoveeBluetoothController
from .govee2mqtt import Govee2Mqtt
import logging
_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.LIGHT,
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Govee BLE Lights component."""
    main = Govee2Mqtt()
    hass.data[DOMAIN] = main
    hass.async_create_task(main.async_start(hass))

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    main = hass.data[DOMAIN]
    await main.async_stop()

    return True
