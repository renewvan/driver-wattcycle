"""Derived battery-entity fields (pure math, no I/O).

Worked examples come from the live 2026-10-23 capture of the WattCycle 314Ah
(13.35 V, -0.43 A, 245.28 Ah remaining, 314.0 Ah nominal) and the hub
battery schema's sign convention (positive = charging).
"""

from __future__ import annotations

import pytest

from driver_wattcycle.fields import (
    charge_state,
    max_temperature_c,
    power_w,
    time_to_go_s,
)

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
