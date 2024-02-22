"""Constants for the HACS Govee BLE Lights integration."""
import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.const import CONF_ADDRESS, CONF_NAME, CONF_MODEL

NAME = "HACS Govee Ble Lights"
DOMAIN = "HACSGoveeBleLights"

DEVICE_SCHEMA = vol.Schema({
    vol.Required(CONF_ADDRESS): cv.string,
    vol.Required(CONF_MODEL): cv.string,
    vol.Required(CONF_NAME): cv.string,
    vol.Optional("area"): cv.string,
})

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional('devices'): vol.All(cv.ensure_list, [DEVICE_SCHEMA]),
    }),
}, extra=vol.ALLOW_EXTRA)
