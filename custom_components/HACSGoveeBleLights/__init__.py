"""The HACS Govee BLE Lights integration."""
from __future__ import annotations
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import CONF_ADDRESS, CONF_MODEL, CONF_NAME, Platform
import yaml
from .const import DOMAIN
from .govee_controller import GoveeBluetoothController
import logging
_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.LIGHT,
]

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Govee devices configured through configuration.yaml."""

    govee_config = config.get(DOMAIN)

    if not govee_config:
        return True

    config_file = govee_config.get('config_file')

    if config_file:
        path = hass.config.path(config_file)
        try:
            with open(path) as file:
                device_config = yaml.safe_load(file)
                devices = device_config.get('devices', [])

                for _device in devices:
                    address = device_config[CONF_ADDRESS]
                    model = device_config.get(CONF_MODEL)
                    name = device_config.get(CONF_NAME)
                    area = device_config.get('area')

                    # Create a new config entry. This doesn't set up the device yet; it schedules setup via async_setup_entry
                    hass.async_create_task(
                        hass.config_entries.flow.async_init(
                            DOMAIN,
                            context={'source': SOURCE_IMPORT},
                            data={'address': address, 'model': model, 'name': name, 'area': area}
                        )
                    )
        except FileNotFoundError:
            _LOGGER.error(f"File {path} not found")
            return False



    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Govee BLE device from a config entry.

    Args:
        hass (HomeAssistant): The Home Assistant instance.
        entry (ConfigEntry): The config entry representing the Govee BLE device.

    Returns:
        bool: True if the setup was successful, False otherwise.
    """
    address = entry.unique_id
    assert address is not None

    # Initialize or retrieve the shared controller.
    if 'controller' not in hass.data.get(DOMAIN, {}):
        hass.data.setdefault(DOMAIN, {})['controller'] = GoveeBluetoothController(hass, address)

    controller = hass.data[DOMAIN]['controller']

    # controller = hass.data[DOMAIN]["controller"]

    # Use the Bluetooth API to get the BLE device object
    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), True)
    if not ble_device:
        raise ConfigEntryNotReady(f"Could not find LED BLE device with address {address}")


    # Store BLE device and other relevant info in hass.data for use in the platform setup.
    hass.data[DOMAIN][entry.entry_id] = {
        "ble_device": ble_device,
        "address": address,
        "controller": controller,  # Store the controller for use in platform setup.
    }


    await hass.config_entries.async_forward_entry_setup(entry, 'light')

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
