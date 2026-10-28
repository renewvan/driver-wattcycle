"""Derived battery-entity fields (pure math, no I/O).

Worked examples come from the live 2026-10-23 capture of the WattCycle 314Ah
(13.35 V, -0.43 A, 245.28 Ah remaining, 314.0 Ah nominal) and the hub
battery schema's sign convention (positive = charging).
"""

from __future__ import annotations

import pytest

from driver_wattcycle.fields import (
    charge_state,
    live_fields,
    max_temperature_c,
    power_w,
    time_to_go_s,
)
from driver_wattcycle.jbd import BatteryReading

# 13.35 V x -0.43 A, worked by hand
LIVE_POWER_W = -5.7405
# 245.28 Ah / 0.43 A = 570.4186... h in seconds
LIVE_TIME_TO_GO_S = 245.28 / 0.43 * 3600


def test_power_w_is_signed_product() -> None:
    assert power_w(13.35, -0.43) == pytest.approx(LIVE_POWER_W)
    assert power_w(13.2, 50.0) == pytest.approx(660.0)


def test_charge_state_published_only_when_discharging() -> None:
    assert charge_state(-0.43) == "discharging"
    assert charge_state(-30.0) == "discharging"
    # The enum is charger-stage oriented; a BMS does not know the charging
    # stage, so the field is omitted rather than guessed (schema guidance).
    assert charge_state(5.0) is None
    assert charge_state(0.02) is None
    assert charge_state(-0.04) is None


def test_time_to_go_s_only_when_discharging_beyond_deadband() -> None:
    assert time_to_go_s(245.28, -0.43) == pytest.approx(LIVE_TIME_TO_GO_S)
    # charging: no meaningful time-to-empty
    assert time_to_go_s(245.28, 5.0) is None
    # inside the deadband the estimate would be absurd, not informative
    assert time_to_go_s(245.28, -0.05) is None


def test_payload_rounding_strips_float_artifacts() -> None:
    # 13.05 K-conversion artifacts like 13.150000000000034 must not reach the bus
    assert max_temperature_c((13.150000000000034, 13.050000000000011)) == 13.15
    assert time_to_go_s(245.28, -0.85) == 1038833  # 245.28/0.85 h in whole seconds


def test_max_temperature_c_picks_the_hottest_probe() -> None:
    assert max_temperature_c((13.05, 13.05, 13.15, 13.35)) == pytest.approx(13.35)
    assert max_temperature_c(()) is None


def test_live_fields_relays_bms_reported_capacity_unchanged() -> None:
    # Values from the live capture: the BMS reports both words raw in frame
    # 0x03; they must reach the bus as-is (no derivation), so consumers can
    # cross-check soc_pct against remaining/capacity.
    reading = BatteryReading(
        voltage_v=13.35,
        current_a=-0.43,
        soc_pct=78,
        capacity_ah=314.0,
        remaining_ah=245.28,
        cycles=8,
        hardware_version=3.5,
        cell_count=4,
        temperatures_c=(13.05, 13.05, 13.15, 13.35),
    )
    fields = live_fields(reading)
    assert fields["capacity_ah"] == 314.0
    assert fields["remaining_ah"] == 245.28


# --- per-cell BMS data (hub ADR 0022); values from the 2026-10-28 DH04 capture ---

import logging  # noqa: E402

from driver_wattcycle.fields import (  # noqa: E402
    cell_balancing,
    cell_scalars,
    probe_temperatures_c,
    protections,
)

CAPTURE_CELLS = (3.331, 3.334, 3.333, 3.333)
CAPTURE_1028 = BatteryReading(
    voltage_v=13.33,
    current_a=-0.73,
    soc_pct=74,
    capacity_ah=314.0,
    remaining_ah=232.65,
    cycles=8,
    hardware_version=3.5,
    cell_count=4,
    temperatures_c=(13.85, 13.65, 13.75, 14.15),
    balance_word=0,
    protection_word=0,
    fet_byte=0x03,
    cell_voltages_v=CAPTURE_CELLS,
)


def test_capture_publishes_cells_scalars_probes_fets_and_empty_protections() -> None:
    fields = live_fields(CAPTURE_1028)
    assert fields["cell_voltages_v"] == [3.331, 3.334, 3.333, 3.333]
    assert fields["cell_voltage_max_v"] == 3.334
    assert fields["cell_voltage_min_v"] == 3.331
    assert fields["cell_voltage_delta_v"] == 0.003  # exact, not 0.0030000000000001137
    assert fields["temperatures_c"] == [13.85, 13.65, 13.75, 14.15]
    assert fields["allow_charge"] is True
    assert fields["allow_discharge"] is True
    assert fields["protections"] == []  # reported, none active: kept, not dropped
    # at rest: not balancing; the bit is clear, so False (cells all readable)
    assert fields["cell_balancing"] == [False, False, False, False]


def test_limits_are_never_published() -> None:
    fields = live_fields(CAPTURE_1028)
    assert not {"charge_voltage_limit_v", "charge_current_limit_a"} & fields.keys()
    assert "discharge_current_limit_a" not in fields


def test_null_cell_slot_keeps_length_and_scalars_use_non_null_cells() -> None:
    cells = (3.331, None, 3.333, 3.340)
    fields = live_fields(
        BatteryReading(**{**CAPTURE_1028.__dict__, "cell_voltages_v": cells, "balance_word": 0b101})
    )
    assert fields["cell_voltages_v"] == [3.331, None, 3.333, 3.340]
    assert fields["cell_voltage_max_v"] == 3.340
    assert fields["cell_voltage_min_v"] == 3.331
    assert fields["cell_voltage_delta_v"] == 0.009
    # the unreadable cell's balance state is unknown (None), never False
    assert fields["cell_balancing"] == [True, None, True, False]


def test_fewer_than_two_readable_cells_omit_all_three_scalars() -> None:
    assert cell_scalars((None, 3.3, None)) == {}
    assert cell_scalars((3.3,)) == {}
    assert cell_scalars(()) == {}
    fields = live_fields(
        BatteryReading(**{**CAPTURE_1028.__dict__, "cell_voltages_v": (3.3, None)})
    )
    assert fields["cell_voltages_v"] == [3.3, None]
    assert not {"cell_voltage_max_v", "cell_voltage_min_v", "cell_voltage_delta_v"} & fields.keys()


def test_no_cell_data_publishes_no_cell_properties() -> None:
    fields = live_fields(BatteryReading(**{**CAPTURE_1028.__dict__, "cell_voltages_v": None}))
    assert not [k for k in fields if k.startswith("cell_")]


def test_cell_balancing_is_index_aligned_and_absent_without_alignment() -> None:
    # bit i = cell i+1; a 24-cell pack reaches into the high word
    cells = tuple(3.3 for _ in range(20))
    got = cell_balancing(cells, 1 | 1 << 16)
    assert got is not None and len(got) == 20
    assert got[0] is True and got[16] is True and got.count(True) == 2
    assert cell_balancing(cells, None) is None  # no balance word: absent
    assert cell_balancing((), 0b1) is None
    assert cell_balancing(tuple(3.3 for _ in range(33)), 0) is None  # past the schema's 32


def test_unreadable_probe_is_null_and_headline_uses_readable_probes() -> None:
    temps = (13.85, None, 14.15, 13.0)
    assert probe_temperatures_c(temps) == [13.85, None, 14.15, 13.0]
    assert max_temperature_c(temps) == 14.15
    assert max_temperature_c((None, None)) is None
    assert probe_temperatures_c(()) is None
    assert probe_temperatures_c(tuple(20.0 for _ in range(17))) is None  # schema max 16


def test_fet_byte_bits_are_independent() -> None:
    def fets(byte: int) -> tuple:
        f = live_fields(BatteryReading(**{**CAPTURE_1028.__dict__, "fet_byte": byte}))
        return f["allow_charge"], f["allow_discharge"]

    assert fets(0b00) == (False, False)
    assert fets(0b01) == (True, False)
    assert fets(0b10) == (False, True)
    assert fets(0b11) == (True, True)
    f = live_fields(BatteryReading(**{**CAPTURE_1028.__dict__, "fet_byte": None}))
    assert "allow_charge" not in f and "allow_discharge" not in f


def test_protections_map_bits_in_vocabulary_order() -> None:
    assert protections(0) == []
    assert protections(None) is None
    assert protections(1 << 0) == ["cell_overvoltage"]
    assert protections(1 << 12) == ["locked"]
    assert protections(1 << 10 | 1 << 1 | 1 << 8) == [
        "cell_undervoltage",
        "charge_overcurrent",
        "short_circuit",
    ]


def test_protection_bit_beyond_vocabulary_is_other_and_logged(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        got = protections(1 << 13 | 1 << 2)
    assert got == ["pack_overvoltage", "other"]
    assert "0x2004" in caplog.text
    assert protections(1 << 15 | 1 << 14) == ["other"]  # one `other`, no duplicates
