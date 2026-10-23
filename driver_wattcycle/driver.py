"""Publish loop: poll the WattCycle BMS over BLE and publish the hub's
battery entity under renewvan/battery/<id>/<property>.

This driver owns the sensing (Q1): a bleak BLE GATT client talking JBD
protocol to a DH04/JBD BMS. All live fields, no identity fields — the
battery schema's properties are all per-poll; device identity goes to
renewvan/battery/info via the manifest (see publisher.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

from driver_wattcycle.ble import BleError, WattCycleBle
from driver_wattcycle.config import AppConfig
from driver_wattcycle.device_info import build_info, read_board_serial
from driver_wattcycle.fields import live_fields
from driver_wattcycle.publisher import Publisher

logger = logging.getLogger(__name__)


def _topic(device_id: str, prop: str) -> str:
    return f"renewvan/battery/{device_id}/{prop}"


async def read_and_publish(publisher: Publisher, ble: WattCycleBle, config: AppConfig) -> None:
    """One poll: read the BMS and publish every derived live field."""
    reading = await ble.read()
    for prop, value in live_fields(reading).items():
        publisher.publish(_topic(config.device.id, prop), json.dumps(value))
    logger.debug(
        "battery %s: %.2fV %.2fA %d%%",
        config.device.id, reading.voltage_v, reading.current_a, reading.soc_pct,
    )


async def run(config: AppConfig) -> None:
    if not config.device.mac:
        raise SystemExit(
            "device.mac is required (BLE address of the battery, e.g. AA:C2:37:0A:CC:05)"
        )
    # Version is stamped by the release build (DEVICE_VERSION); unset reports "dev".
    info = build_info(os.environ.get("DEVICE_VERSION"), read_board_serial())
    publisher = Publisher(config.mqtt, info)
    ble = WattCycleBle(
        mac=config.device.mac,
        uuid_variant=config.device.uuid_variant,
        adapter=config.device.hci_adapter,
        connect_timeout_s=config.device.connect_timeout_s,
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
        while True:
            try:
                await read_and_publish(publisher, ble, config)
            except BleError as exc:
                logger.warning(
                    "BLE read failed (%s); retrying in %.0fs", exc, config.device.retry_delay_s
                )
                await asyncio.sleep(config.device.retry_delay_s)
                continue
            await asyncio.sleep(config.device.update_interval_ms / 1000)
    except asyncio.CancelledError:
        logger.info("Shutting down")
    finally:
        await ble.close()
        publisher.disconnect()
