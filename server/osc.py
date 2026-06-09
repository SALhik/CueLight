from __future__ import annotations

import asyncio
import struct

from pythonosc.osc_message_builder import OscMessageBuilder

from .models import OscDevice, OscFireResult, OscProbeState

FIRE_TIMEOUT = 0.4
PROBE_TIMEOUT = 0.4


def _build_osc_dgram(address: str, args: list) -> bytes:
    builder = OscMessageBuilder(address=address)
    for arg in args:
        if isinstance(arg, int):
            builder.add_arg(arg)
        elif isinstance(arg, float):
            builder.add_arg(arg)
        else:
            builder.add_arg(str(arg))
    return builder.build().dgram


def _resolve_template(template: str, cue_number: str) -> str:
    if not template:
        return ""
    return template.replace("{cue}", cue_number)


async def fire(device: OscDevice, cue_number: str) -> OscFireResult:
    address = _resolve_template(device.go_template, cue_number)
    if not address:
        return OscFireResult.SENT
    try:
        dgram = _build_osc_dgram(address, device.go_args)
        if device.protocol == "tcp":
            return await _send_tcp(device.ip, device.port, dgram, device.expect_reply, FIRE_TIMEOUT)
        else:
            return await _send_udp(device.ip, device.port, dgram, device.expect_reply, FIRE_TIMEOUT)
    except Exception:
        return OscFireResult.NO_REPLY


async def probe(device: OscDevice) -> tuple[OscProbeState, str]:
    # Tier 1: OSC ping with reply expected
    if device.ping_template and device.expect_reply:
        try:
            dgram = _build_osc_dgram(device.ping_template, [])
            if device.protocol == "tcp":
                result = await _send_tcp(device.ip, device.port, dgram, True, PROBE_TIMEOUT)
            else:
                result = await _send_udp(device.ip, device.port, dgram, True, PROBE_TIMEOUT)
            if result == OscFireResult.SENT:
                return (OscProbeState.CONFIRMED, "osc_reply")
            return (OscProbeState.FAILED, "osc_reply")
        except Exception:
            return (OscProbeState.FAILED, "osc_reply")

    # Tier 2: TCP port connect
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(device.ip, device.port),
            timeout=PROBE_TIMEOUT,
        )
        writer.close()
        await writer.wait_closed()
        return (OscProbeState.CONFIRMED, "tcp_port")
    except Exception:
        if device.protocol == "udp":
            return (OscProbeState.UNVERIFIED, "none")
        return (OscProbeState.FAILED, "tcp_port")


async def _send_udp(ip: str, port: int, dgram: bytes, expect_reply: bool, timeout: float) -> OscFireResult:
    loop = asyncio.get_running_loop()

    if not expect_reply:
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=(ip, port),
        )
        try:
            transport.sendto(dgram)
        finally:
            transport.close()
        return OscFireResult.SENT

    reply_event = asyncio.Event()

    class _ReplyProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            reply_event.set()

    transport, _ = await loop.create_datagram_endpoint(
        _ReplyProtocol,
        remote_addr=(ip, port),
    )
    try:
        transport.sendto(dgram)
        await asyncio.wait_for(reply_event.wait(), timeout=timeout)
        return OscFireResult.SENT
    except asyncio.TimeoutError:
        return OscFireResult.NO_REPLY
    finally:
        transport.close()


async def _send_tcp(ip: str, port: int, dgram: bytes, expect_reply: bool, timeout: float) -> OscFireResult:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, OSError):
        return OscFireResult.NO_REPLY

    try:
        # OSC-over-TCP: 4-byte big-endian length prefix
        writer.write(struct.pack(">I", len(dgram)) + dgram)
        await writer.drain()

        if expect_reply:
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=timeout)
                if data:
                    return OscFireResult.SENT
                return OscFireResult.NO_REPLY
            except asyncio.TimeoutError:
                return OscFireResult.NO_REPLY
        return OscFireResult.SENT
    except Exception:
        return OscFireResult.NO_REPLY
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
