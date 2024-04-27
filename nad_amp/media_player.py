"""Support for Anthem Network Receivers and Processors."""
from __future__ import annotations

import logging

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, NAD_UPDATE_SIGNAL
from .nad_amp.connection import Connection
from .nad_amp.protocol import NAD

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entry."""
    name = config_entry.title

    nad: Connection = hass.data[DOMAIN][config_entry.entry_id]

    async_add_entities([NadAmp(nad.protocol, name, config_entry.entry_id)])


class NadAmp(MediaPlayerEntity):
    """Entity reading values from NAD protocol."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        # | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(
        self,
        nad: NAD,
        name: str,
        entry_id: str,
    ) -> None:
        """Initialize entity with transport."""
        super().__init__()
        self.nad = nad
        self._entry_id = entry_id
        self._attr_unique_id = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN)},
            manufacturer=MANUFACTURER,
            model=nad.get_model(),
            name=name,
        )
        self.volume_step = 0.05
        self.set_states()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{NAD_UPDATE_SIGNAL}_{self._entry_id}",
                self.update_states,
            )
        )

    @callback
    def update_states(self) -> None:
        """Update states."""
        self.set_states()
        self.async_write_ha_state()

    def set_states(self) -> None:
        """Set all the states from the device to the entity."""
        self._attr_state = MediaPlayerState.ON
        self._attr_volume_level = self.nad.get_volume() / 100.0
        self._attr_source_list = self.nad.get_sources()
        self._attr_source = self.nad.get_current_source()

    async def async_set_volume_level(self, volume: float) -> None:
        """Set NAD volume (0 to 1)."""
        self.nad.set_volume(volume * 100.0)

    async def async_select_source(self, source: str) -> None:
        """Change AVR to the designated source (by name)."""
        self.nad.set_current_source(source)
