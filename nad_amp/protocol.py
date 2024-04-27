"""Module to maintain NAD state information and network interface."""

import asyncio
from collections.abc import Awaitable, Callable
import logging
import time
import re

__all__ = ["NAD"]


class NAD(asyncio.Protocol):
    """The NAD IP control protocol handler."""

    def __init__(
        self,
        update_callback: Callable[[str], None] = None,
        loop: asyncio.AbstractEventLoop = None,
        connection_lost_callback: Callable[[], Awaitable[None]] = None,
    ) -> None:
        """Protocol handler that handles all status and changes on NAD.

        This class is expected to be wrapped inside a Connection class object
        which will maintain the socket and handle auto-reconnects.

            :param update_callback:
                called if any state information changes in device (optional)
            :param connection_lost_callback:
                called when connection is lost to device (optional)
            :param loop:
                asyncio event loop (optional)

            :type update_callback:
                callable
            :type: connection_lost_callback:
                callable
            :type loop:
                asyncio.loop
        """
        self._loop = loop
        self.log = logging.getLogger(__name__)
        self._connection_lost_callback = connection_lost_callback
        self._update_callback = update_callback
        self.buffer = ""
        self._input_names = {}
        self._input_numbers = {}
        self._available_input_numbers = []
        self.transport: asyncio.Transport = None
        self._deviceinfo_received = asyncio.Event()
        self.volume = -1
        self.model = "(none)"
        self.sources = {}
        self.current_source = ""
        self.last_received_time = time.time()
        self.is_connected = False
        self._deviceinfo_received = asyncio.Event()

    async def refresh_all(self):
        """Query device for all attributes.

        This does not return any data, it just issues the queries.
        """
        self.log.debug("Sending out query for all attributes")
        if self.transport is None:
            self.log.warning("Lost connection to receiver while refreshing device")
        else:
            self.query("")

    async def wait_for_device_initialised(self, timeout: float):
        """Wait to receive the model and mac address for the device."""
        await asyncio.wait_for(self._deviceinfo_received.wait(), timeout)
        self.log.debug("device is initialised")

    #
    # asyncio network functions
    #

    async def heartbeat(self):
        """Send heartbeat every 5 seconds."""
        while self.is_connected:
            await asyncio.sleep(5)
            self.query("Main.Model")

    async def check_connection(self):
        """Verify that data is received from the device within 10 seconds."""
        while self.is_connected:
            await asyncio.sleep(1)  # wait for 1 second
            if time.time() - self.last_received_time > 10:  # 10 seconds passed
                if self.transport is not None:
                    self.transport.close()
                    self.log.warning("Connection closed due to inactivity")
                break

    def connection_made(self, transport: asyncio.Transport):
        """Call when asyncio.Protocol establishes the network connection."""
        self.log.debug("Connection established to NAD")
        self.transport = transport

        # self.transport.set_write_buffer_limits(0)
        limit_low, limit_high = self.transport.get_write_buffer_limits()
        self.log.debug("Write buffer limits %d to %d", limit_low, limit_high)
        self.is_connected = True
        asyncio.run_coroutine_threadsafe(self.refresh_all(), self._loop)
        asyncio.run_coroutine_threadsafe(self.heartbeat(), self._loop)
        asyncio.run_coroutine_threadsafe(self.check_connection(), self._loop)

    def data_received(self, data):
        """Call when asyncio.Protocol detects received data from network."""
        self.last_received_time = time.time()
        self.buffer += data.decode()
        # self.log.debug("Received %d bytes from NAD: %s", len(self.buffer), self.buffer)
        self._loop.create_task(self._assemble_buffer())

    def connection_lost(self, exc):
        """Call when asyncio.Protocol loses the network connection."""
        self.log.warning("Lost connection to receiver")
        self.is_connected = False
        self._deviceinfo_received.clear()

        if exc is not None:
            self.log.debug(exc)

        self.transport = None

        if self._connection_lost_callback:
            asyncio.run_coroutine_threadsafe(
                self._connection_lost_callback(), self._loop
            )

    async def _assemble_buffer(self):
        """Split up received data from device into individual commands.

        Data sent by the device is a sequence of datagrams separated by
        semicolons.  It's common to receive a burst of them all in one
        submission when there's a lot of device activity.  This function
        disassembles the chain of datagrams into individual messages which
        are then passed on for interpretation.
        """
        self.transport.pause_reading()

        for message in self.buffer.split("\n"):
            if message != "":
                self.log.debug("assembled message %s", message)
                await self._parse_message(message)

        self.buffer = ""

        self.transport.resume_reading()

    async def _parse_message(self, data: str):
        """Interpret each message datagram from device and do the needful.

        This function receives datagrams from _assemble_buffer and interprets
        what they mean.  It's responsible for maintaining the internal state
        table for each device attribute and also for firing the update_callback
        function (if one was supplied)
        """
        newdata = False
        if data.startswith("Main.VolumePercent="):
            value = int(data.split("=")[1])
            if value != self.volume:
                self.volume = value
                newdata = True
        elif data.startswith("Main.Model="):
            value = data.split("=")[1]
            self.model = value
            self._deviceinfo_received.set()
        elif data.startswith("Main.Source="):
            # self.sources is a dictionary with keys like "Source2"
            s = f"Source{data.split('=')[1]}"
            if s != self.current_source:
                self.current_source = s
                newdata = True
        elif data.startswith("Source"):
            # Get "Source2" or equivilent
            s = data.split(".")[0]
            # Get property
            p = data.split("=")[0].split(".")[1]
            # Get value
            v = data.split("=")[1].replace("\r", "")
            if s not in self.sources:
                self.sources[s] = {"Name": "", "Enabled": False}
            if p == "Name" and v != self.sources[s]["Name"]:
                self.sources[s]["Name"] = v
                newdata = True
            if p == "Enabled":
                enabled = v == "Yes"
                if enabled != self.sources[s]["Enabled"]:
                    self.sources[s]["Enabled"] = enabled
                    newdata = True
        if newdata:
            if self._update_callback:
                self._loop.call_soon(self._update_callback, data)
        # else:
        # self.log.debug("no new data encountered")

    def query(self, item: str):
        """Issue a raw query to the device for an item.

        This function is used to request that the device supply the current
        state for a data item as described in the Anthem IP protocoal API.
        Normal interaction with this module will not require you to make raw
        device queries with this function, but the method is exposed in case
        there's a need that's not otherwise met by the abstraction methods
        defined elsewhere.

        This function does not return the result, it merely issues the request.

            :param item: Any of the data items from the API
            :type item: str

        :Example:

        >>> query('Z1VOL')

        """
        item = item + "?"
        self.command(item)

    def command(self, command: str):
        """Issue a raw command to the device.

        This function is used to update a data item on the device.  It's used
        to cause activity or change the configuration of the NAD.  Normal
        interaction with this module will not require you to make raw device
        queries with this function, but the method is exposed in case there's a
        need that's not otherwise met by the abstraction methods defined
        elsewhere.

            :param command: Any command as documented in the Anthem API
            :type command: str

        :Example:

        >>> command('Z1VOL-50')
        """
        command = command + "\n"
        self.formatted_command(command)

    def formatted_command(self, command: str):
        """Issue a raw, formatted command to the device.

        This function is invoked by both query and command and is the point
        where we actually send bytes out over the network.  This function does
        the wrapping and formatting required by the Anthem API so that the
        higher-level function can just operate with regular strings without
        the burden of byte encoding and terminating device requests.

            :param command: Any command as documented in the Anthem API
            :type command: str

        :Example:

        >>> formatted_command('Z1VOL-50')
        """
        command = command.encode()

        self.log.debug("> %s", command)
        try:
            self.transport.write(command)
        except Exception as error:
            self.log.warning(
                "No transport found, unable to send command. error: %s", str(error)
            )

    def get_volume(self):
        """Volume between 0 and 1."""
        return self.volume

    def set_volume(self, value):
        """Volume between 0 and 1."""
        # self.volume = value
        self.command(f"Main.VolumePercent={value}")

    def get_sources(self):
        """Return names of enabled sources."""
        sources = []
        for _, v in self.sources.items():
            if v["Enabled"]:
                sources.append(v["Name"])
        return sources

    def get_current_source(self):
        """Return the current source."""
        if self.current_source in self.sources:
            if self.sources[self.current_source].get("Name"):
                return self.sources[self.current_source]["Name"]
        return ""

    def set_current_source(self, s):
        """Set to source."""
        for k, v in self.sources.items():
            if v["Name"] == s:
                # get number from string k, which is in the form "Source2"
                n = re.search(r"\d+", k).group(0)
                self.command(f"Main.Source={n}")
                return

    def get_model(self):
        """Model name."""
        return self.model
