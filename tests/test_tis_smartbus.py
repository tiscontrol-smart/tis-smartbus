"""Library tests. Run with ``python -m unittest`` or pytest; no hardware needed."""

from __future__ import annotations

import asyncio
import unittest

from tis_smartbus import Category, CurtainAction, OpCode, PanelType, TISGateway, frame, lookup, parse
from tis_smartbus import commands as cmd
from tis_smartbus.protocol import build_telegram, crc16


class ProtocolTest(unittest.TestCase):
    def test_frame_matches_the_node_bridge_byte_for_byte(self) -> None:
        # Reference frames from a sender already verified against a live TIS bus.
        self.assertEqual(
            frame(OpCode.SINGLE_CHANNEL, 1, 5, cmd.single_channel(4, 100), src_ip="192.168.1.200").hex(),
            "c0a801c8534d415254434c4f5544aaaa0fc8c8fffe0031010504640000c2b1",
        )
        self.assertEqual(
            frame(OpCode.PANEL_CONTROL, 1, 10, cmd.panel(PanelType.COOL_SETPOINT, 24), src_ip="10.0.0.2").hex(),
            "0a000002534d415254434c4f5544aaaa0dc8c8fffee3d8010a041829b1",
        )

    def test_crc_is_xmodem(self) -> None:
        self.assertEqual(crc16(b"123456789"), 0x31C3)

    def test_parse_round_trip(self) -> None:
        dg = frame(0x0034, 255, 255, bytes([3, 100, 0, 40]), src_ip="1.2.3.4",
                   src_subnet=1, src_device=5, src_type=0x0258)
        tel = parse(dg)
        assert tel is not None
        self.assertTrue(tel.crc_ok)
        self.assertEqual((tel.source, tel.device_type, tel.opcode), ((1, 5), 0x0258, 0x0034))
        self.assertEqual(tel.content, bytes([3, 100, 0, 40]))

    def test_parse_rejects_garbage_and_flags_bad_crc(self) -> None:
        self.assertIsNone(parse(b"hello"))
        bad = bytearray(build_telegram(0x0031, 1, 5, b"\x01\x64\x00\x00"))
        bad[-1] ^= 0xFF
        tel = parse(bytes(bad))
        assert tel is not None
        self.assertFalse(tel.crc_ok)


class CommandsTest(unittest.TestCase):
    def test_encoders(self) -> None:
        self.assertEqual(cmd.single_channel(2, 150, 90), bytes([2, 100, 0, 90]))
        self.assertEqual(cmd.scene(1, 3), b"\x01\x03")
        self.assertEqual(cmd.curtain(1, CurtainAction.CLOSE), b"\x01\x02")
        self.assertEqual(cmd.universal_switch(200, True), b"\xc8\xff")

    def test_decoders(self) -> None:
        levels = cmd.decode_channel_levels(OpCode.CHANNEL_STATUS_REPLY, bytes([3, 100, 0, 40]))
        self.assertEqual([(c.channel, c.level) for c in levels], [(1, 100), (2, 0), (3, 40)])
        # live captures from a DIM-6CH-2A switched from the TIS app: [channel, 0xF8, level]
        on = cmd.decode_channel_levels(OpCode.SINGLE_CHANNEL_REPLY, bytes.fromhex("04f864"))
        off = cmd.decode_channel_levels(OpCode.SINGLE_CHANNEL_REPLY, bytes.fromhex("04f800"))
        self.assertEqual([(c.channel, c.level) for c in on + off], [(4, 100), (4, 0)])
        # anything without the done marker is not a level
        self.assertEqual(cmd.decode_channel_levels(OpCode.SINGLE_CHANNEL_REPLY, bytes([4, 55, 0, 0])), [])
        # live capture 2026-09-05: "041a00" = cool setpoint 26
        self.assertEqual(cmd.decode_panel(bytes.fromhex("041a00")), (PanelType.COOL_SETPOINT, 26))
        self.assertEqual(cmd.decode_remark(b"Living Relay\x00\x00\xff"), "Living Relay")

    def test_device_types(self) -> None:
        dim = lookup(0x0258)
        self.assertEqual((dim.model, dim.category, dim.channels), ("DIM-6CH-2A", Category.DIMMER, 6))
        self.assertEqual(lookup(0xABCD).category, Category.OTHER)


class FakeGateway(asyncio.DatagramProtocol):
    """Plays a TIS bus: one 6-channel dimmer at 1.5 named 'Living Dimmer'."""

    def __init__(self) -> None:
        self.levels = [100, 0, 40, 0, 0, 0]
        self.received: list[tuple[int, bytes]] = []

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def _reply(self, addr, opcode: int, content: bytes) -> None:
        self.transport.sendto(
            frame(opcode, 255, 255, content, src_subnet=1, src_device=5, src_type=0x0258), addr
        )

    def datagram_received(self, data: bytes, addr) -> None:
        tel = parse(data)
        assert tel is not None and tel.crc_ok
        self.received.append((tel.opcode, tel.content))
        if tel.opcode == OpCode.DEVICE_TYPE_ADDR:
            self._reply(addr, OpCode.DEVICE_TYPE_ADDR_REPLY, b"")
        elif tel.opcode == OpCode.REMARK:
            self._reply(addr, OpCode.REMARK_REPLY, b"Living Dimmer\x00\x00\x00")
        elif tel.opcode == OpCode.CHANNEL_STATUS and tel.source and (tel.tgt_subnet, tel.tgt_device) == (1, 5):
            self._reply(addr, OpCode.CHANNEL_STATUS_REPLY, bytes([6, *self.levels]))
        elif tel.opcode == OpCode.SINGLE_CHANNEL:
            ch, lvl = tel.content[0], tel.content[1]
            self.levels[ch - 1] = lvl
            self._reply(addr, OpCode.SINGLE_CHANNEL_REPLY, bytes([ch, 0xF8, lvl]))


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        loop = asyncio.get_running_loop()
        self.fake = FakeGateway()
        self.fake_transport, _ = await loop.create_datagram_endpoint(
            lambda: self.fake, local_addr=("127.0.0.1", 0)
        )
        port = self.fake_transport.get_extra_info("sockname")[1]
        self.gw = TISGateway("127.0.0.1", port, local_ip="127.0.0.1", bind_port=0, broadcast=None)
        await self.gw.connect()

    async def asyncTearDown(self) -> None:
        await self.gw.close()
        self.fake_transport.close()

    async def test_discover(self) -> None:
        found = await self.gw.discover(timeout=0.4)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].address, found[0].device_type.model, found[0].name),
                         ((1, 5), "DIM-6CH-2A", "Living Dimmer"))

    async def test_read_and_set_channels_with_push_updates(self) -> None:
        self.assertEqual(await self.gw.read_channels(1, 5), [100, 0, 40, 0, 0, 0])
        heard: list[tuple[int, int]] = []

        def on_tel(tel) -> None:
            heard.extend((c.channel, c.level) for c in cmd.decode_channel_levels(tel.opcode, tel.content))

        self.gw.add_listener(on_tel)
        self.gw.set_channel(1, 5, 2, 75)
        for _ in range(50):
            if heard:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(heard, [(2, 75)])
        self.assertEqual(self.fake.levels[1], 75)

    async def test_request_times_out_cleanly(self) -> None:
        self.assertIsNone(await self.gw.read_channels(9, 9, timeout=0.2))


class RoutingTest(unittest.TestCase):
    """Several gateways, each in front of its own part of the bus (common on larger sites)."""

    def test_sends_to_the_gateway_a_module_was_heard_through(self) -> None:
        gw = TISGateway("192.168.1.200", local_ip="192.168.1.115")
        self.assertEqual(gw._destination(4, 44), "255.255.255.255")  # not heard yet: every gateway
        reply = frame(OpCode.CHANNEL_STATUS_REPLY, 255, 255, bytes([1, 100]),
                      src_subnet=4, src_device=44, src_type=0x0258)
        gw._on_datagram(reply, "192.168.1.198")
        self.assertEqual(gw._destination(4, 44), "192.168.1.198")
        self.assertEqual(gw._destination(255, 255), "255.255.255.255")
        gw._on_datagram(frame(0x0034, 255, 255, b"\x00", src_subnet=4, src_device=45, src_type=0x0258),
                        "192.168.1.115")  # our own echo never becomes a route
        self.assertNotIn((4, 45), gw.routes)

    def test_without_broadcast_unknown_modules_go_to_host(self) -> None:
        gw = TISGateway("10.0.0.5", local_ip="10.0.0.2", broadcast=None)
        self.assertEqual(gw._destination(1, 5), "10.0.0.5")


if __name__ == "__main__":
    unittest.main()
