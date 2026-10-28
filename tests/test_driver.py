"""read_and_publish: publish live fields, retract the ones that go absent.

The battery topics are retained; when a computed field (time_to_go_s,
charge_state) drops out of live_fields the previous value must be cleared
(empty retained payload), or it lingers on the bus forever — observed live
as charge_state "discharging" at 0.0 A (see .scratch/stale-retained-live-fields).
"""

from __future__ import annotations

from types import SimpleNamespace

from driver_wattcycle.driver import read_and_publish
from driver_wattcycle.jbd import BatteryReading

DISCHARGING = BatteryReading(
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
IDLE = BatteryReading(
    voltage_v=13.35,
    current_a=0.0,
    soc_pct=78,
    capacity_ah=314.0,
    remaining_ah=245.28,
    cycles=8,
    hardware_version=3.5,
    cell_count=4,
    temperatures_c=(13.05, 13.05, 13.15, 13.35),
)


class RecordingPublisher:
    """Duck-typed Publisher: records publishes and clears per topic."""

    def __init__(self) -> None:
        self.published: dict[str, str] = {}
        self.cleared: list[str] = []

    def publish(self, topic: str, payload: str) -> None:
        self.published[topic] = payload

    def clear(self, topic: str) -> None:
        self.cleared.append(topic)


class StubBle:
    def __init__(self, readings: list[BatteryReading]) -> None:
        self._readings = iter(readings)

    async def read(self) -> BatteryReading:
        return next(self._readings)


async def _poll(publisher: RecordingPublisher, readings: list[BatteryReading], last: set) -> None:
    await read_and_publish(
        publisher,
        StubBle(readings),
        SimpleNamespace(driver=SimpleNamespace(id="wattcycle")),
        last,
    )


def test_fields_that_go_absent_are_cleared_not_left_stale() -> None:
    publisher, last = RecordingPublisher(), set()
    import asyncio

    asyncio.run(_poll(publisher, [DISCHARGING], last))
    assert publisher.published["renewvan/battery/wattcycle/charge_state"] == '"discharging"'
    assert publisher.cleared == []

    asyncio.run(_poll(publisher, [IDLE], last))
    # idle: charge_state and time_to_go_s go absent -> retracted, not lingering
    assert sorted(publisher.cleared) == [
        "renewvan/battery/wattcycle/charge_state",
        "renewvan/battery/wattcycle/time_to_go_s",
    ]
    # always-present fields keep publishing; no clear storm on them
    assert "renewvan/battery/wattcycle/voltage_v" in publisher.published


def test_no_clears_while_fields_stay_present() -> None:
    publisher, last = RecordingPublisher(), set()
    import asyncio

    asyncio.run(_poll(publisher, [DISCHARGING], last))
    asyncio.run(_poll(publisher, [DISCHARGING], last))
    assert publisher.cleared == []


# --- per-cell data on the bus (hub ADR 0022) ---

import json  # noqa: E402
from dataclasses import replace  # noqa: E402

T = "renewvan/battery/wattcycle/"
CELLS_4 = replace(
    DISCHARGING,
    balance_word=0,
    protection_word=0,
    fet_byte=0x03,
    cell_voltages_v=(3.331, 3.334, 3.333, 3.333),
)
CELLS_8 = replace(
    CELLS_4,
    cell_count=8,
    temperatures_c=(13.0, 13.0),
    cell_voltages_v=(3.3, 3.3, 3.3, 3.3, 3.3, 3.3, 3.3, 3.31),
)


def test_cell_arrays_are_published_as_json_with_null_slots() -> None:
    import asyncio

    publisher, last = RecordingPublisher(), set()
    reading = replace(CELLS_4, cell_voltages_v=(3.331, None, 3.333, 3.333))
    asyncio.run(_poll(publisher, [reading], last))
    assert json.loads(publisher.published[T + "cell_voltages_v"]) == [3.331, None, 3.333, 3.333]
    assert json.loads(publisher.published[T + "cell_balancing"]) == [False, None, False, False]
    assert json.loads(publisher.published[T + "protections"]) == []
    assert publisher.published[T + "allow_charge"] == "true"
    assert publisher.published[T + "cell_voltage_delta_v"] == "0.002"


def test_cell_count_change_replaces_arrays_wholesale_in_one_cycle() -> None:
    import asyncio

    publisher, last = RecordingPublisher(), set()
    asyncio.run(_poll(publisher, [CELLS_4], last))
    asyncio.run(_poll(publisher, [CELLS_8], last))
    assert len(json.loads(publisher.published[T + "cell_voltages_v"])) == 8
    assert len(json.loads(publisher.published[T + "cell_balancing"])) == 8
    assert len(json.loads(publisher.published[T + "temperatures_c"])) == 2
    assert publisher.published[T + "cell_voltage_delta_v"] == "0.01"
    assert publisher.cleared == []  # replaced, not retracted


def test_silent_cell_register_clears_every_cell_property() -> None:
    import asyncio

    publisher, last = RecordingPublisher(), set()
    asyncio.run(_poll(publisher, [CELLS_4], last))
    asyncio.run(_poll(publisher, [replace(CELLS_4, cell_voltages_v=None)], last))
    assert sorted(publisher.cleared) == [
        T + "cell_balancing",
        T + "cell_voltage_delta_v",
        T + "cell_voltage_max_v",
        T + "cell_voltage_min_v",
        T + "cell_voltages_v",
    ]


def test_scalars_cleared_when_cells_drop_below_two_readable() -> None:
    import asyncio

    publisher, last = RecordingPublisher(), set()
    asyncio.run(_poll(publisher, [CELLS_4], last))
    asyncio.run(_poll(publisher, [replace(CELLS_4, cell_voltages_v=(3.3, None, None, None))], last))
    assert T + "cell_voltage_max_v" in publisher.cleared
    assert json.loads(publisher.published[T + "cell_voltages_v"]) == [3.3, None, None, None]
