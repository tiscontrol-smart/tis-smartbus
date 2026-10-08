"""TIS device-type code -> model, category and channel count."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources


class Category(StrEnum):
    DIMMER = "dimmer"
    RELAY = "relay"
    MOTOR = "motor"
    HVAC = "hvac"
    SENSOR = "sensor"
    PANEL = "panel"
    INPUT = "input"
    AUDIO = "audio"
    SECURITY = "security"
    GATEWAY = "gateway"
    OTHER = "other"


# "flag" column of the TIS device-type table -> category.
_FLAG_CATEGORY = {
    0: Category.SENSOR,
    1: Category.PANEL,
    2: Category.SENSOR,
    3: Category.MOTOR,
    4: Category.RELAY,
    5: Category.PANEL,
    6: Category.INPUT,
    7: Category.OTHER,  # IR emitter
    8: Category.DIMMER,
    9: Category.HVAC,
    10: Category.GATEWAY,
    11: Category.AUDIO,
    12: Category.SENSOR,
    13: Category.PANEL,
    14: Category.SECURITY,
    15: Category.OTHER,
    16: Category.PANEL,
    17: Category.PANEL,
    18: Category.DIMMER,
}


@dataclass(frozen=True, slots=True)
class DeviceType:
    code: int
    model: str
    category: Category
    channels: int = 0
    description: str = ""


def _load() -> dict[int, dict]:
    raw = json.loads(
        resources.files(__package__).joinpath("device_types.json").read_text("utf-8")
    )
    return {int(code, 16): entry for code, entry in raw.items()}


# Read once at import time, so lookups never touch the disk (they run inside event loops).
_TABLE = _load()


def _table() -> dict[int, dict]:
    return _TABLE


def lookup(code: int) -> DeviceType:
    """Describe a device-type code; unknown codes come back as category OTHER."""
    entry = _table().get(code)
    if entry is None:
        return DeviceType(code, f"TIS 0x{code:04X}", Category.OTHER)
    category = _FLAG_CATEGORY.get(entry.get("flag", -1), Category.OTHER)
    model = entry["model"]
    # The official table leaves "flag" empty for some models; their names say what they are.
    if category is Category.OTHER and model.upper().startswith(("DIM-", "DALI")):
        category = Category.DIMMER
    elif category is Category.OTHER and model.upper().startswith(("RCU", "RLY-")):
        category = Category.RELAY
    return DeviceType(
        code=code,
        model=model,
        category=category,
        channels=int(entry.get("channels", 0)),
        description=entry.get("desc", ""),
    )
