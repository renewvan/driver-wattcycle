"""WattCycleBle.read() against a fake BleakClient (no hardware).

Seam: read() must surface every failure as BleError so the driver's poll
loop retries instead of crashing. Regression for the hub incident where a
desynced notification stream raised ValueError from validate_frame and
killed the process (container restart loop).
"""

from __future__ import annotations

import asyncio

import pytest

import driver_wattcycle.ble as ble_module
from driver_wattcycle.ble import BleError, WattCycleBle

VALID_CHUNKS = [
    bytes.fromhex("dd03002f0537ffd55fd07aa80008343c00000000"),
    bytes.fromhex("0000354e0304040b2e0b2e0b2f0b310080007aa8"),
    bytes.fromhex("5fd0000000001900000000f69877"),
]

# Well-framed but checksum-corrupt: passes extract_frames, fails validate_frame.
CORRUPT_CHUNK = bytes.fromhex("dd0300020000000077")


class FakeBleakClient:
    """Answers write_gatt_char by feeding notification chunks immediately."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self._on_notify = None
        self.is_connected = True
        self.disconnected = False

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False
        self.disconnected = True

    async def start_notify(self, _uuid, callback) -> None:
        self._on_notify = callback

    async def write_gatt_char(self, _uuid, _data, response=False) -> None:
        assert self._on_notify is not None
        for chunk in self._chunks:
            self._on_notify(0, bytearray(chunk))


def make_ble(monkeypatch, chunks: list[bytes]) -> tuple[WattCycleBle, FakeBleakClient]:
    fake = FakeBleakClient(chunks)
    monkeypatch.setattr(ble_module, "BleakClient", lambda *a, **k: fake)
    ble = WattCycleBle(
        mac="AA:BB:CC:DD:EE:FF", uuid_variant="dh04", adapter="hci0", connect_timeout_s=1
    )
    return ble, fake


def test_read_decodes_valid_frame(monkeypatch) -> None:
    ble, _fake = make_ble(monkeypatch, VALID_CHUNKS)
    reading = asyncio.run(ble.read())
    assert reading.voltage_v == pytest.approx(13.35)
    assert reading.capacity_ah == pytest.approx(314.0)


def test_read_raises_ble_error_and_disconnects_on_corrupt_frame(monkeypatch) -> None:
    ble, fake = make_ble(monkeypatch, [CORRUPT_CHUNK])
    with pytest.raises(BleError, match="checksum"):
        asyncio.run(ble.read())
    assert fake.disconnected
