import asyncio;
import json;
import threading;
import time;
import math;
import logging;

from homeassistant.core import HomeAssistant as hass;

from enum import IntEnum;
from bleak import BleakClient;

from .models import LedCommand, LedMode, ControlMode, ModelInfo;
from homeassistant.util.color import value_to_brightness, brightness_to_value

_LOGGER = logging.getLogger(__name__);

UUID_CONTROL_CHARACTERISTIC = '00010203-0405-0607-0809-0a0b0c0d2b11';

class Client:
    """Client for Govee BLE lights."""
    def __init__(self, hass, device_id, model, mqttclient, topic):
        """Initialize."""
        self._hass = hass;

        self.ControlMode        = ControlMode.COLOR;
        self.State              = 0;
        self.Brightness         = 1;
        self.Temperature        = 4000;
        self.R                 = 255;
        self.G                 = 255;
        self.B                 = 255;

        self._device_id         = device_id;
        self._model             = model;

        self.ledmode            = ModelInfo.get_led_mode(model);
        self.brightness_max     = ModelInfo.get_brightness_max(model);


        self._client            = None;
        self._reconnect         = 0;
        self._mqttclient        = mqttclient;
        self._topic             = topic;
        self._dirtyState            = False;
        self._dirtyBrightness           = False;
        self._dirtyColor            = False;
        self._lastSent          = 0;
        self._pingRoll          = 0;
        self._taskCond            = True;
        self._task            = None;

        self._task = hass.async_create_task(self._taskStarter());

    def __del__(self):
        """Destructor."""
        self.Close();

    def Close(self):
        """Close the client."""
        if self._task is None:
            return;

        _LOGGER.info("Closing device: " + self._device_id);

        try:
            self._taskCond = False;
            self._task.cancel();
        except Exception as e:
            _LOGGER.error("Error: " + str(e));

        self._task = None;

    def SetPower(self, state):
        """Set the power state."""
        if not isinstance(state, int) or state < 0 or state > 1:
            return ValueError("Invalid state");

        self.State = 1 if state == 1 else 0;
        self._dirtyState = True;

    def SetBrightness(self, brightness):
        """Set the brightness."""
        if not 0 <= float(brightness) <= 1:
            return ValueError("Invalid brightness");

        self.Brightness = brightness;
        self._dirtyBrightness = True;

    def SetColorTempMired(self, temperature):
        """Set the color temperature."""
        _colorTempK = 1000000 / temperature;

        self.ControlMode = ControlMode.TEMPERATURE;
        self.Temperature = _colorTempK;
        self._dirtyColor = True;

    def setColorRGB(self, r, g, b):
        """Set the color."""
        if not isinstance(r, int) or r < 0 or r > 255:
            return ValueError("Invalid r");
        if not isinstance(g, int) or g < 0 or g > 255:
            return ValueError("Invalid g");
        if not isinstance(b, int) or b < 0 or b > 255:
            return ValueError("Invalid b");

        self.ControlMode = ControlMode.COLOR;
        self.R = r;
        self.G = g;
        self.B = b;
        self._dirtyColor = True;

    async def _taskCoroutine(self):
        while self._taskCond:
            try:
                if not await(self._connect()):
                    asyncio.sleep(2);
                    continue;

                _changed = True;

                if self._dirtyState:
                    if not await self._send_setPower(self.State):
                        asyncio.sleep(1);
                        continue;

                    self._dirtyState = False;
                elif self._dirtyBrightness:
                    if not await self._send_setBrightness(self.Brightness):
                        asyncio.sleep(1);
                        continue;

                    self._dirtyBrightness = False;
                elif self._dirtyColor:
                    if not await self._send_setColor():
                        asyncio.sleep(1);
                        continue;

                    self._dirtyColor = False;
                else:
                    _changed = False;

                    # Keep alive
                    if time.time() - self._lastSent >= 1:
                        _asyncRes = False;
                        self._pingRoll += 1;

                        if self._pingRoll % 3 == 0 or self.State == 0:
                            _asyncRes = await self._send_setPower(self.State);
                        elif self._pingRoll % 3 == 1:
                            _asyncRes = await self._send_setBrightness(self.Brightness);
                        elif self._pingRoll % 3 == 2:
                            _asyncRes = await self._send_setColor();

                    asyncio.sleep(0.1);
                    continue;

                if _changed:
                    _LOGGER.info(self.buildMqttPayload());
                    self._mqttclient.publish(self._topic, self.buildMqttPayload());
                asyncio.sleep(0.01);

            except Exception as e:
                _LOGGER.error("Error: " + str(e));

                try:
                    if self._client is not None:
                        await self._client.disconnect();
                except Exception as e:
                    pass;

                self._client = None;

                asyncio.sleep(2);
        try:
            if self._client is not None:
                _LOGGER.info("Disconnecting from device: " + self._device_id);
                await self._client.disconnect();
        except Exception as e:
            pass;

        self._client = None;


    async def _taskStarter(self):
        while self._taskCond:
            _LOGGER.info("Starting task for device: " + self._device_id);

            asyncio.sleep(0.5);

            _coroutine = asyncio.new_event_loop();
            asyncio.set_event_loop(_coroutine);
            _coroutine.run_until_complete(self._taskCoroutine());
            _coroutine.close();



    async def _connect(self):
        if self._client is not None and self._client.is_connected:
            return True;

        _LOGGER.info("Re/connecting to device: " + self._device_id);

        try:
            if self._client is not None:
                await self._client.disconnect();

        except Exception as e:
            pass;

        self._client = None;

        try:
            self._client = BleakClient(self._device_id);

            await self._client.connect();
            self._reconnect = 0;

            _LOGGER.info("Connected to device: " + self._device_id);
            return self._client.is_connected;

        except Exception as e:
            _LOGGER.error("Error: " + str(e));
            self._reconnect += 1;

            if self._reconnect >= 3:
                self._reconnect = 0;
                return False;

            return False;


    async def _send_setPower(self, state):
        if not isinstance(state, int) or state < 0 or state > 1:
            return ValueError("Invalid state");

        try:
            return await self._send(LedCommand.POWER, [1 if state == 1 else 0]);

        except Exception as e:
            _LOGGER.error("Send SetPower Error: " + str(e));
            return False;

    async def _send_setBrightness(self, brightness):
        if not 0 <= float(brightness) <= 1:
            return ValueError("Invalid brightness");

        try:
            return await self._send(LedCommand.BRIGHTNESS, [math.floor(brightness * self.brightness_max)]);

        except Exception as e:
            _LOGGER.error("Send SetBrightness Error: " + str(e));
            return False;

    async def _send_setColor(self):
        _R = self.R;
        _G = self.G;
        _B = self.B;

        _TK = 0;
        _WR = 0;
        _WG = 0;
        _WB = 0;

        if self.ControlMode == ControlMode.TEMPERATURE:
            _R = _G = _B = 0xFF;
            _TK = int(self.Temperature);

        if not isinstance(_R, int) or _R < 0 or _R > 255:
            return ValueError("Invalid r");
        if not isinstance(_G, int) or _G < 0 or _G > 255:
            return ValueError("Invalid g");
        if not isinstance(_B, int) or _B < 0 or _B > 255:
            return ValueError("Invalid b");


        try:
            _payload = [self.ledmode]

            if self.ledmode == LedMode.MODE_1501:

                _payload.extend([
                    0x01,
                    _R,
                    _G,
                    _B,
                    (_TK >> 8) & 0xFF,
                    _TK & 0xFF,
                    _WR,
                    _WG,
                    _WB,
                    0xFF,
                    0x74,
                ])
                """
                    proven to work,  but let's see if the new method is better
                     _payload.extend([
                    0x01,
                    self.R,
                    self.G,
                    self.B,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0xFF,
                    0x74,
                ]) """

            else: #MODE_D and MODE_2
                _payload.extend([
                    _R,
                    _G,
                    _B,
                    (_TK >> 8) & 0xFF,
                    _TK & 0xFF,
                    _WR,
                    _WG,
                    _WB,
                ])

            return await self._send(LedCommand.COLOR, _payload);
        except Exception as e:
            _LOGGER.error("Send SetColor Error: " + str(e));
            return False;

    def buildMqttPayload(self):
        if self.ControlMode == ControlMode.COLOR:
            return json.dumps({
                "state": "ON" if self.State == 1 else "OFF",
                "brightness": round(self.Brightness * 255),
                "color": {
                    "r": self.R,
                    "g": self.G,
                    "b": self.B,
                }
            });
        elif self.ControlMode == ControlMode.TEMPERATURE:
            return json.dumps({
                "state": "ON" if self.State == 1 else "OFF",
                "brightness": round(self.Brightness * 255),
                "color": {
                    "r": self.R,
                    "g": self.G,
                    "b": self.B,
                },
                "color_temp": int(1000000 / self.Temperature),
            });

    async def _send(self, command, payload):
        if not isinstance(command, int):
            return ValueError("Invalid command");
        if not isinstance(payload, bytes) and not (isinstance(payload, list) and all(isinstance(x, int) for x in payload)):
            return ValueError("Invalid payload");
        if len(payload) > 17:
            return ValueError("Payload too long");

        _LOGGER.info("Sending command: " + str(command) + " with payload: " + str(payload));

        command = command & 0xFF;
        payload = bytes(payload);

        frame = bytes([0x33, command]) + bytes(payload);
        # pad frame data to 19 bytes (plus checksum)
        frame = bytes([0] * (19 - len(frame)))

        # checksum is calculated by XORing all data bytes
        checksum = 0;
        for b in frame:
            checksum ^= b;

        frame += bytes([checksum & 0xFF]);

        try:
            if self._client is not None and self._client.is_connected:
                await self._client.write_gatt_char(UUID_CONTROL_CHARACTERISTIC, frame, False);
                self._lastSent = time.time();
                return True;
        except Exception as e:
            _LOGGER.error("Error: " + str(e));

            try:
                if self._client is not None:
                    _LOGGER.info("Disconnecting from device: " + self._device_id);
                    await self._client.disconnect();
            except Exception as e:
                pass;

            self._reconnect += 1;
            self._client = None;

            return False;