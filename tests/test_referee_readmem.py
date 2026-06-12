"""Unit tests for EmulatorClient.read_memory (referee READMEM plumbing, Phase 1).

These run headless with a fake socket layer: we stub `_send` and `_recv_line`
on a real EmulatorClient instance so no emulator/network is needed. The fake
echoes a known MEM:<hex> payload for a READMEM:... send and we assert that
read_memory returns the exact decoded bytes; a second case asserts malformed /
ERROR: responses raise.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.emulator.emulator import EmulatorClient


def _make_client() -> EmulatorClient:
    """Build an EmulatorClient with a minimal config, bypassing the network.

    __init__ only needs an "emulator" block with host/port; the socket itself
    is never opened because we stub _send/_recv_line.
    """
    config = {"emulator": {"host": "127.0.0.1", "port": 8888}}
    return EmulatorClient(config)


class _FakeWire:
    """Captures the last sent command and replays a canned response line."""

    def __init__(self, response: str):
        self.response = response
        self.last_sent: str | None = None

    def send(self, msg: str) -> None:
        self.last_sent = msg

    def recv_line(self, timeout: float = 10.0) -> str:
        return self.response


def test_read_memory_round_trips_known_hex():
    # 4 bytes; little-endian pointer 0x02024029 for example
    payload_bytes = bytes([0x29, 0x40, 0x02, 0x02])
    hex_payload = payload_bytes.hex()  # "29400202"
    fake = _FakeWire(f"MEM:{hex_payload}")

    client = _make_client()
    client._send = fake.send
    client._recv_line = fake.recv_line

    result = client.read_memory(0x03005008, 4)

    assert result == payload_bytes
    assert fake.last_sent == "READMEM:50352136:4"  # 0x03005008 decimal


def test_read_memory_empty_zero_length():
    fake = _FakeWire("MEM:")
    client = _make_client()
    client._send = fake.send
    client._recv_line = fake.recv_line

    result = client.read_memory(0x02024029, 0)
    assert result == b""


def test_read_memory_raises_on_error_response():
    fake = _FakeWire("ERROR:Invalid READMEM format. Use READMEM:addr:len")
    client = _make_client()
    client._send = fake.send
    client._recv_line = fake.recv_line

    with pytest.raises(RuntimeError, match="read_memory failed"):
        client.read_memory(0x03005008, 4)


def test_read_memory_raises_on_unexpected_prefix():
    fake = _FakeWire("PONG")
    client = _make_client()
    client._send = fake.send
    client._recv_line = fake.recv_line

    with pytest.raises(RuntimeError, match="Unexpected read_memory response"):
        client.read_memory(0x03005008, 4)


def test_read_memory_raises_on_malformed_hex():
    fake = _FakeWire("MEM:zzzz")
    client = _make_client()
    client._send = fake.send
    client._recv_line = fake.recv_line

    with pytest.raises(RuntimeError, match="Malformed hex"):
        client.read_memory(0x03005008, 2)


def test_read_memory_raises_on_short_payload():
    # Asked for 4 bytes, bridge returned only 2
    fake = _FakeWire("MEM:2940")
    client = _make_client()
    client._send = fake.send
    client._recv_line = fake.recv_line

    with pytest.raises(RuntimeError, match="expected 4 bytes, got 2"):
        client.read_memory(0x03005008, 4)
