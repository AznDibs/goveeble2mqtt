"""Models for Govee BLE lights."""
from enum import IntEnum

class LedCommand(IntEnum):
    """A control command packet's type."""

    POWER      = 0x01
    BRIGHTNESS = 0x04
    COLOR      = 0x05

class LedMode(IntEnum):
    """The mode in which a color change happens in.

    Currently only manual is supported.
    """

    MODE_2     = 0x02
    MODE_D     = 0x0D
    MODE_1501  = 0x15 # lots more data in the packet, must make exception for this one
    MICROPHONE = 0x06
    SCENES     = 0x05


class ControlMode(IntEnum):
    """The mode in which a color change happens in."""

    COLOR       = 0x01
    TEMPERATURE = 0x02


class ModelInfo:
    """Class to store information about different models of lights."""

    MODELS = {
        "H6008": [LedMode.MODE_D, 100],
        "H6046": [LedMode.MODE_1501, 100],
        "H6072": [LedMode.MODE_1501, 100],
        "H6076": [LedMode.MODE_1501, 100],
        "default": [LedMode.MODE_D, 100],
        "MODE2": [LedMode.MODE_2, 255],
    }

    @staticmethod
    def get_led_mode(model):
        """Get the LED mode for a given model."""
        return ModelInfo.MODELS.get(model, ModelInfo.MODELS["default"])[0]

    @staticmethod
    def get_brightness_max(model):
        """Get the maximum brightness for a given model."""
        return ModelInfo.MODELS.get(model, ModelInfo.MODELS["default"])[1]
