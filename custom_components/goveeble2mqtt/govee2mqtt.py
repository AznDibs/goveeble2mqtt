"""Govee 2 mqtt."""
import asyncio
import json
import paho.mqtt.client as mqtt
import logging
from .govee_ble_light import GoveeBleLight;
import sys
import getopt
import time
import signal
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__);

MQTT_SERVER: str = "homeassistant";
MQTT_PORT: int = 1883;
MQTT_USER: str = None;
MQTT_PASSWORD: str = None;

CLIENTS = {};
MESSAGE_QUEUE = [];
RUNNING = True;

class Govee2Mqtt:
    """Class to convert Govee BLE messages to MQTT messages."""

    def __init__(self, hass):
        """Initialize."""
        self._hass = hass;

    async def async_start(self):
        """Start."""
        global CLIENTS;
        global MESSAGE_QUEUE;
        global RUNNING;

        _MqttClient = mqtt.Client();
        _MqttClient.on_connect = self._on_connect;
        _MqttClient.on_message = self._on_message;

        if MQTT_USER is not None:
            _MqttClient.username_pw_set(MQTT_USER, MQTT_PASSWORD);

        _MqttClient.connect(MQTT_SERVER, MQTT_PORT, 60);

        while RUNNING:
            try:
                if _MqttClient.loop() != mqtt.MQTT_ERR_SUCCESS:
                    _LOGGER.error("Disconnected from Mqtt, trying to reconnect in 5 seconds");
                    asyncio.sleep(5);

                    if _MqttClient.connect(MQTT_SERVER, MQTT_PORT, 60) == mqtt.MQTT_ERR_SUCCESS:
                        _LOGGER.info("Reconnected to Mqtt");
                        pass;
                while len(MESSAGE_QUEUE) > 0:
                    message = MESSAGE_QUEUE.pop(0);
                    topic = message["topic"];
                    prefix = DOMAIN + "/light/"
                    suffix = "/command";

                    if not topic.startswith(prefix) and not topic.endwith(suffix):
                        _LOGGER.error("Invalid topic: " + topic);
                        continue;

                    device_id = topic[len(prefix):len(topic) - len(suffix)];
                    model = "default";
                    payload = json.loads(message["payload"].decode("utf-8", "ignore"));

                    if "_" in device_id:
                        model = device_id[device_id.find("_") + 1:];
                        device_id = device_id[0:device_id.find("_")];

                    self._on_payload_received(_MqttClient, topic, device_id, model, payload);

            except Exception as e:
                _LOGGER.error("Error: " + str(e));
                pass;

        print("Exiting");

        for client in CLIENTS:
            CLIENTS[client].Close();

    def _on_connect(self, mqttclient, _, __, ___):
        topic = DOMAIN + "/light/+/command";

        _LOGGER.info("Connected to Mqtt broker")
        _LOGGER.info("Subscribing to topic: " + topic);

        mqttclient.subscribe(topic);

    def _on_message(self, mqttclient, _, message):
        global MESSAGE_QUEUE;
        MESSAGE_QUEUE.append(message);

    def _on_payload_received(self, mqttclient, topic, device_id, model, payload):
        global CLIENTS;
        global MESSAGE_QUEUE;

        _requested_device_id = device_id;
        _device = None;

        _LOGGER.info(device_id + " " + str(payload));

        try:
            device_id = ":".join(device_id[i:i+2] for i in range (0, len(device_id), 2));

            if device_id not in CLIENTS:
                _LOGGER.info("Creating new device: " + device_id);
                CLIENTS[device_id] = GoveeBleLight.Client(self._hass, device_id, model, mqttclient, topic);
                asyncio.sleep(2);

            _device = CLIENTS[device_id];

            if "state" in payload:
                _expectedstate = 1 if payload["state"] == "ON" else 0;

                if _device.State != _expectedstate:
                    _device.SetPower(_expectedstate);

            if "brightness" in payload:
                _device.SetBrightness(payload["brightness"]/255);

            if "color_temp" in payload:
                _device.SetColorTempMired(payload["color_temp"]);

            if "color" in payload:
                _r = payload["color"]["r"];
                _g = payload["color"]["g"];
                _b = payload["color"]["b"];

                if _device.R != _r or _device.G != _g or _device.B != _b:
                    _device.SetColorRGB(payload["color"]);

        except Exception as e:
            _LOGGER.error("Error: " + str(e));


    def stop(self):
        """Stop."""
        global RUNNING
        RUNNING = False