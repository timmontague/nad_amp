"""The NAD Amp integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, NAD_UPDATE_SIGNAL
from .nad_amp import connection

# import asyncio

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up NAD Receivers from a config entry."""

    @callback
    def async_nad_update_callback(message: str) -> None:
        """Receive notification from transport that new data exists."""
        _LOGGER.debug("Received update callback from NAD: %s", message)
        async_dispatcher_send(hass, f"{NAD_UPDATE_SIGNAL}_{entry.entry_id}")

    try:
        nad = await connection.Connection.create(
            host=entry.data[CONF_HOST],
            port=23,
            update_callback=async_nad_update_callback,
        )

        # Wait for the zones to be initialised based on the model
        await nad.protocol.wait_for_device_initialised(10)
    except OSError as err:
        raise ConfigEntryNotReady from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = nad

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def close_nad(event: Event) -> None:
        nad.close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, close_nad)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    nad = hass.data[DOMAIN][entry.entry_id]

    if nad is not None:
        _LOGGER.debug("Close avr connection")
        nad.close()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
