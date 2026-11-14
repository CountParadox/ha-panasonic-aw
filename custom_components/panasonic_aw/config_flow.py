"""Config flow for Panasonic AW cameras."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .api import (
    PanasonicAwAuthError,
    PanasonicAwClient,
    PanasonicAwConnectionError,
    PanasonicAwError,
)
from .const import (
    CONF_RTSP_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SNAPSHOT_RESOLUTION,
    DEFAULT_PORT,
    DEFAULT_RTSP_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SNAPSHOT_RESOLUTION,
    DOMAIN,
    SNAPSHOT_RESOLUTIONS,
)


class PanasonicAwConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a camera by IP address."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = PanasonicAwClient(
                async_get_clientsession(self.hass),
                user_input[CONF_HOST].strip(),
                user_input[CONF_PORT],
                user_input.get(CONF_USERNAME),
                user_input.get(CONF_PASSWORD),
            )
            try:
                info = await client.async_get_info()
            except PanasonicAwAuthError:
                errors["base"] = "invalid_auth"
            except PanasonicAwConnectionError:
                errors["base"] = "cannot_connect"
            except PanasonicAwError:
                errors["base"] = "not_aw_camera"
            else:
                unique_id = info.get("mac") or info.get("serial") or client.host
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                title = (
                    user_input.get(CONF_NAME)
                    or info.get("name")
                    or info.get("model")
                    or f"Panasonic {client.host}"
                )
                data = {
                    CONF_HOST: client.host,
                    CONF_PORT: user_input[CONF_PORT],
                }
                if user_input.get(CONF_USERNAME):
                    data[CONF_USERNAME] = user_input[CONF_USERNAME]
                    data[CONF_PASSWORD] = user_input.get(CONF_PASSWORD, "")
                return self.async_create_entry(title=title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Optional(CONF_NAME): str,
                vol.Optional(CONF_USERNAME): str,
                vol.Optional(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PanasonicAwOptionsFlow()


class PanasonicAwOptionsFlow(OptionsFlow):
    """Polling and video options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=2, max=300)),
                vol.Required(
                    CONF_SNAPSHOT_RESOLUTION,
                    default=options.get(
                        CONF_SNAPSHOT_RESOLUTION, DEFAULT_SNAPSHOT_RESOLUTION
                    ),
                ): vol.In(SNAPSHOT_RESOLUTIONS),
                vol.Required(
                    CONF_RTSP_PORT,
                    default=options.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
                ): cv.port,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
