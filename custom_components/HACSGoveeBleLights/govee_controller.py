"""Controller for Govee BLE lights."""

from __future__ import annotations
import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
import bleak_retry_connector
import asyncio
from bleak import BleakClient
import random


from .light import HACSGoveeBleLight

import logging
_LOGGER = logging.getLogger(__name__)
UUID_CONTROL_CHARACTERISTIC = '00010203-0405-0607-0809-0a0b0c0d2b11'
PYTHONASYNCIODEBUG = 1



class GoveeBluetoothController:
    """Controller for Govee BLE lights."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        """Initialize the controller."""
        self._hass = hass
        self._address = address
        # Config attributes
        self._MAX_RECONNECT_ATTEMPTS = 1
        self._KEEP_ALIVE_PACKET_INTERVAL = 0.1
        self._KEEP_ALIVE_PACKET_MAX_DURATION = 10
        self._MAX_QUEUE_SIZE = 0 # 0 means no limit
        self._PARALLEL_UPDATES = 10 # Number of active tasks to allow at once
        # Existing attributes
        self._lights = set()


        # type: asyncio.Queue[GoveeBleLight]
        # Queued tasks
        self._queued_lights = asyncio.Queue(self._MAX_QUEUE_SIZE)


        # type: set[GoveeBleLight]
        # Lights that are currently processing
        self._active_lights = set()
        # type: set[asyncio.Task]
        # Active tasks assume connection or at least attempted connection.
        # Therefore, any new requests from the light will be dropped.
        self._active_tasks = set()

        self._lock = asyncio.Lock()




    def register_light(self, light: HACSGoveeBleLight):
        """Register a light entity with the controller."""
        self._lights.add(light)  # .append(light)


    """Queue management logic"""
    async def queue_update(self, light: HACSGoveeBleLight):
        """Queue an update for a light."""
        _LOGGER.debug("Received update request for %s", light.debug_name)
        async with self._lock:
            if light not in self._queued_lights and light not in self._active_lights:
                self._hass.async_create_task(self._add_task(light))
            else:
                _LOGGER.debug("Light %s is already queued or processing", light.debug_name)


    async def _on_task_done(self, task, light: HACSGoveeBleLight):
        try:
            # Attempt to remove the task from the active tasks set
            self._active_tasks.remove(task)
            light._task = None
            # Attempt to remove the light from the active lights set
            self._active_lights.remove(light)
            _LOGGER.debug("Task finished for %s, %d tasks remaining", light.debug_name, len(self._active_tasks))
        except Exception as e:
            # Log any errors that occurred during task removal or completion
            _LOGGER.error(f"Error finishing task for {light.name}: {e}")
        finally:
            async with self._lock:
                # Safe to remove the light from tracking as the update is done
                if light in self._active_lights:
                    self._active_lights.remove(light)
                if task in self._active_tasks:
                    self._active_tasks.remove(task)
                # Check if there are queued tasks to process next
                await self._manage_task_queue()


    async def _add_task(self, light: HACSGoveeBleLight):
        """Handle tasks in the queue, respecting the parallel_updates limit."""
        try:
            async with self._lock:
                if light in self._queued_lights or light in self._active_lights:
                    _LOGGER.debug(f"Update already queued or in progress for {light.debug_name}")
                    return

                if len(self._active_tasks) >= self._PARALLEL_UPDATES:
                    _LOGGER.debug("Max active tasks reached, queueing light update")
                    await self._queued_lights.put(light)
                else:
                    _LOGGER.debug("Adding light update task to active tasks")
                    task = self._hass.async_create_task(self._async_process_light_update(light))
                    self._active_tasks.add(task)
                    self._active_lights.add(light)
                    light._task = task

                    def on_task_done(task):
                        self._hass.async_create_task(self._on_task_done(task, light))

                    task.add_done_callback(on_task_done)
        except Exception as e:
            _LOGGER.error("Error handling task queue: %s", str(e))
            if light in self._active_lights:
                self._active_lights.remove(light)
            if light._task is not None:
                light._task.cancel()
                if light.is_dirty():
                    self.queue_update(light)  # Retry



    async def _manage_task_queue(self):
       # Process the task queue if not already at capacity
        try:
            async with self._lock:
                if len(self._active_tasks) < self._PARALLEL_UPDATES and not self._queued_lights.empty():
                    queued_light = await self._queued_lights.get()
                    _LOGGER.debug("Processing queued light update for %s", queued_light.debug_name)
                    if queued_light in self._queued_or_processing_lights:
                        task = self._hass.async_create_task(self._async_process_light_update(queued_light))
                        self._active_tasks.add(task)
                        queued_light._task = task

                        def on_task_done(task):
                            self._hass.async_create_task(self._on_task_done(task, queued_light))

                        task.add_done_callback(on_task_done)
        except Exception as e:
            _LOGGER.error("Error processing task queue: %s", str(e))


    async def _async_process_light_update(self, light: HACSGoveeBleLight):
        """Manage sending packets to a light with retry and keep-alive logic."""
        attempt = 0
        _LOGGER.debug("Processing update for %s", light.debug_name)
        light.set_state_attr("send_packet_attempts", attempt)
        while attempt < self._MAX_RECONNECT_ATTEMPTS:
            try:
                if not await self._async_connect(light):
                    attempt += 1
                    light.set_state_attr("send_packet_attempts", attempt)
                    continue

                if light._dirty_state:
                    cmd, payload = light.get_power_payload()
                    result = await self._async_send_data(light, cmd, payload)
                    if result:
                        light.mark_clean("state")
                elif light._dirty_brightness:
                    cmd, payload = light.get_brightness_payload()
                    result = await self._async_send_data(light, cmd, payload)
                    if result:
                        light.mark_clean("brightness")
                elif light._dirty_rgb_color:
                    cmd, payload = light.get_rgb_color_payload()
                    result = await self._async_send_data(light, cmd, payload)
                    if result:
                        light.mark_clean("rgb_color")
                else: # No updates needed
                    attempt = 0
                    light.set_state_attr("send_packet_attempts", attempt)
                    #Keep-alive logic if needed
                    await self._handle_keep_alive(light)
                    break
            except Exception as e:
                _LOGGER.error("Failed to send packet to %s: %s", light.debug_name, e)
                attempt += 1
                light.set_state_attr("send_packet_attempts", attempt)
                await asyncio.sleep(random.uniform(0.7,1.3))


    # Improve response time by sending keep-alive packets to lights that are not being updated
    async def _handle_keep_alive(self, light: HACSGoveeBleLight):
        """Handle keep-alive logic for a light."""
        _start_time = dt_util.utcnow()
        while (dt_util.utcnow() - _start_time).total_seconds() < self._KEEP_ALIVE_PACKET_MAX_DURATION:
            if len(self._active_tasks) >= self._PARALLEL_UPDATES and not self._queued_lights.empty():
                break # If the queue is full, don't send keep-alive packets

            try:
                if not await self._async_connect(light):
                    continue
                cmd, payload = light.get_power_payload()
                await self._async_send_data(light, cmd, payload)
                await asyncio.sleep(self._KEEP_ALIVE_PACKET_INTERVAL)
            except Exception as e:
                _LOGGER.error("Failed to send keep-alive packet to %s: %s", light.debug_name, e)
                await asyncio.sleep(self._KEEP_ALIVE_PACKET_INTERVAL)


    """Bluetooth communication logic"""
    async def _async_connect(self, light: HACSGoveeBleLight):
        """Connect to a light."""
        _LOGGER.debug("Connecting to %s", light.debug_name)
        # formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        light.set_state_attr("last_connection_attempt", dt_util.utcnow())
        if light.client is not None and light.client.is_connected:
            _LOGGER.debug("Already connected to %s", light.debug_name)
            return True

        try:
            if light.client is not None:
                _LOGGER.debug("Reconnecting to %s", light.debug_name)
                await self._async_disconnect(light)
        except Exception as e:
            _LOGGER.error("Failed to disconnect from %s: %s", light.debug_name, e)

        light.client = None

        def _disconnected_callback(client):
            if light.client == client:
                light.client = None
                light.set_state_attr("connection_status", "Disconnected")
                _LOGGER.debug("Disconnected from %s", light.debug_name)

        try:
            light.set_state_attr("connection_status", "Connecting...")
            _LOGGER.debug("Establishing connection to %s", light.debug_name)
            light.client = await bleak_retry_connector.establish_connection(
                client_class = BleakClient,
                device = light.ble_device,
                name = light.unique_id,
                disconnected_callback = _disconnected_callback,
                max_attempts = 10,
            )
            _LOGGER.debug("Connected to %s", light.debug_name)
            light.reconnect = 0
            light.set_state_attr("connection_status", "Connected")

            return light.client.is_connected
        except Exception as e:
            _LOGGER.error("Failed to establish connection to %s: %s", light.debug_name, e)
            light.set_state_attr("connection_status", "Failed to connect")
            light.reconnect += 1
            if light.client is not None:
                await self._async_disconnect(light)
        return False



    async def _async_disconnect(self, light: HACSGoveeBleLight):
        """Disconnect from a light."""
        if light.client is not None and light.client.is_connected:
            _LOGGER.debug("Disconnecting from %s", light.debug_name)
            try:
                await light.client.disconnect()
            except Exception as e:
                _LOGGER.error("Failed to disconnect from %s: %s", light.debug_name, e)

        light.client = None

    async def _async_send_data(self, light: HACSGoveeBleLight, cmd, payload):
        """Send data to a light."""
        if not isinstance(cmd, int):
            raise TypeError('Invalid command')
        if not isinstance(payload, bytes) and not (isinstance(payload, list) and all(isinstance(x, int) for x in payload)):
            raise ValueError('Invalid payload')
        if len(payload) > 17:
            raise ValueError('Payload too long')

        _LOGGER.debug("Sending command %s with payload %s to %s", hex(cmd), payload, light.debug_name)

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
            if light.client is not None and light.client.is_connected:
                await light.client.write_gatt_char(UUID_CONTROL_CHARACTERISTIC, frame, False)
                light.set_state_attr("last_packet_attempt", dt_util.utcnow())
                _LOGGER.debug("Sent data to %s: %s", light.debug_name, frame.hex())
                return True
        except Exception as e:
            _LOGGER.error("Failed to send data to %s: %s", light.debug_name, e)
            try:
                if light.client is not None and light.client.is_connected:
                    await self._async_disconnect(light)
            except Exception as e:
                _LOGGER.error("Failed to disconnect from %s: %s", light.debug_name, e)

        light.client = None

        return False
