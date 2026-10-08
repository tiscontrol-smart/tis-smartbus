"""TIS SmartBus wire format: framing, CRC and parsing.

A datagram on UDP 6000 is::

    <4-byte source IPv4> "SMARTCLOUD" AA AA <telegram>

and a telegram is::

    [len, srcSubnet, srcDevice, typeHi, typeLo, opHi, opLo, tgtSubnet, tgtDevice, content..., crcHi, crcLo]

where ``len`` counts the whole telegram including the CRC, and the CRC is CRC-16/XMODEM
(poly 0x1021, init 0x0000) over the telegram minus its two CRC bytes.
"""

from __future__ import annotations

from dataclasses import dataclass

HEAD = b"SMARTCLOUD"
MARKER = b"\xaa\xaa"
BROADCAST = 255
CONSOLE_DEVICE_TYPE = 0xFFFE  # what a configuration console (us) announces itself as
MIN_TELEGRAM = 11


def crc16(data: bytes) -> int:
    """CRC-16/XMODEM, as used by the SmartBus telegram."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_telegram(
    opcode: int,
    subnet: int,
    device: int,
    content: bytes = b"",
    *,
    src_subnet: int = 200,
    src_device: int = 200,
    src_type: int = CONSOLE_DEVICE_TYPE,
) -> bytes:
    """Build one telegram addressed to ``subnet.device``."""
    length = MIN_TELEGRAM + len(content)
    if length > 255:
        raise ValueError("content too long for one telegram")
    body = bytes(
        [
            length,
            src_subnet & 0xFF,
            src_device & 0xFF,
            (src_type >> 8) & 0xFF,
            src_type & 0xFF,
            (opcode >> 8) & 0xFF,
            opcode & 0xFF,
            subnet & 0xFF,
            device & 0xFF,
        ]
    ) + bytes(content)
    crc = crc16(body)
    return body + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def ip_to_bytes(ip: str) -> bytes:
    parts = [int(p) & 0xFF for p in str(ip).split(".") if p.strip().isdigit()]
    parts = (parts + [0, 0, 0, 0])[:4]
    return bytes(parts)


def frame(
    opcode: int,
    subnet: int,
    device: int,
    content: bytes = b"",
    *,
    src_ip: str = "0.0.0.0",
    **telegram_kwargs: int,
) -> bytes:
    """Build the full UDP datagram the IP gateway expects."""
    return ip_to_bytes(src_ip) + HEAD + MARKER + build_telegram(
        opcode, subnet, device, content, **telegram_kwargs
    )


@dataclass(frozen=True, slots=True)
class Telegram:
    """One parsed telegram."""

    src_subnet: int
    src_device: int
    device_type: int
    opcode: int
    tgt_subnet: int
    tgt_device: int
    content: bytes
    crc_ok: bool

    @property
    def source(self) -> tuple[int, int]:
        return (self.src_subnet, self.src_device)


def parse(datagram: bytes) -> Telegram | None:
    """Parse a datagram (or a bare telegram). Returns None when it is not a SmartBus telegram."""
    idx = datagram.find(HEAD)
    if idx >= 0:
        pos = idx + len(HEAD)
        if datagram[pos : pos + 2] == MARKER:
            pos += 2
        tel = datagram[pos:]
    else:
        tel = datagram
    if len(tel) < MIN_TELEGRAM:
        return None
    length = tel[0]
    if length < MIN_TELEGRAM or length > len(tel):
        return None
    tel = tel[:length]
    crc_ok = crc16(tel[:-2]) == ((tel[-2] << 8) | tel[-1])
    return Telegram(
        src_subnet=tel[1],
        src_device=tel[2],
        device_type=(tel[3] << 8) | tel[4],
        opcode=(tel[5] << 8) | tel[6],
        tgt_subnet=tel[7],
        tgt_device=tel[8],
        content=bytes(tel[9:-2]),
        crc_ok=crc_ok,
    )
