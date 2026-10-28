"""Derived battery-entity fields: pure math from a BatteryReading to the
hub battery schema's optional properties. See CONTEXT.md for semantics.
"""

from __future__ import annotations

from driver_wattcycle.jbd import BatteryReading

# |current| below this is "idle", not charging/discharging (see CONTEXT.md).
# Schema sign convention: positive current = charging.
DEADBAND_A = 0.1


def power_w(voltage_v: float, current_a: float) -> float:
    """Signed power; the BMS does not report it (schema: positive = charging)."""
    return voltage_v * current_a


def charge_state(current_a: float, deadband_a: float = DEADBAND_A) -> str | None:
    """`discharging` when the bank discharges beyond the deadband, else None.

    The battery schema's charge_state enum is charger-stage oriented
    (bulk/absorption/float...); a BMS only knows current direction, so per
    the schema's own guidance ("only sources that know the stage publish
    it") the field is omitted unless the bank is discharging.
    """
    if current_a < -deadband_a:
        return "discharging"
    return None
def time_to_go_s(
    remaining_ah: float, current_a: float, deadband_a: float = DEADBAND_A
) -> int | None:
    """Seconds until empty at the present discharge rate; None when charging
    or idle (a fabricated estimate is worse than an absent optional field)."""
    if current_a >= -deadband_a:
        return None
    return round(remaining_ah / -current_a * 3600)


def max_temperature_c(temperatures_c: tuple[float, ...]) -> float | None:
    """Hottest probe; one hot spot is the alarm-worthy case (schema has one field)."""
    return round(max(temperatures_c), 2) if temperatures_c else None


def live_fields(reading: BatteryReading) -> dict[str, float | int | str | None]:
    """Map a JBD 0x03 reading to battery-entity live properties.

    Keys with a None value (time_to_go_s when not discharging,
    temperature_c when the BMS reports no probes) are dropped by the caller
    or here — dropped here: the schema marks them optional, and publishing
    a JSON null would fail its number type.
    """
    fields: dict[str, float | int | str | None] = {
        "soc_pct": reading.soc_pct,
        "voltage_v": round(reading.voltage_v, 2),
        "current_a": round(reading.current_a, 2),
        "power_w": round(power_w(reading.voltage_v, reading.current_a), 2),
        "temperature_c": max_temperature_c(reading.temperatures_c),
        "time_to_go_s": time_to_go_s(reading.remaining_ah, reading.current_a),
        "charge_state": charge_state(reading.current_a),
        # Raw BMS-reported fuel gauge (frame 0x03 words), relayed unchanged —
        # consumers cross-check soc_pct against remaining/capacity.
        "capacity_ah": round(reading.capacity_ah, 2),
        "remaining_ah": round(reading.remaining_ah, 2),
    }
    return {k: v for k, v in fields.items() if v is not None}
