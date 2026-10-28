# driver-wattcycle

Domain glossary for driver-wattcycle: what each published field *means*,
independent of the Python that computes it. Settled by the `scaffold-driver`
skill's Step 5 design round (2026-10-23) against a live WattCycle 314Ah
Super Mini (DH04 BMS) read over BLE from the hub.

## Language

**Driver key (`wattcycle`)**: this Driver's identity on the bus —
`manifest.json` `driver`, `renewvan/wattcycle/info`, `renewvan/wattcycle/health`,
and the retained `driver` field of its battery instance. It is the repo name
minus `driver-`: named for the source, not the entity (hub ADR-0008).
_Avoid_: `battery` as the key — that is the entity, and was a bug here.

**Battery bank instance (`[driver] id`)**: one BMS = one bank. Topic is
`renewvan/battery/<id>/<property>`, *not* `renewvan/wattcycle/...` — the
driver publishes the hub's existing `battery` entity; the repo name is the
source, the topic is the entity (hub ADR-0008). Default id `wattcycle`;
set `house` when this BMS is the van's single house bank and nothing else
publishes that id. A Victron shunt via driver-venus and this BMS can both
describe the same physical bank; the instance publishes retained
`renewvan/battery/<id>/driver` = `"wattcycle"` (and `name` when `[driver]
name` is set) so consumers know which Driver owns it, and the dashboard lets
the user pick the **Primary battery** (`renewvan/hub/primary-battery`).

**JBD protocol**: the command/response framing the BMS speaks over BLE —
request `DD A5 <cmd> <len> <cksum> 77`, response `DD <cmd> ... <cksum> 77`.
This driver uses frame `0x03` (pack info: voltage, current, SOC,
capacities, cycles, temps) and frame `0x04` (per-cell voltages). Same
protocol family as `dbus-serialbattery`'s `lltjbd` driver; this repo
reimplements the read-only subset over BLE, it does not port that package.

**DH04 BMS**: the JBD-compatible BMS variant in newer WattCycle batteries
(e.g. 314Ah Super Mini, model DH04SC02-200A). GATT characteristics
`fff1` (notify) / `fff2` (write) instead of standard JBD `ff01`/`ff02`.
No pairing, no auth. Identified at runtime by `uuid_variant = auto`
(Pattern per venus-os_dbus-serialbattery PR #441). Some WattCycle batches
use an XDZN BMS instead (requires "HiLink" auth) — **not** this driver.

**`soc_pct`**: state of charge 0–100, as reported by the BMS frame `0x03`
(not recomputed from current integration).
_Avoid_: `soc`, `charge_pct` — schema name is canonical.

**`voltage_v`**: total pack voltage in volts (4S LiFePO4 = 4 cells; the BMS
reports the sum directly in frame `0x03`).
_Avoid_: `v`, `pack_mv` — publish volts, not millivolts.

**`current_a`**: signed pack current in amperes. Positive = charging,
negative = discharging (hub battery schema convention). From frame `0x03`,
0.01 A resolution.
_Avoid_: `amps`, unsigned magnitude — the sign is the data.

**`power_w`**: signed power in watts, computed `voltage_v × current_a`. The
BMS does not report power directly.
_Avoid_: sourcing it from the BMS — it is derived, keep the derivation in
one pure function.

**`temperature_c`**: degrees Celsius, **max** of the BMS's temperature
probes (4 on the tested DH04). Max, not average or first: a single hot cell
MOSFET is the alarm-worthy case.
_Avoid_: publishing per-probe values — battery schema has one field.

**`capacity_ah`**: nominal pack capacity in amp-hours, reported raw by the
BMS frame `0x03` (0.01 Ah resolution; 314.0 on the tested unit). Relayed
unchanged — consumers cross-check `soc_pct` against
`remaining_ah / capacity_ah`.
_Avoid_: deriving it from energy counters — it is a BMS-reported constant.

**`remaining_ah`**: remaining pack capacity in amp-hours, reported raw by
the BMS frame `0x03` (not recomputed here). The BMS's own coulomb-counted
fuel gauge; also the input to `time_to_go_s`.
_Avoid_: integrating current locally — the BMS already counts coulombs.

**`time_to_go_s`**: estimated seconds until empty at the present discharge
rate, derived `remaining_ah / |current_a|`. Omitted (not zero) while
charging or in the near-zero-current deadband — a made-up number is worse
than absent (schema marks it optional).
_Avoid_: `ttl`, `time_remaining_s`.

**`charge_state`**: `discharging` when `current_a` is beyond the deadband in
the negative direction; otherwise omitted. The schema enum is charger-stage
oriented (bulk/absorption/float/…), and the BMS only knows current direction,
so it never publishes a stage it cannot know. This is coarser than the Venus
remap of the same bank.

**Not published (schema has no field)**: per-cell voltages, cycle count,
balancing/protection flags, production date. JBD reports them;
battery.schema.json has no slot. Candidate future `bms` entity — needs a
hub PR before any publish.

**Deadband (idle threshold)**: the |current| below which the bank is
`idle`. Config-constant, not per-message; picked once in implementation.
