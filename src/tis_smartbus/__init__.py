"""Async client for the TIS SmartBus (TIS Control) over an IP gateway."""

from .commands import AcMode, CurtainAction, FanSpeed, OpCode, PanelType
from .device_types import Category, DeviceType, lookup
from .gateway import DEFAULT_PORT, DiscoveredDevice, TISConnectionError, TISGateway
from .protocol import BROADCAST, Telegram, crc16, frame, parse

__all__ = [
    "BROADCAST",
    "DEFAULT_PORT",
    "AcMode",
    "Category",
    "CurtainAction",
    "DeviceType",
    "DiscoveredDevice",
    "FanSpeed",
    "OpCode",
    "PanelType",
    "TISConnectionError",
    "TISGateway",
    "Telegram",
    "crc16",
    "frame",
    "lookup",
    "parse",
]

__version__ = "0.1.3"
