"""SmartBus opcodes and content encoders/decoders.

Byte layouts are from "TIS Protocol for App Developers rev 3.3"; the ones marked CONFIRMED were
checked against a live bus. Anything without a confirmed layout is deliberately not decoded.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class OpCode(IntEnum):
    SCENE = 0x0002
    SCENE_REPLY = 0x0003
    DEVICE_TYPE_ADDR = 0x0004  # broadcast to 255.255 = discovery
    DEVICE_TYPE_ADDR_REPLY = 0x0005
    REMARK = 0x000E  # read the module's comment/name
    REMARK_REPLY = 0x000F
    SINGLE_CHANNEL = 0x0031
    SINGLE_CHANNEL_REPLY = 0x0032  # CONFIRMED [channel, level, rampHi, rampLo]
    CHANNEL_STATUS = 0x0033
    CHANNEL_STATUS_REPLY = 0x0034  # CONFIRMED [qty, ch1..chN]
    SECURITY = 0x0104
    UNIVERSAL_SWITCH = 0xE01C
    UNIVERSAL_SWITCH_REPLY = 0xE01D  # CONFIRMED [switch, 0/1]
    PANEL_CONTROL = 0xE3D8
    PANEL_CONTROL_REPLY = 0xE3D9  # CONFIRMED [type, value, pad]
    CURTAIN = 0xE3E0
    CURTAIN_REPLY = 0xE3E1


class PanelType(IntEnum):
    """Type byte of PANEL_CONTROL (AC / floor heating)."""

    AC_POWER = 0x03
    COOL_SETPOINT = 0x04
    FAN_SPEED = 0x05
    MODE = 0x06
    HEAT_SETPOINT = 0x07
    AUTO_SETPOINT = 0x08
    FLOOR_POWER = 0x14
    FLOOR_SETPOINT = 0x18


class AcMode(IntEnum):
    COOL = 0
    HEAT = 1
    FAN = 2
    AUTO = 3


class FanSpeed(IntEnum):
    AUTO = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class CurtainAction(IntEnum):
    STOP = 0
    OPEN = 1
    CLOSE = 2


ALL_CHANNELS = 255


def _byte(value: int) -> int:
    return int(value) & 0xFF


def single_channel(channel: int, level: int, ramp_seconds: int = 0) -> bytes:
    """Set one output channel to 0-100 %. ``ramp_seconds`` is the fade time."""
    level = max(0, min(100, int(level)))
    ramp = max(0, min(0xFFFF, int(ramp_seconds)))
    return bytes([_byte(channel), level, (ramp >> 8) & 0xFF, ramp & 0xFF])


def scene(area: int, number: int) -> bytes:
    return bytes([_byte(area), _byte(number)])


def curtain(channel: int, action: CurtainAction) -> bytes:
    return bytes([_byte(channel), int(action)])


def panel(kind: PanelType, value: int) -> bytes:
    return bytes([int(kind), _byte(value)])


def universal_switch(number: int, on: bool) -> bytes:
    return bytes([_byte(number), 255 if on else 0])


@dataclass(frozen=True, slots=True)
class ChannelLevel:
    channel: int
    level: int


def decode_channel_levels(opcode: int, content: bytes) -> list[ChannelLevel]:
    """Channel levels carried by a status reply or a single-channel reply."""
    if opcode == OpCode.CHANNEL_STATUS_REPLY and content:
        qty = content[0]
        return [
            ChannelLevel(ch, content[ch]) for ch in range(1, min(qty, len(content) - 1) + 1)
        ]
    if opcode == OpCode.SINGLE_CHANNEL_REPLY and len(content) >= 2:
        return [ChannelLevel(content[0], content[1])]
    return []


def decode_panel(content: bytes) -> tuple[PanelType, int] | None:
    """One AC field from a PANEL_CONTROL reply/broadcast."""
    if len(content) < 2:
        return None
    try:
        return PanelType(content[0]), content[1]
    except ValueError:
        return None


def decode_remark(content: bytes) -> str:
    """Module name from a REMARK reply: ASCII, NUL-padded."""
    text = content.split(b"\x00", 1)[0].decode("latin-1")
    return "".join(ch for ch in text if " " <= ch <= "~").strip()
