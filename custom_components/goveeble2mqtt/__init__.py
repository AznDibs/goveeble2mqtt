"""The Govee BLE2MQTT integration."""
from __future__ import annotations
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import Platform
from .const import DOMAIN
from .govee_controller import GoveeBluetoothController
from .govee_ble_light import GoveeBleLight
from .govee2mqtt import Govee2Mqtt

import logging
_LOGGER = logging.getLogger(__name__)




async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Govee BLE Lights component."""
    if DOMAIN not in config:
        return True

    mqtt_ip = config[DOMAIN].get("mqtt_ip")
    mqtt_port = config[DOMAIN].get("mqtt_port")
    mqtt_user = config[DOMAIN].get("mqtt_user")
    mqtt_password = config[DOMAIN].get("mqtt_password")

    hass.data[DOMAIN] = {
        "mqtt_ip": mqtt_ip,
        "mqtt_port": mqtt_port,
        "mqtt_user": mqtt_user,
        "mqtt_password": mqtt_password,
    }

    main = Govee2Mqtt(hass)
    hass.async_create_task(main.async_start())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    main = hass.data[DOMAIN]
    await main.async_stop()

    return True
