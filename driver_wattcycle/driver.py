"""Publish loop: poll the WattCycle BMS over BLE and publish the hub's
battery entity under renewvan/battery/<id>/<property>.

This driver owns the sensing (Q1): a bleak BLE GATT client talking JBD
protocol to a DH04/JBD BMS. All live fields, no identity fields — the
battery schema's properties are all per-poll; Driver identity goes to
renewvan/wattcycle/info via the manifest (see publisher.py), and the instance
names its Driver (and optionally a label) in retained `driver` / `name`
topics published on every MQTT (re)connect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

from driver_wattcycle.ble import BleError, WattCycleBle
from driver_wattcycle.config import AppConfig
from driver_wattcycle.driver_info import build_info, read_board_serial
from driver_wattcycle.fields import live_fields
from driver_wattcycle.publisher import DRIVER_KEY, Publisher

logger = logging.getLogger(__name__)


def _topic(instance_id: str, prop: str) -> str:
    return f"renewvan/battery/{instance_id}/{prop}"


def identity_topics(config: AppConfig) -> dict[str, str]:
    """Retained identity fields of this battery instance: owning Driver, optional name."""
    identity = {_topic(config.driver.id, "driver"): json.dumps(DRIVER_KEY)}
    if config.driver.name:
        identity[_topic(config.driver.id, "name")] = json.dumps(config.driver.name)
    return identity


async def read_and_publish(
    publisher: Publisher, ble: WattCycleBle, config: AppConfig, last_props: set[str]
) -> None:
    """One poll: read the BMS and publish every derived live field.

    Computed fields (time_to_go_s, charge_state) drop out of live_fields
    when the bank is idle/charging; the topics are retained, so a field
    absent from this poll that was published last poll is cleared (an empty
    retained payload), not left stale on the bus. `last_props` is the
    caller's record of the previous poll, updated in place.
    """
    reading = await ble.read()
    fields = live_fields(reading)
    for prop in last_props - fields.keys():
        publisher.clear(_topic(config.driver.id, prop))
    last_props.clear()
    last_props.update(fields)
    for prop, value in fields.items():
        publisher.publish(_topic(config.driver.id, prop), json.dumps(value))
    logger.debug(
        "battery %s: %.2fV %.2fA %d%%",
        config.driver.id,
        reading.voltage_v,
        reading.current_a,
        reading.soc_pct,
    )


async def run(config: AppConfig) -> None:
    if not config.driver.mac:
        raise SystemExit(
            "driver.mac is required (BLE address of the battery, e.g. AA:C2:37:0A:CC:05)"
        )
    # Version is stamped by the release build (DRIVER_VERSION); unset reports "dev".
    info = build_info(os.environ.get("DRIVER_VERSION"), read_board_serial())
    publisher = Publisher(config.mqtt, info, identity_topics(config))
    ble = WattCycleBle(
        mac=config.driver.mac,
        uuid_variant=config.driver.uuid_variant,
        adapter=config.driver.hci_adapter,
        connect_timeout_s=config.driver.connect_timeout_s,
    )
    publisher.connect()
    # Cancel the poll loop on SIGTERM/SIGINT (systemctl stop) so the finally
    # block disconnects the BMS -- a leaked link stops it advertising, which
    # would block reconnects (see ble._ensure_connected).
    task = asyncio.current_task()
    assert task is not None
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        # Props published on the previous poll, so fields that go absent
        # (time_to_go_s, charge_state when idle) get retracted, not left stale.
        last_props: set[str] = set()
        while True:
            try:
                await read_and_publish(publisher, ble, config, last_props)
            except BleError as exc:
                logger.warning(
                    "BLE read failed (%s); retrying in %.0fs", exc, config.driver.retry_delay_s
                )
                await asyncio.sleep(config.driver.retry_delay_s)
                continue
            await asyncio.sleep(config.driver.update_interval_ms / 1000)
    except asyncio.CancelledError:
        logger.info("Shutting down")
    finally:
        await ble.close()
        publisher.disconnect()
