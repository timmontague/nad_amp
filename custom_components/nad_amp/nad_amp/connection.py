"""Module containing the connection wrapper for the NAD interface."""

import asyncio
from collections.abc import Callable
import logging

from .protocol import NAD

__all__ = ["Connection"]


class Connection:
    """Connection handler to maintain network connection for NAD Protocol."""

    def __init__(self) -> None:
        """Instantiate the Connection object."""
        self.log = logging.getLogger(__name__)
        self.host = ""
        self.port = 0
        self._loop: asyncio.AbstractEventLoop = None
        self._retry_interval = 1
        self._closed = False
        self.closing = False
        self._halted = False
        self.auto_reconnect = False
        self.protocol: asyncio.Protocol = None

    @classmethod
    async def create(
        cls,
        host: str = "localhost",
        port: int = 14999,
        auto_reconnect: bool = True,
        loop: asyncio.AbstractEventLoop = None,
        protocol_class: asyncio.Protocol = NAD,
        update_callback: Callable[[str], None] = None,
    ):
        """Initiate a connection to a specific device.

        Here is where we supply the host and port and callback callables we
        expect for this NAD class object.

        :param host:
            Hostname or IP address of the device
        :param port:
            TCP port number of the device
        :param auto_reconnect:
            Should the Connection try to automatically reconnect if needed?
        :param loop:
            asyncio.loop for async operation
        :param update_callback"
            This function is called whenever NAD state data changes

        :type host:
            str
        :type port:
            int
        :type auto_reconnect:
            boolean
        :type loop:
            asyncio.loop
        :type update_callback:
            callable
        """
        assert port >= 0, f"Invalid port value: {port}"
        conn = cls()

        conn.host = host
        conn.port = port
        conn._loop = loop or asyncio.get_event_loop()
        conn._retry_interval = 1
        conn._closed = False
        conn.closing = False
        conn._halted = False
        conn.auto_reconnect = auto_reconnect

        async def connection_lost():
            """Protocol class callback."""
            if conn.auto_reconnect and not conn.closing:
                await conn.reconnect()

        conn.protocol = protocol_class(
            connection_lost_callback=connection_lost,
            loop=conn._loop,
            update_callback=update_callback,
        )

        if auto_reconnect:
            await conn.reconnect()

        return conn

    @property
    def transport(self):
        """Return pointer to the transport object.

        Use this property to obtain passthrough access to the underlying
        Protocol properties and methods.
        """
        return self.protocol.transport

    def _get_retry_interval(self):
        return self._retry_interval

    def _reset_retry_interval(self):
        self._retry_interval = 1

    def _increase_retry_interval(self):
        self._retry_interval = min(30, 1.5 * self._retry_interval)

    async def reconnect(self):
        """Connect to the host and keep the connection open."""
        while True:
            try:
                if self._halted:
                    await asyncio.sleep(2)
                else:
                    self.log.debug("Connecting to NAD at %s:%d", self.host, self.port)
                    await self._loop.create_connection(
                        lambda: self.protocol, self.host, self.port
                    )
                    self._reset_retry_interval()
                    return

            except OSError:
                self._increase_retry_interval()
                interval = self._get_retry_interval()
                self.log.warning("Connecting failed, retrying in %i seconds", interval)
                if not self.auto_reconnect or self.closing:
                    raise
                await asyncio.sleep(interval)

            if not self.auto_reconnect or self.closing:
                break

    def close(self):
        """Close the NAD device connection and don't try to reconnect."""
        self.log.debug("Closing connection to NAD")
        self.closing = True
        if self.protocol.transport:
            self.protocol.transport.close()

    def halt(self):
        """Close the NAD device connection and wait for a resume() request."""
        self.log.warning("Halting connection to NAD")
        self._halted = True
        if self.protocol.transport:
            self.protocol.transport.close()

    def resume(self):
        """Resume the NAD device connection if we have been halted."""
        self.log.warning("Resuming connection to NAD")
        self._halted = False
