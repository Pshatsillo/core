"""Config flow for LEDBLE integration."""

from __future__ import annotations

import logging
from typing import Any

from bluetooth_data_tools import human_readable_name
from led_ble import BLEAK_EXCEPTIONS, LEDBLE, CharacteristicMissingError
import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import DOMAIN, LOCAL_NAMES, UNSUPPORTED_SUB_MODEL

_LOGGER = logging.getLogger(__name__)


class LedBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Yale Access Bluetooth."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        if discovery_info.name.startswith(UNSUPPORTED_SUB_MODEL):
            # These versions speak a different protocol
            # that we do not support yet.
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": human_readable_name(
                None, discovery_info.name, discovery_info.address
            )
        }
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]
            local_name = discovery_info.name
            await self.async_set_unique_id(
                discovery_info.address, raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            led_ble = LEDBLE(discovery_info.device)
            try:
                await led_ble.update()
            except CharacteristicMissingError:
                return self.async_abort(reason="not_supported")
            except BLEAK_EXCEPTIONS:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"
            else:
                await led_ble.stop()
                return self.async_create_entry(
                    title=local_name,
                    data={
                        CONF_ADDRESS: discovery_info.address,
                    },
                )

        if discovery := self._discovery_info:
            self._discovered_devices[discovery.address] = discovery
        else:
            current_addresses = self._async_current_ids()
            for discovery in async_discovered_service_info(self.hass):
                if (
                    discovery.address in current_addresses
                    or discovery.address in self._discovered_devices
                    or not any(
                        discovery.name.startswith(local_name)
                        and not discovery.name.startswith(UNSUPPORTED_SUB_MODEL)
                        for local_name in LOCAL_NAMES
                    )
                ):
                    continue
                self._discovered_devices[discovery.address] = discovery

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(
                    {
                        service_info.address: (
                            f"{service_info.name} ({service_info.address})"
                        )
                        for service_info in self._discovered_devices.values()
                    }
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
