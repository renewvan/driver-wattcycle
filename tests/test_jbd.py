"""JBD protocol tests against bytes captured live from the WattCycle 314Ah
Super Mini (DH04 BMS, advertised as WTaHdAZ2601270580, MAC AA:C2:37:0A:CC:05)
on 2026-10-23. The capture is the independent source of truth: expected
values were read off the bus with gatttool before this module existed.
"""

from __future__ import annotations

import pytest

from driver_wattcycle.jbd import (
    BatteryReading,
    build_read_command,
    decode_general,
    extract_frames,
    validate_frame,
)

# One complete 0x03 response, exactly as delivered in three BLE
# notifications (20 + 20 + 14 bytes; frame total 54 = 0x2f payload + 7).
CAPTURE_CHUNKS = [
    bytes.fromhex("dd03002f0537ffd55fd07aa80008343c00000000"),
    bytes.fromhex("0000354e0304040b2e0b2e0b2f0b310080007aa8"),
    bytes.fromhex("5fd0000000001900000000f69877"),
]
CAPTURE_FRAME = b"".join(CAPTURE_CHUNKS)


def test_build_read_command_matches_bytes_that_worked_on_the_device() -> None:
    assert build_read_command(0x03) == bytes.fromhex("dda50300fffd77")


def test_extract_frames_reassembles_fragmented_notifications() -> None:
    frames, remainder = extract_frames(b"".join(CAPTURE_CHUNKS))
    assert frames == [CAPTURE_FRAME]
    assert remainder == b""


def test_extract_frames_returns_partial_tail_as_remainder() -> None:
    frames, remainder = extract_frames(CAPTURE_CHUNKS[0] + CAPTURE_CHUNKS[1])
    assert frames == []
    assert remainder == CAPTURE_CHUNKS[0] + CAPTURE_CHUNKS[1]


def test_extract_frames_skips_junk_before_start_byte() -> None:
    frames, remainder = extract_frames(b"\x00\x01" + CAPTURE_FRAME)
    assert frames == [CAPTURE_FRAME]
    assert remainder == b""


def test_extract_frames_handles_two_back_to_back_frames() -> None:
    frames, _ = extract_frames(CAPTURE_FRAME + CAPTURE_FRAME)
    assert frames == [CAPTURE_FRAME, CAPTURE_FRAME]


def test_validate_frame_returns_payload() -> None:
    payload = validate_frame(CAPTURE_FRAME)
    assert payload == CAPTURE_FRAME[4:-3]
    assert len(payload) == 0x2F


def test_validate_frame_rejects_corrupt_checksum() -> None:
    corrupt = CAPTURE_FRAME[:-3] + b"\x00\x00" + CAPTURE_FRAME[-1:]
    with pytest.raises(ValueError, match="checksum"):
        validate_frame(corrupt)


def test_validate_frame_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="truncated"):
        validate_frame(CAPTURE_FRAME + b"\x00" * 3)


def test_decode_general_matches_live_values() -> None:
    reading = decode_general(validate_frame(CAPTURE_FRAME))
    assert reading == BatteryReading(
        voltage_v=pytest.approx(13.35),
        current_a=pytest.approx(-0.43),
        soc_pct=78,
        remaining_ah=pytest.approx(245.28),
        cycles=8,
        hardware_version=pytest.approx(3.5),
        cell_count=4,
        capacity_ah=pytest.approx(314.0),
        temperatures_c=(
            pytest.approx(13.05),
            pytest.approx(13.05),
            pytest.approx(13.15),
            pytest.approx(13.35),
        ),
    )


def test_decode_general_rejects_short_payload() -> None:
    with pytest.raises(ValueError, match="too short"):
        decode_general(b"\x00" * 22)
