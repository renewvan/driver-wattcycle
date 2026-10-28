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


async def _poll(
    publisher: RecordingPublisher, readings: list[BatteryReading], last: set
) -> None:
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
