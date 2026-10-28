"""Derived battery-entity fields: pure math from a BatteryReading to the
hub battery schema's optional properties. See CONTEXT.md for semantics.
"""

from __future__ import annotations

import logging

from driver_wattcycle.jbd import MAX_CELLS, PROTECTION_NAMES, BatteryReading

logger = logging.getLogger(__name__)

MAX_PROBES = 16  # hub battery schema temperatures_c maxItems

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


def max_temperature_c(temperatures_c: tuple[float | None, ...]) -> float | None:
    """Hottest readable probe; one hot spot is the alarm-worthy case (schema has one field)."""
    readable = [t for t in temperatures_c if t is not None]
    return round(max(readable), 2) if readable else None


def probe_temperatures_c(temperatures_c: tuple[float | None, ...]) -> list[float | None] | None:
    """Per-probe array (None = unreadable probe, length kept); None when there
    are no probes or more than the schema's 16 (omitted, not truncated)."""
    if not temperatures_c or len(temperatures_c) > MAX_PROBES:
        return None
    return [None if t is None else round(t, 2) for t in temperatures_c]


def cell_scalars(cells: tuple[float | None, ...]) -> dict[str, float]:
    """max/min/delta over the non-null cells, all three or none (fewer than 2).

    Derived in integer millivolts so the delta is exact, not float noise.
    """
    mv = [round(v * 1000) for v in cells if v is not None]
    if len(mv) < 2:
        return {}
    return {
        "cell_voltage_max_v": max(mv) / 1000,
        "cell_voltage_min_v": min(mv) / 1000,
        "cell_voltage_delta_v": (max(mv) - min(mv)) / 1000,
    }


def cell_balancing(
    cells: tuple[float | None, ...], balance_word: int | None
) -> list[bool | None] | None:
    """Per-cell balancing, index-aligned with `cells` (bit i of the word = cell i+1).

    True = balancing; False = the bit is clear; None = unknown (the cell
    itself is unreadable, so we do not vouch for its balance bit). Returns
    None (field omitted) when the word is unknown or alignment with the cell
    array cannot be guaranteed (hub ADR 0022: never padded or truncated).
    """
    if balance_word is None or not 1 <= len(cells) <= MAX_CELLS:
        return None
    return [None if v is None else bool(balance_word >> i & 1) for i, v in enumerate(cells)]


def protections(word: int | None) -> list[str] | None:
    """Active protections in vocabulary order; [] = reported and none active.

    Bits 0-12 map to the first 13 vocabulary names; any other set bit is
    `other` and the raw word is logged (hub ADR 0022). None = not reported.
    """
    if word is None:
        return None
    active = [name for bit, name in enumerate(PROTECTION_NAMES) if word >> bit & 1]
    if word >> len(PROTECTION_NAMES):
        logger.warning("BMS protection bits outside the known vocabulary: word=%#06x", word)
        active.append("other")
    return active


def live_fields(reading: BatteryReading) -> dict[str, object]:
    """Map a JBD 0x03 reading to battery-entity live properties.

    Keys with a None value (time_to_go_s when not discharging,
    temperature_c when the BMS reports no probes) are dropped here: the
    schema marks them optional, and publishing a JSON null would fail its
    type. Empty lists and False are values and are kept (`protections: []`
    means none active). The driver never publishes the BMS limits.
    """
    fields: dict[str, object] = {
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
        "temperatures_c": probe_temperatures_c(reading.temperatures_c),
        "protections": protections(reading.protection_word),
    }
    if reading.fet_byte is not None:
        fields["allow_charge"] = bool(reading.fet_byte & 0b01)
        fields["allow_discharge"] = bool(reading.fet_byte & 0b10)
    if reading.cell_voltages_v:
        # One atomic snapshot: the array and everything derived from it, together.
        fields["cell_voltages_v"] = list(reading.cell_voltages_v)
        fields.update(cell_scalars(reading.cell_voltages_v))
        fields["cell_balancing"] = cell_balancing(reading.cell_voltages_v, reading.balance_word)
    return {k: v for k, v in fields.items() if v is not None}
