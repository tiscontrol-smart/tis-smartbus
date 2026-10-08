"""Async connection to a TIS IP gateway (IP-COM-PORT / GTY) over UDP 6000."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

from . import commands as cmd
from .commands import OpCode
from .device_types import DeviceType, lookup
from .protocol import BROADCAST, CONSOLE_DEVICE_TYPE, Telegram, frame, parse

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 6000
Listener = Callable[[Telegram], None]


class TISConnectionError(Exception):
    """The gateway could not be reached or the local port could not be opened."""


@dataclass(slots=True)
class DiscoveredDevice:
    subnet: int
    device: int
    device_type: DeviceType
    name: str = ""

    @property
    def address(self) -> tuple[int, int]:
        return (self.subnet, self.device)


@dataclass
class _Waiter:
    opcode: int
    source: tuple[int, int] | None
    future: asyncio.Future[Telegram] = field(repr=False)


def local_ip_for(host: str) -> str:
    """The local address the OS would use to reach ``host`` (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect((host, DEFAULT_PORT))
            return sock.getsockname()[0]
        except OSError:
            return "0.0.0.0"


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, owner: TISGateway) -> None:
        self._owner = owner

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._owner._on_datagram(data)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error: %s", exc)


class TISGateway:
    """Send telegrams to, and receive every telegram from, one TIS bus."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        local_ip: str | None = None,
        bind_port: int | None = DEFAULT_PORT,
    ) -> None:
        self.host = host
        self.port = port
        self.local_ip = local_ip
        self._bind_port = bind_port
        self._transport: asyncio.DatagramTransport | None = None
        self._listeners: list[Listener] = []
        self._waiters: list[_Waiter] = []

    # ---- lifecycle ----
    async def connect(self) -> None:
        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()
        if self.local_ip is None:
            self.local_ip = await loop.run_in_executor(None, local_ip_for, self.host)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Gateways broadcast replies to port 6000, so listen there when we can.
            sock.bind(("0.0.0.0", self._bind_port or 0))
        except OSError as err:
            sock.close()
            raise TISConnectionError(f"cannot open UDP port {self._bind_port}: {err}") from err
        sock.setblocking(False)
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), sock=sock
        )

    async def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        for waiter in self._waiters:
            if not waiter.future.done():
                waiter.future.cancel()
        self._waiters.clear()

    @property
    def connected(self) -> bool:
        return self._transport is not None

    # ---- raw I/O ----
    def add_listener(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` for every valid telegram heard on the bus. Returns an unsubscribe."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def send(self, opcode: int, subnet: int, device: int, content: bytes = b"") -> None:
        if self._transport is None:
            raise TISConnectionError("not connected")
        datagram = frame(opcode, subnet, device, content, src_ip=self.local_ip or "0.0.0.0")
        self._transport.sendto(datagram, (self.host, self.port))

    async def request(
        self,
        opcode: int,
        subnet: int,
        device: int,
        content: bytes,
        reply_opcode: int,
        timeout: float = 2.0,
    ) -> Telegram | None:
        """Send and wait for the first matching reply from that device, or None on timeout."""
        future: asyncio.Future[Telegram] = asyncio.get_running_loop().create_future()
        source = None if subnet == BROADCAST else (subnet, device)
        waiter = _Waiter(reply_opcode, source, future)
        self._waiters.append(waiter)
        try:
            self.send(opcode, subnet, device, content)
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return None
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def _on_datagram(self, data: bytes) -> None:
        telegram = parse(data)
        if telegram is None or not telegram.crc_ok:
            return
        if telegram.device_type == CONSOLE_DEVICE_TYPE:
            return  # our own sends, or another configuration console
        for waiter in list(self._waiters):
            if waiter.future.done() or waiter.opcode != telegram.opcode:
                continue
            if waiter.source is None or waiter.source == telegram.source:
                waiter.future.set_result(telegram)
        for listener in list(self._listeners):
            try:
                listener(telegram)
            except Exception:  # a broken listener must not stop the others
                _LOGGER.exception("listener failed")

    # ---- high level ----
    async def discover(self, timeout: float = 3.0) -> list[DiscoveredDevice]:
        """Ask every module on the bus to identify itself (read-only)."""
        found: dict[tuple[int, int], DiscoveredDevice] = {}

        def collect(tel: Telegram) -> None:
            if not (1 <= tel.src_subnet <= 254 and 1 <= tel.src_device <= 254):
                return
            entry = found.get(tel.source)
            if entry is None:
                entry = found[tel.source] = DiscoveredDevice(
                    tel.src_subnet, tel.src_device, lookup(tel.device_type)
                )
            if tel.opcode == OpCode.REMARK_REPLY:
                entry.name = cmd.decode_remark(tel.content) or entry.name

        unsubscribe = self.add_listener(collect)
        try:
            self.send(OpCode.DEVICE_TYPE_ADDR, BROADCAST, BROADCAST)
            await asyncio.sleep(timeout / 2)
            self.send(OpCode.REMARK, BROADCAST, BROADCAST)
            await asyncio.sleep(timeout / 2)
        finally:
            unsubscribe()
        return sorted(found.values(), key=lambda d: d.address)

    async def read_channels(self, subnet: int, device: int, timeout: float = 2.0) -> list[int] | None:
        """Levels (0-100) of every output channel, channel 1 first."""
        reply = await self.request(
            OpCode.CHANNEL_STATUS, subnet, device, b"", OpCode.CHANNEL_STATUS_REPLY, timeout
        )
        if reply is None:
            return None
        return [c.level for c in cmd.decode_channel_levels(reply.opcode, reply.content)]

    def set_channel(self, subnet: int, device: int, channel: int, level: int, ramp_seconds: int = 0) -> None:
        self.send(OpCode.SINGLE_CHANNEL, subnet, device, cmd.single_channel(channel, level, ramp_seconds))

    def run_scene(self, subnet: int, device: int, area: int, number: int) -> None:
        self.send(OpCode.SCENE, subnet, device, cmd.scene(area, number))

    def curtain(self, subnet: int, device: int, channel: int, action: cmd.CurtainAction) -> None:
        self.send(OpCode.CURTAIN, subnet, device, cmd.curtain(channel, action))

    def panel(self, subnet: int, device: int, kind: cmd.PanelType, value: int) -> None:
        self.send(OpCode.PANEL_CONTROL, subnet, device, cmd.panel(kind, value))

    def universal_switch(self, subnet: int, device: int, number: int, on: bool) -> None:
        self.send(OpCode.UNIVERSAL_SWITCH, subnet, device, cmd.universal_switch(number, on))
