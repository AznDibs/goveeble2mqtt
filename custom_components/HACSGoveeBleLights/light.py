"""Support for Govee BLE lights."""
from __future__ import annotations
import math
import asyncio
import logging
import random


import time
import bleak_retry_connector

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from homeassistant.core import HomeAssistant
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_COLOR_TEMP,
    ColorMode,
    LightEntity)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.color import value_to_brightness
from homeassistant.util.color import brightness_to_value
from homeassistant.helpers.area_registry import async_get as async_get_area_registry
from homeassistant.helpers.device_registry import async_get as async_get_device_registry

from .const import DOMAIN
from .models import LedCommand, LedMode, ModelInfo
from .kelvin_rgb import kelvin_to_rgb

_LOGGER = logging.getLogger(__name__)
UUID_CONTROL_CHARACTERISTIC = '00010203-0405-0607-0809-0a0b0c0d2b11'

PARALLEL_UPDATES = 1

def clamp(value, min_value, max_value):
    """Clamp value to be between min_value and max_value."""
    return max(min(value, max_value), min_value)

async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
    ) -> None:
    """Set up the light from a config entry."""
    """Set up HACSGoveeBleLight from a config entry."""
    controller = hass.data[DOMAIN][entry.entry_id]['controller']
    ble_device = hass.data[DOMAIN][entry.entry_id]['ble_device']
    address = hass.data[DOMAIN][entry.entry_id]['address']

    light = hass.data[DOMAIN][entry.entry_id]

    area_registry = async_get_area_registry(hass)
    area_name = entry.data.get('area')

    if area_name:
        # Look for an existing area with the given name
        area = next((area for area in area_registry.async_list_areas() if area.name == area_name), None)

        # If the area doesn't exist, you can choose to create it (optional)
        # area = area_registry.async_create(area_name)

        if area:
            # Now you have an area, ensure the device is associated with it
            device_registry = await async_get_device_registry(hass)
            device = device_registry.async_get_device(identifiers={(DOMAIN, address)})

            if device:
                # Update the device to be associated with the found or created area
                device_registry.async_update_device(device.id, area_id=area.id)

    #bluetooth setup
    # ble_device = bluetooth.async_ble_device_from_address(hass, light.address.upper(), False)

    async_add_entities([
        HACSGoveeBleLight(
            hass,
            light,
            address,
            ble_device,
            entry,
            controller=controller,
            )])

class HACSGoveeBleLight(LightEntity):
    """Representation of a Govee BLE light."""

    MAX_RECONNECT_ATTEMPTS = 5
    INITIAL_RECONNECT_DELAY = 1 # seconds

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.RGB
    _attr_min_color_temp_kelvin = 2000
    _attr_max_color_temp_kelvin = 9000
    _attr_supported_color_modes = {
            ColorMode.COLOR_TEMP,
            ColorMode.RGB,
            # ColorMode.BRIGHTNESS,
        }

    def __init__(
            self,
            hass,
            light,
            address,
            ble_device: BLEDevice,
            config_entry: ConfigEntry,
            controller,
            ) -> None:
        """Initialize an bluetooth light."""
        _LOGGER.debug("Config entry data: %s", config_entry.data)
        self._hass = hass
        self._mac = address
        self._model = config_entry.data.get("model", "default")
        self._name = config_entry.data.get("name", self._model + "-" + self._mac.replace(":", "")[-4:])
        self._ble_device = ble_device
        self._state = None
        self._is_on = False
        self._brightness = 0
        self._rgb_color = [255,255,255]
        self._client: BleakClient | None = None

        self._controller = controller
        self._controller.register_light(self)

        self._BRIGHTNESS_SCALE = (1, ModelInfo.get_brightness_max(self.model))

        self._attr_extra_state_attributes = {}

        self._power_data = 0x0
        self._brightness_data = 0x0
        self._rgb_color_data = [0,0,0]

        self._reconnect = 0
        self._last_update = time.time()
        self._ping_roll = 0
        self._keep_alive_task = None

        # creates dirty and temp variables for state, brightness, and rgb_color
        self._temp_state = False
        self._temp_brightness = 0
        self._temp_rgb_color = [0,0,0]
        self.mark_clean("state")
        self.mark_clean("brightness")
        self.mark_clean("rgb_color")

    @property
    def name(self):
        """Return the name of the light."""
        return self._name

    @property
    def mac_address(self):
        """Return the mac address of the light."""
        return self._mac

    @property
    def debug_name(self):
        """Return the name of the light."""
        return f"{self.name} ({self.mac_address})"

    @property
    def model(self):
        """Return the model of the light."""
        return self._model

    @property
    def ble_device(self) -> BLEDevice:
        """Return the BLE device."""
        return self._ble_device

    @property
    def reconnect(self):
        """Return the reconnect attempts."""
        return self._reconnect

    @reconnect.setter
    def reconnect(self, reconnect):
        """Set the reconnect attempts."""
        self._reconnect = reconnect
        self._attr_extra_state_attributes["reconnect_attempts"] = reconnect

    @property
    def client(self) -> BleakClient | None:
        """Return the client."""
        return self._client

    @client.setter
    def client(self, client):
        """Set the client."""
        self._client = client
        self._attr_extra_state_attributes["connection_status"] = "Connected" if client is not None and client.is_connected else "Disconnected"

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return self._mac.replace(":", "")

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 0..255."""
        return value_to_brightness(self._BRIGHTNESS_SCALE, self._brightness)

    @property
    def rgb_color(self):
        """Return the color of the light."""
        return self._rgb_color or [0,0,0]

    @property
    def is_on(self) -> bool | None:
        """Return true if light is on."""
        return self._state

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self.unique_id)
            },
            name=self._name,
            manufacturer="Govee",
            model=self._model,
            serial_number=self.mac_address,
        )

    def set_state_attr(self, attr, value):
        """Set the state attribute."""
        self._attr_extra_state_attributes[attr] = value

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        _LOGGER.debug("Adding %s", self.name)
        # await self._connect()
        # _LOGGER.debug("Connected to %s", self.name)

    async def async_will_remove_from_hass(self):
        """Run when entity will be removed from hass."""
        _LOGGER.debug("Removing %s", self.name)
        if self._keep_alive_task:
            self._keep_alive_task.cancel()
            _LOGGER.debug("Cancelled keep alive task for %s", self.name)

    def _mark_dirty(self, property_name, value, dirty=True):
        """Mark the property as dirty."""
        setattr(self, f"_dirty_{property_name}", dirty)
        self.set_state_attr(f"dirty_{property_name}", dirty)
        if dirty:
            setattr(self, f"_temp_{property_name}", value)
            # Notify controller that light has pending updates

    def mark_clean(self, property_name):
        """Mark the property as clean."""
        setattr(self, f"_dirty_{property_name}", False)
        self.set_state_attr(f"dirty_{property_name}", False)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the light on."""
        _LOGGER.debug(
            "async turn on %s %s with %s",
            self.name,
            self.model,
            kwargs,
        )

        self._mark_dirty("state", True)

        if ATTR_BRIGHTNESS in kwargs:
            brightness = clamp(kwargs[ATTR_BRIGHTNESS], 0, 255)
            brightness = int(math.ceil(brightness_to_value(self._BRIGHTNESS_SCALE, brightness)))

            self._mark_dirty("brightness", brightness)
        '''
        elif ATTR_BRIGHTNESS_PCT in kwargs:
            brightness_pct = max(min(kwargs.get(ATTR_BRIGHTNESS_PCT, 100), 100), 0)
            value_in_range = math.ceil(percentage_to_ranged_value(self._BRIGHTNESS_SCALE, kwargs[ATTR_BRIGHTNESS]))
            self._temp_brightness = brightness_pct * 255 / 100
            self._dirty_brightness = True
            self._attr_extra_state_attributes["dirty_brightness"] = self._dirty_brightness
        '''
        if ATTR_RGB_COLOR in kwargs:
            red, green, blue = kwargs.get(ATTR_RGB_COLOR, [255, 255, 255])
            # santize the values
            red = clamp(red, 0, 255)
            green = clamp(green, 0, 255)
            blue = clamp(blue, 0, 255)

            self._mark_dirty("rgb_color", [red, green, blue])

        elif ATTR_COLOR_TEMP in kwargs:
            color_temp = kwargs.get(ATTR_COLOR_TEMP, self._attr_max_color_temp_kelvin)
            #sanitize the values
            kelvin = int(1000000 / color_temp)
            kelvin = clamp(kelvin, self._attr_min_color_temp_kelvin, self._attr_max_color_temp_kelvin)
            red, green, blue = kelvin_to_rgb(kelvin)

            self._mark_dirty("rgb_color", [red, green, blue])

        await self._controller.queue_update(self)

        if False and self._keep_alive_task:
            self._keep_alive_task.cancel()
            self._temp_brightness = max(min(self._temp_brightness, 255), 0)
            self._temp_rgb_color = [
                max(min(self._temp_rgb_color[0], 255), 0),
                max(min(self._temp_rgb_color[1], 255), 0),
                max(min(self._temp_rgb_color[2], 255), 0)
            ]
            self._dirty_brightness = True
            self._dirty_rgb_color = True
            _LOGGER.debug("Cancelled keep alive task for %s", self.name)


        # self._keep_alive_task = asyncio.create_task(self._send_packets_thread())
        # if self.client:
            # await self._disconnect()


    async def async_turn_off(self, **kwargs) -> None:
        """Turn the light off."""
        if False and self._keep_alive_task:
            self._keep_alive_task.cancel()
            _LOGGER.debug("Cancelled keep alive task for %s", self.name)

        self._mark_dirty("state", False)

        await self._controller.queue_update(self)

        # self._keep_alive_task = asyncio.create_task(self._send_packets_thread())

    # should return cmd and payload
    def get_power_payload(self) -> tuple[int, list[int]]:
        """Get the power state payload."""
        payload = 0x1 if self._temp_state else 0x0
        self.set_state_attr("power_data", payload)
        return LedCommand.POWER, [payload]

    async def _send_power(self, power):
        """Send the power state to the device."""
        self._attr_extra_state_attributes["power_data"] = 0x1 if power else 0x0
        try:
            return await self._send_bluetooth_data(LedCommand.POWER, [0x1 if power else 0x0])

        except Exception as exception:
            _LOGGER.error("Error sending power to %s: %s", self.name, exception)

        return False

    def get_brightness_payload(self) -> tuple[int, list[int]]:
        """Get the brightness payload."""
        payload = self._temp_brightness
        self.set_state_attr("brightness_data", payload)
        return LedCommand.BRIGHTNESS, [payload]

    async def _send_brightness(self, brightness):
        """Send the brightness to the device."""
        self.set_state_attr("brightness_data", brightness)
        _packet = [brightness]
        try:
            return await self._send_bluetooth_data(LedCommand.BRIGHTNESS, _packet)

        except Exception as exception:
            _LOGGER.error("Error sending brightness to %s: %s", self.name, exception)

        return False

    def get_rgb_color_payload(self) -> tuple[int, list[int]]:
        """Get the RGB color payload."""
        payload = [ModelInfo.get_led_mode(self.model)]
        self.set_state_attr("rgb_color_data", payload)

        red, green, blue = [*self._temp_rgb_color]
        if ModelInfo.get_led_mode(self.model) == LedMode.MODE_1501:
            payload.extend([
                0x01,
                red,
                green,
                blue,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0xFF,
                0x74,
            ])
        else:
            payload.extend(self._temp_rgb_color)
        return LedCommand.COLOR, payload

    async def _send_rgb_color(self, red, green, blue):
        """Send the RGB color to the device."""
        _packet = [ModelInfo.get_led_mode(self.model)]
        self._attr_extra_state_attributes["rgb_color_data"] = [red, green, blue]

        if ModelInfo.get_led_mode(self.model) == LedMode.MODE_1501:
                _packet.extend([
                    0x01,
                    red,
                    green,
                    blue,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0xFF,
                    0x74,
                ])
        else:
            _packet.extend([red, green, blue])

        try:
            return await self._send_bluetooth_data(LedCommand.COLOR, _packet)

        except Exception as exception:
            _LOGGER.error("Error sending color to %s: %s", self.name, exception)

        return False


    async def _send_packets_thread(self):
        """Send the packets to the device."""
        self._thread_started = time.time()
        _current_thread_started = time.time()

        _max_thread_time = 20

        def _check_thread_time():
            _current_thread_started = time.time()
            if _current_thread_started - self._thread_started > _max_thread_time:
                _LOGGER.error("Thread for %s took too long, ending thread", self.name)
                return True
            return False

        def _thread_is_alive():
            """Check if the thread is still alive. a new thread will reset the self._thread_started time."""
            return _current_thread_started - self._thread_started > 0

        while _check_thread_time and _thread_is_alive:
            """Connect to the device and send the packets."""
            try:
                """Connect to the device."""
                if not await self._connect():

                    if self._reconnect > self.MAX_RECONNECT_ATTEMPTS:
                        _LOGGER.error("Failed to connect to %s after %s attempts, ending thread", self.name, self.MAX_RECONNECT_ATTEMPTS)
                        return

                    jitter = self.INITIAL_RECONNECT_DELAY + (self._reconnect * random.uniform(0.3, 0.7))
                    await asyncio.sleep(jitter)
                    continue

                _changed = True # send mqtt packet once mqtt is implemented

                jitter = random.uniform(0.7, 1.3)
                """Send the packets."""
                if self._dirty_state:
                    if not await self._send_power(self._temp_state):
                        await asyncio.sleep(jitter)
                        continue

                    self._dirty_state = False
                    self._attr_extra_state_attributes["dirty_state"] = self._dirty_state
                    self._state = self._temp_state
                elif self._dirty_brightness:
                    if not await self._send_brightness(self._temp_brightness):
                        await asyncio.sleep(jitter)
                        continue

                    self._dirty_brightness = False
                    self._attr_extra_state_attributes["dirty_brightness"] = self._dirty_brightness
                    self._brightness = self._temp_brightness
                elif self._dirty_rgb_color:
                    if not await self._send_rgb_color(*self._temp_rgb_color):
                        await asyncio.sleep(jitter)
                        continue

                    self._dirty_rgb_color = False
                    self._attr_extra_state_attributes["dirty_rgb_color"] = self._dirty_rgb_color
                    self._rgb_color = self._temp_rgb_color
                else:
                    """Keep alive, send a packet every 1 second."""
                    _changed = False # no mqtt packet if no change


                    if (time.time() - self._last_update) >= 0.3:
                        _async_res = False
                        self._ping_roll += 1

                        if self._ping_roll % 3 == 0 or self._state == 0:
                            _async_res = await self._send_power(self._state)
                        elif self._ping_roll % 3 == 1:
                            _async_res = await self._send_brightness(self._brightness)
                        elif self._ping_roll % 3 == 2:
                            _async_res = await self._send_rgb_color(*self._rgb_color)

                        if self._ping_roll > 3:
                            self._ping_roll = 0
                            if self._client is not None:
                                await self._disconnect()

                    await asyncio.sleep(0.1)

                if _changed:
                    # send mqtt packet
                    pass

                await asyncio.sleep(0.01)


            except Exception as exception:
                _LOGGER.error("Error sending packets to %s: %s", self.name, exception)
                try:
                    if self._client is not None:
                        await self._disconnect()
                except Exception as exception:
                    _LOGGER.error("Error disconnecting from %s: %s", self.name, exception)

                self._client = None

                jitter = random.uniform(0.7, 1.3)
                await asyncio.sleep(jitter)

        if self._client is not None:
            await self._disconnect()
        _LOGGER.debug("Thread for %s ended", self.name)



    async def _disconnect(self):
        if self._client is not None and self._client.is_connected:
            _LOGGER.debug("Disconnecting from %s", self.name)
            self._attr_extra_state_attributes["connection_status"] = "Disconnecting"
            await self._client.disconnect()
        self._client = None




    async def _connect(self):

        if self._client is not None and self._client.is_connected:
            return self._client

        try:
            if self._client is not None:
                _LOGGER.debug("Disconnecting from %s", self.name)
                await self._disconnect()
        except Exception as exception:
            _LOGGER.error("Error disconnecting from %s: %s", exception)

        self._client = None

        def disconnected_callback(client):
            if self._client == client:
                self._client = None
                self._attr_extra_state_attributes["connection_status"] = "Disconnected"
                _LOGGER.debug("Disconnected from %s", self.name)

        try:
            self._attr_extra_state_attributes["connection_status"] = "Connecting..."
            client = await bleak_retry_connector.establish_connection(
                BleakClient,
                self._ble_device,
                self.unique_id,
                disconnected_callback=disconnected_callback,
                timeout=5  # Adjust the timeout as needed
            )
            self._client = client
            self._reconnect = 0
            self._attr_extra_state_attributes["connection_status"] = "Connected"

            return self._client.is_connected
        except Exception as exception:
            _LOGGER.error("Failed to connect to %s: %s", self.name, exception)
            self._attr_extra_state_attributes["connection_status"] = "Failed to connect"
            self._client = None
            self._reconnect += 1

        return None


    async def _send_bluetooth_data(self, cmd, payload):
        if not isinstance(cmd, int):
            raise TypeError('Invalid command')
        if not isinstance(payload, bytes) and not (isinstance(payload, list) and all(isinstance(x, int) for x in payload)):
            raise ValueError('Invalid payload')
        if len(payload) > 17:
            raise ValueError('Payload too long')

        _LOGGER.debug("Sending command %s with payload %s to %s", cmd, payload, self.name)
        self._attr_extra_state_attributes["last_command"] = cmd
        # if ModelInfo.get_led_mode(self.model) != LedMode.MODE_1501:
        cmd = cmd & 0xFF
        payload = bytes(payload)

        frame = bytes([0x33, cmd]) + bytes(payload)
        # pad frame data to 19 bytes (plus checksum)
        frame += bytes([0] * (19 - len(frame)))

        # The checksum is calculated by XORing all data bytes
        checksum = 0
        for b in frame:
            checksum ^= b

        frame += bytes([checksum & 0xFF])


        try:
            if self._client is not None:
                await self._client.write_gatt_char(UUID_CONTROL_CHARACTERISTIC, frame, False)
                self._last_update = time.time()
                _LOGGER.debug("Sent data to %s: %s", self.name, frame)
                return True
        except Exception as exception:
            _LOGGER.error("Error sending data to %s: %s", self.name, exception)
            try:
                if self._client is not None:
                    _LOGGER.debug("Disconnecting from %s", self.name)
                    await self._disconnect()
            except Exception as exception:
                _LOGGER.error("Error disconnecting from %s: %s", self.name, exception)

            self._reconnect += 1
            self._client = None
        return False
