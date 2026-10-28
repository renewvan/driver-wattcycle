# driver-wattcycle

Just for WattCycle: BLE driver for WattCycle JBD BMS batteries, publishing battery telemetry over MQTT.

Scaffolded from `renewvan/driver-tank`'s reference shape via `renewvan/skills`'
`scaffold-driver` skill (per the **Driver** term in `hub/docs/architecture.md`'s
Terminology section: anything that publishes one specific device or feed
onto the renewvan bus, normalized into the device model). Layout: pure,
unit-testable conversion math with no I/O, a layered
`config.default.ini`/`config.ini` loader (`config.py`), a persistent MQTT
publisher with LWT (`publisher.py`), a thin publish loop (`driver.py`), a
systemd unit, and its own Dockerfile + CI publishing a versioned image.

**Implemented and verified live** against a WattCycle 314Ah Super Mini
(DH04 BMS) on the hub: BLE connect with UUID auto-detection, JBD 0x03
polling, and MQTT publishing to `renewvan/battery/<id>/` (2026-10-23).

## Design round (resolved)

Settled via the `scaffold-driver` skill's Step 5 round against the real
hardware (WattCycle 314Ah Super Mini, DH04 BMS, verified live from the hub
2026-10-23):

- [x] **Sensing or remapping?** Owns sensing — a bleak BLE GATT client that
      talks JBD protocol directly on the BMS (same approach as
      `dbus-serialbattery`'s `lltjbd_ble.py`, minus the dbus/Venus layer).
- [x] **Topics published** — see below; publishes the hub's existing
      `battery` entity, not a wattcycle entity.
- [x] **Hub schema** — extends `hub/schema/battery.schema.json` as-is; no
      new schema, no fixtures, no demo scenario (existing-entity rule).
- [x] **Wiring** — see below; BLE, no pairing, no auth on DH04.
- [x] **`DriverConfig`** — `mac`, `uuid_variant`, `hci_adapter`,
      `connect_timeout_s`, `retry_delay_s`, `name`, mirrored in
      `config.default.ini`.
- [ ] **Hub integration** — pending a tagged image: add a
      `driver-wattcycle` service block to `hub/docker-compose.yml` (pattern:
      driver-tank's block) and any new keys to `hub/.env.example`. Open
      detail for then: the container needs D-Bus access to the host's
      BlueZ daemon (mount `/var/run/dbus` + `NET_ADMIN`, or host
      networking) — unlike driver-tank's serial device.

## Topics published

Retained, under `renewvan/battery/<id>/` (`id` from `[driver] id`, default
`wattcycle`; set `id = house` on a van where this BLE BMS is the single
house bank and no Venus remap publishes that id):

**Live fields** (republished every poll, `update_interval_ms` default 3000):
- `soc_pct` — number 0–100, from JBD frame `0x03`
- `voltage_v` — number, pack voltage (4S cell sum), frame `0x03`
- `current_a` — number, signed (positive = charging, negative = discharging)
- `power_w` — number, signed, computed `voltage_v × current_a`
- `temperature_c` — number, max of the BMS's temperature probes (4 on the
  tested DH04), frame `0x03`
- `time_to_go_s` — whole seconds, derived `remaining_ah / |discharge
  current|`; omitted while charging or inside the current deadband, and the
  retained topic is **cleared** (empty retained payload) so it cannot linger
- `capacity_ah` — number, nominal pack capacity, **raw** from frame `0x03`
  (0.01 Ah resolution; 314.0 on the tested unit), relayed unchanged
- `remaining_ah` — number, remaining pack capacity, **raw** from frame
  `0x03` (the BMS's coulomb-counted fuel gauge); publish both so consumers
  can cross-check `soc_pct` against `remaining_ah / capacity_ah`
- `charge_state` — `"discharging"` only; otherwise omitted and the retained
  topic cleared. The schema's
  enum is charger-stage oriented (bulk/absorption/float…) and a BMS only
  knows current direction, so per the schema's guidance only the state it
  actually knows is published.

**Per-cell BMS data** (hub ADR 0022; all retained, republished every poll;
needs a second read of JBD frame `0x04` per poll):
- `cell_voltages_v` — array of volts (3 decimals), index i = cell i+1, from
  `0x04` (2-byte big-endian mV per cell). `null` = unreadable slot (0 or
  0xFFFF mV), never 0; the array keeps its length. Absent (and cleared) when
  `0x04` is silent or its length disagrees with the `0x03` `cell_count`.
- `cell_voltage_max_v` / `cell_voltage_min_v` / `cell_voltage_delta_v` —
  derived over the non-null cells; all three or none, in the same cycle as
  the array; omitted with fewer than 2 readable cells.
- `cell_balancing` — boolean array index-aligned with `cell_voltages_v`
  (balance words, bit i = cell i+1); `null` for a cell whose voltage is
  unreadable; absent if alignment cannot be guaranteed.
- `temperatures_c` — array, one per `0x03` probe, `null` for an unreadable
  probe. `temperature_c` stays the max of the readable probes.
- `allow_charge` / `allow_discharge` — booleans from the FET byte (bit0
  charge, bit1 discharge).
- `protections` — array of names from protection bits 0-12 in vocabulary
  order; `[]` = none active; a set bit with no name publishes `other` and
  logs the raw word.
- Never published: charge/discharge limits (the BMS reports none; this
  driver never computes them).

When a property stops being published (cell count change leaves
`cell_balancing` misaligned, a silent BMS) its retained topic is cleared
with an empty payload. On a cell-count change the arrays are replaced
wholesale at the new length.

The balance, protection and FET **bit layouts are unverified on hardware**
(the DH04 capture only saw `0x0000`/`0x03` at rest); see issue #5.

Not published (JBD reports them; no schema field): cycle count, production
date.

**Command topics**: none — the JBD BLE protocol is treated read-only;
settings writes are out of scope.

**Instance identity** (retained, republished on every MQTT (re)connect):
- `renewvan/battery/<id>/driver` — `"wattcycle"`, the owning Driver's key
- `renewvan/battery/<id>/name` — optional human label, from `[driver] name`
  in `config.ini` (e.g. `name = House bank`). Not published when unset: the
  driver does not read the BMS's advertised BLE name.

**Driver liveness** (keyed by the Driver, not the battery entity):
- `renewvan/wattcycle/health` — `online`/`offline` via MQTT LWT
- `renewvan/wattcycle/info` — one retained JSON message describing this
  Driver (see `hub`'s `schema/driver-info.schema.json`), published on every
  connect before `online`.

The Driver key is `wattcycle` (was `battery`; named for the source per hub
ADR-0008). **Cutover:** clear the stale retained topics once with
`mosquitto_pub -r -n -t renewvan/battery/info` and
`mosquitto_pub -r -n -t renewvan/battery/health`. A live `config.ini` keeps
its `[device]` section until edited to `[driver]` (no compatibility shim), and
the image build arg is now `DRIVER_VERSION`.

Payload shapes match `hub`'s `schema/battery.schema.json` for the entity
and `hub`'s `schema/driver-info.schema.json` for info.

## Wiring

BLE only — no cable, no pairing:

1. The battery advertises over BLE (name prefix `WT...`). Find its MAC on the hub:
   `bluetoothctl scan on`.
2. Set `mac` in `config.ini`. No PIN/pairing is needed for the DH04 BMS —
   it accepts the JBD command frame straight after connect. (The XDZN
   variant used in some WattCycle batches requires "HiLink" auth and is out
   of scope for this driver.)
3. `uuid_variant = auto` detects standard JBD (`ff01`/`ff02`) vs DH04
   (`fff1`/`fff2`) GATT characteristics on connect (per
   venus-os_dbus-serialbattery PR #441); force `jbd`/`dh04` only if a new
   batch misbehaves.
4. Requirements on the host: BlueZ running with the adapter unblocked
   (`rfkill unblock bluetooth`), and exclusive access — only one client can
   hold the battery's BLE link at a time, so this driver must be the only
   thing talking to that MAC. Same exclusivity applies *within* the link:
   no second D-Bus client may subscribe to the BMS's notify characteristic
   (`fff1`/`ff01`) while the driver runs — verified 2026-10-28 with `btmon`
   (`.scratch/ble-second-notify-client`): the second subscription sends no
   ATT traffic (the BMS cannot know about it), yet BlueZ's multi-client
   notification fan-out corrupts the fragmented stream delivered to the
   driver (byte-level re-splicing; the on-air frames stay pristine). The
   driver survives each bad read (hardened poll loop) but loses polls until
   the second client unsubscribes. Debuggers: sniff passively with
   `btmon`, or stop the driver first.
5. Stale link recovery: a killed process (`kill -9`, power loss) can leave
   the BMS connected at the controller level. A connected BMS stops
   advertising, so scan-based reconnect fails. Fix: `sudo systemctl restart
   bluetooth`, or `hcitool con` to confirm, then restart the driver. SIGTERM
   and SIGINT disconnect cleanly; the driver connects by address first and
   only scans on cold start.

## Development

```bash
git clone git@github.com:renewvan/driver-wattcycle.git
cd driver-wattcycle
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-test.txt
```

Keep conversion/decision logic in pure functions tested against fixture
values — no MQTT broker or real hardware required for any of it. Before
opening a PR: `pytest tests/ -v` must pass (same command CI runs), and any
change to a published field needs a matching
`hub/schema/battery.schema.json` update in a `hub` PR (per
`hub/docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`,
the two repos are never coupled at build time, only at the schema-version
level).

## Configuration

`config.default.ini` ships every key with a default. Copy
`config.ini.example` to `config.ini` (gitignored) and override only what
differs.

## Running

```bash
pip install -r requirements.txt
python -m driver_wattcycle.main --config config.ini
```

Install `driver_wattcycle/systemd/driver-wattcycle.service` to run at boot
(`Restart=on-failure`).

## Testing

```bash
pip install -r requirements.txt -r requirements-test.txt
pytest tests/ -v
```

## Releasing

Tagging a GitHub release builds and publishes
`ghcr.io/<owner>/driver-wattcycle:<tag>`, which `hub`'s deployment compose file pins
by tag (never builds from source — see
`hub/docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`).
