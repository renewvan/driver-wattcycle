"""JBD protocol pure functions, tested against bytes captured live from the
WattCycle 314Ah Super Mini (DH04 BMS) on 2026-10-23.

Frame format (read): request  DD A5 <reg> <len=0> <cksum:2> 77
                     response DD <reg> <len:2 BE> <payload> <cksum:2> 77
Checksum covers the bytes between A5/reg and the checksum itself (request:
reg+len; response: len+payload), computed as (0x10000 - sum) & 0xFFFF.
Same framing as dbus-serialbattery's lltjbd driver.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

REG_GENERAL = 0x03  # pack info: voltage, current, capacities, SOC, temps
FRAME_START = 0xDD
FRAME_END = 0x77
# dd + reg + len(2) + cksum(2) + 77
FRAME_OVERHEAD = 7
KELVIN_OFFSET = 273.15


@dataclass(frozen=True)
class BatteryReading:
    voltage_v: float
    current_a: float  # signed: positive = charging, negative = discharging
    soc_pct: int
    capacity_ah: float
    remaining_ah: float
    cycles: int
    hardware_version: float
    cell_count: int
    temperatures_c: tuple[float, ...]


def _checksum(data: bytes) -> int:
    return (0x10000 - sum(data)) % 0x10000


def build_read_command(reg: int) -> bytes:
    """Request frame for reading register `reg` (e.g. 0x03 -> DD A5 03 00 FF FD 77)."""
    payload = bytes([reg, 0x00])
    return b"\xdd\xa5" + payload + struct.pack(">H", _checksum(payload)) + b"\x77"


def extract_frames(data: bytes) -> tuple[list[bytes], bytes]:
    """Split a BLE notification stream into complete JBD frames.

    The BMS fragments responses across multiple 20-byte notifications, so
    this is incremental: returns (complete frames, unconsumed remainder
    starting at the next 0xDD). Junk before a start byte is dropped.
    """
    frames: list[bytes] = []
    buf = data
    while True:
        start = buf.find(b"\xdd")
        if start == -1:
            return frames, b""
        buf = buf[start:]
        if len(buf) < FRAME_OVERHEAD:
            return frames, buf
        length = struct.unpack_from(">H", buf, 2)[0]
        total = length + FRAME_OVERHEAD
        if len(buf) < total:
            return frames, buf
        frames.append(buf[:total])
        buf = buf[total:]


def validate_frame(frame: bytes) -> bytes:
    """Check start/status/length/checksum/end; return the payload bytes."""
    if frame[0] != FRAME_START:
        raise ValueError(f"frame does not start with 0xDD: {frame[:4].hex()}")
    reg = frame[1]
    if reg & 0x80:  # responses set bit 0x80 on error replies
        raise ValueError(f"BMS error reply, status {reg:#04x}")
    (length,) = struct.unpack_from(">H", frame, 2)
    if len(frame) != length + FRAME_OVERHEAD:
        raise ValueError(f"truncated frame: {len(frame)} bytes, expected {length + FRAME_OVERHEAD}")
    (checksum,) = struct.unpack_from(">H", frame, length + 4)
    if frame[-1] != FRAME_END:
        raise ValueError(f"frame does not end with 0x77: {frame[-4:].hex()}")
    covered = frame[2:-3]
    if checksum != _checksum(covered):
        raise ValueError(
            f"checksum mismatch: got {checksum:#06x}, expected {_checksum(covered):#06x}"
        )
    return frame[4 : length + 4]


def decode_general(payload: bytes) -> BatteryReading:
    """Decode the payload of a 0x03 (general) response.

    Offsets verified against the live capture and dbus-serialbattery's
    lltjbd.read_gen_data(): >HhHHHHhHHBBBBB then temperature words at 23+2t
    (value/10 Kelvin).
    """
    if len(payload) < 23:
        raise ValueError(f"general payload too short: {len(payload)} bytes, need >= 23")
    (
        voltage,
        current,
        remaining,
        capacity,
        cycles,
        _production,
        _balance,
        _balance2,
        _protection,
        version,
        soc,
        _fet,
        cell_count,
        temp_count,
    ) = struct.unpack_from(">HhHHHHhHHBBBBB", payload)
    temps = tuple(
        struct.unpack_from(">H", payload, 23 + 2 * t)[0] / 10 - KELVIN_OFFSET
        for t in range(temp_count)
    )
    return BatteryReading(
        voltage_v=voltage / 100,
        current_a=current / 100,
        soc_pct=soc,
        capacity_ah=capacity / 100,
        remaining_ah=remaining / 100,
        cycles=cycles,
        hardware_version=float(f"{version >> 4 & 0x0F}.{version & 0x0F}"),
        cell_count=cell_count,
        temperatures_c=temps,
    )
