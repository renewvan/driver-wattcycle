"""JBD protocol pure functions, tested against bytes captured live from the
WattCycle 314Ah Super Mini (DH04 BMS) on 2026-10-23.

Frame format (read): request  DD A5 <reg> <len=0> <cksum:2> 77
                     response DD <reg> <len:2 BE> <payload> <cksum:2> 77
Checksum covers the bytes between A5/reg and the checksum itself (request:
reg+len; response: len+payload), computed as (0x10000 - sum) & 0xFFFF.
Same framing as dbus-serialbattery's lltjbd driver.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

REG_GENERAL = 0x03  # pack info: voltage, current, capacities, SOC, temps
REG_CELLS = 0x04  # per-cell voltages, 2 bytes big-endian mV each
MAX_CELLS = 32  # hub battery schema cell_voltages_v maxItems
FRAME_START = 0xDD
FRAME_END = 0x77
# dd + reg + len(2) + cksum(2) + 77
FRAME_OVERHEAD = 7
KELVIN_OFFSET = 273.15

logger = logging.getLogger(__name__)

# Raw cell/probe words that mean "no reading" rather than a value (never publish 0).
INVALID_CELL_MV = (0, 0xFFFF)
INVALID_TEMP_WORD = (0, 0xFFFF)

# JBD protection bits 0-12, in hub ADR 0022 vocabulary order (battery schema
# `protections` enum, first 13 names). Unverified on hardware beyond "all clear".
PROTECTION_NAMES = (
    "cell_overvoltage",
    "cell_undervoltage",
    "pack_overvoltage",
    "pack_undervoltage",
    "charge_overtemperature",
    "charge_undertemperature",
    "discharge_overtemperature",
    "discharge_undertemperature",
    "charge_overcurrent",
    "discharge_overcurrent",
    "short_circuit",
    "internal_fault",
    "locked",
)


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
    temperatures_c: tuple[float | None, ...]  # None = unreadable probe
    # Optional BMS detail (hub ADR 0022). None = the source did not supply it.
    balance_word: int | None = None  # 0x03 balance words, 32 bits: bit i = cell i+1
    protection_word: int | None = None  # 0x03 protection bits 0-15
    fet_byte: int | None = None  # 0x03 FET status: bit0 charge, bit1 discharge
    # 0x04 cell volts, one per cell (None = unreadable slot); the whole field is
    # None when the BMS did not answer 0x04 or its length disagrees with cell_count.
    cell_voltages_v: tuple[float | None, ...] | None = None


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


def validate_frame(frame: bytes, expect_reg: int | None = None) -> bytes:
    """Check start/status/length/checksum/end; return the payload bytes.

    `expect_reg` additionally pins the response's register byte to the
    request that asked for it, so a well-framed frame of another register
    (e.g. cell data spliced into the stream by a corrupted BLE delivery)
    can never decode as this response.
    """
    if frame[0] != FRAME_START:
        raise ValueError(f"frame does not start with 0xDD: {frame[:4].hex()}")
    reg = frame[1]
    if expect_reg is not None and reg != expect_reg:
        raise ValueError(f"frame register {reg:#04x} does not match request {expect_reg:#04x}")
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
        balance_lo,
        balance_hi,
        protection,
        version,
        soc,
        fet,
        cell_count,
        temp_count,
    ) = struct.unpack_from(">HhHHHHHHHBBBBB", payload)
    temps: tuple[float | None, ...] = tuple(
        _decode_temp(struct.unpack_from(">H", payload, 23 + 2 * t)[0])
        if len(payload) >= 25 + 2 * t
        else None
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
        balance_word=balance_lo | balance_hi << 16,
        protection_word=protection,
        fet_byte=fet,
    )


def _decode_temp(word: int) -> float | None:
    """Probe word (tenths of Kelvin) to degC; None for the no-sensor words."""
    if word in INVALID_TEMP_WORD:
        return None
    return word / 10 - KELVIN_OFFSET


def decode_cells(payload: bytes, cell_count: int) -> tuple[float | None, ...] | None:
    """Decode the payload of a 0x04 (cell voltages) response to volts, 3 decimals.

    2 bytes big-endian millivolts per cell. A 0 / 0xFFFF slot is unreadable
    and becomes None (the array keeps its length). Returns None when the
    payload length disagrees with the 0x03 `cell_count`, or the count is
    zero or beyond the schema's 32: the cell count is then unknown, so no
    array is better than a padded or truncated one (hub ADR 0022).
    """
    if cell_count == 0 or cell_count > MAX_CELLS or len(payload) != 2 * cell_count:
        return None
    mv = struct.unpack(f">{cell_count}H", payload)
    return tuple(None if v in INVALID_CELL_MV else round(v / 1000, 3) for v in mv)
