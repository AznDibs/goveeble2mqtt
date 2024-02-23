from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.components.sensor import SensorEntity
from . import DOMAIN

class GoveeMQTTSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Sensor that is updated by a DataUpdateCoordinator."""

    def __init__(self, coordinator: DataUpdateCoordinator, identifier: str, name: str, attribute: str):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_id = f"sensor.{identifier}"
        self._name = name
        self._attribute = attribute

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return getattr(self.coordinator.data, self._attribute, None)

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self.entity_id

# Assuming you have a method to start your loop and fetch data,
# you'd create and update a DataUpdateCoordinator here,
# then initialize your sensor entities with it.
