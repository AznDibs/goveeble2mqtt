from __future__ import annotations
from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ADDRESS, CONF_MODEL, CONF_NAME
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_discovered_service_info
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import area_registry


from .const import DOMAIN
from .models import ModelInfo


class GoveeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1


    def __init__(self) -> None:
        """Initialize the Govee config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, str] = {}
        self._available_models = list(ModelInfo.MODELS.keys())


    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        return await self.async_step_bluetooth_confirm()




    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm discovery."""
        assert self._discovery_info is not None
        discovery_info = self._discovery_info
        title = discovery_info.name
        if user_input is not None:
            model = user_input[CONF_MODEL]
            # Handle custom name input
            custom_name = user_input.get(CONF_NAME, title)
            area = user_input.get("area")
            return self.async_create_entry(title=title, data={
                CONF_ADDRESS: discovery_info.address,
                CONF_MODEL: model,
                CONF_NAME: custom_name,
                "area": area,
            })

        self._set_confirm_only()
        placeholders = {
            "name": title,
            "model": "Device Model",
        }
        self.context["title_placeholders"] = placeholders
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=placeholders,
            data_schema=vol.Schema({
                vol.Required(CONF_MODEL): vol.In(self._available_models),
                vol.Required(CONF_NAME, title): str, # Allow user to overwrite the name
            })
        )




    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the user step to pick discovered device."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            model = user_input[CONF_MODEL]
            custom_name = user_input.get(CONF_NAME, self._discovered_devices[address])  # Use provided name or default to discovered name
            area = user_input.get("area")
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            # Handle custom name input
            return self.async_create_entry(
                title=custom_name, data={
                    CONF_ADDRESS: address,
                    CONF_MODEL: model,
                    CONF_NAME: custom_name,  # Save the custom name
                    "area": area,
                }
            )

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass, False):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue
            self._discovered_devices[address] = (discovery_info.name)

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(self._discovered_devices),
                vol.Required(CONF_MODEL): vol.In(self._available_models),
                vol.Optional(CONF_NAME): str  # Allow user to specify a name
            }),
        )


    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Handle a flow initiated by an import from configuration.yaml."""
        address = import_data[CONF_ADDRESS]
        # Use the address as a unique ID for this device
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()


        # Check if this address is already configured
        existing_entry = await self.async_set_unique_id(address)
        if existing_entry:
            return self.async_abort(reason="already_configured")

        # Extract the model and name from the import data, applying defaults if necessary
        model = import_data.get(CONF_MODEL, "default_model")
        name = import_data.get(CONF_NAME, f"Govee Light {address}")
        area = import_data.get("area")

        # Proceed to create the entry with the imported data
        return self.async_create_entry(
            title=name,  # Use the provided name or a generated default for the entry title
            data={
                CONF_ADDRESS: address,
                CONF_MODEL: model,
                CONF_NAME: name,
                "area": area,
            }
        )
