# tis-smartbus

Async Python client for the **TIS Control SmartBus**, talking to a TIS IP gateway (IP-COM-PORT / GTY)
over UDP on the local network. No cloud, no dependencies.

It powers the [TIS Control](https://www.tiscontrol.com) integration for Home Assistant.

## Install

```bash
pip install tis-smartbus
```

## Use

```python
import asyncio
from tis_smartbus import TISGateway, CurtainAction, PanelType

async def main():
    gw = TISGateway("192.168.1.50")          # the IP gateway on your LAN
    await gw.connect()

    for module in await gw.discover():        # read-only: every module identifies itself
        print(module.subnet, module.device, module.device_type.model, module.name)

    print(await gw.read_channels(1, 5))       # [100, 0, 40, ...] — levels in %
    gw.set_channel(1, 5, 2, 75, ramp_seconds=2)
    gw.run_scene(1, 8, area=1, number=3)
    gw.curtain(1, 6, 1, CurtainAction.OPEN)
    gw.panel(1, 10, PanelType.COOL_SETPOINT, 23)

    gw.add_listener(lambda tel: print(hex(tel.opcode), tel.source, tel.content.hex()))
    await asyncio.sleep(30)                    # watch the bus
    await gw.close()

asyncio.run(main())
```

## What it covers

| | Opcode |
|---|---|
| Discovery (device type + address, module name) | `0x0004` / `0x000E` |
| Single channel (lights, relays), with fade | `0x0031` → `0x0032` |
| Channel status | `0x0033` → `0x0034` |
| Scenes | `0x0002` |
| Curtains | `0xE3E0` |
| AC / floor heating panel control | `0xE3D8` → `0xE3D9` |
| Universal switch | `0xE01C` → `0xE01D` |

The wire format (datagram header, telegram layout, CRC-16/XMODEM) is documented in
[`protocol.py`](src/tis_smartbus/protocol.py). The device-type table maps 250+ TIS module codes to
their model names.

## Notes

- The gateway broadcasts replies to UDP port 6000, so `TISGateway` listens on 6000 by default. Only one
  program per machine should do that; pass `bind_port=0` for a send-only test.
- Commands are fire-and-forget UDP; state comes back as telegrams on the bus.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## License

Apache-2.0 © TIS Control
