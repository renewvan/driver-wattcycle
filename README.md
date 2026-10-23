# driver-wattcycle

Just for WattCycle: BLE driver for WattCycle JBD BMS batteries, publishing battery telemetry over MQTT.

Scaffolded from `renewvan/driver-tank`'s reference shape via `renewvan/skills`'
`scaffold-driver` skill (per the **Device** term in `hub/docs/architecture.md`'s
Terminology section: "anything that publishes one specific device or feed
onto the renewvan bus, normalized into the device model"). Layout: pure,
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
- [x] **`DeviceConfig`** — `mac`, `uuid_variant`, `hci_adapter`,
      `connect_timeout_s`, `retry_delay_s`, mirrored in
      `config.default.ini`.
- [ ] **Hub integration** — pending a tagged image: add a
      `driver-wattcycle` service block to `hub/docker-compose.yml` (pattern:
      driver-tank's block) and any new keys to `hub/.env.example`. Open
      detail for then: the container needs D-Bus access to the host's
      BlueZ daemon (mount `/var/run/dbus` + `NET_ADMIN`, or host
      networking) — unlike driver-tank's serial device.

## Topics published

Retained, under `renewvan/battery/<id>/` (`id` from `[device] id`, default
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
  current|`; omitted while charging or inside the current deadband
- `charge_state` — `"discharging"` only; omitted otherwise. The schema's
  enum is charger-stage oriented (bulk/absorption/float…) and a BMS only
  knows current direction, so per the schema's guidance only the state it
  actually knows is published.

Not published (JBD reports them, but `battery.schema.json` has no field —
candidate future schema extension, needs a hub PR first): per-cell
voltages, cycle count, nominal/remaining capacity Ah, protection/balancing
flags, production date.

**Command topics**: none — the JBD BLE protocol is treated read-only;
settings writes are out of scope.

**Device liveness** (not battery-keyed):
- `renewvan/battery/health` — `online`/`offline` via MQTT LWT
- `renewvan/battery/info` — one retained JSON message describing this
  Device (see `hub`'s `schema/device-info.schema.json`), published on every
  connect before `online`.

Payload shapes match `hub`'s `schema/battery.schema.json` for the entity
and `hub`'s `schema/device-info.schema.json` for info.

## Wiring

BLE only — no cable, no pairing:

1. The battery advertises over BLE (name prefix `WT...`, e.g.
   `WTaHdAZ2601270580`). Find its MAC on the hub:
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
   thing talking to that MAC.
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
