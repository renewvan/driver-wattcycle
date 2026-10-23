"""Layered INI config: config.default.ini (shipped) + config.ini (local
override).

Same two-file pattern as every renewvan/driver-* repo (see
driver-tank/driver_tank/config.py): config.default.ini ships every key with a
safe default and is never edited at an install; config.ini is a gitignored,
install-local file that overrides only what differs.
"""

from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.default.ini"


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    client_id: str
    reconnect_min_delay: float
    reconnect_max_delay: float


@dataclass(frozen=True)
class DeviceConfig:
    id: str  # battery bank instance: renewvan/battery/<id>/<property>
    update_interval_ms: int
    mac: str  # BLE address of the BMS, e.g. "AA:C2:37:0A:CC:05" (required at runtime)
    uuid_variant: str  # "auto" | "jbd" (ff01/ff02) | "dh04" (fff1/fff2)
    hci_adapter: str  # local BlueZ adapter, e.g. "hci0"
    connect_timeout_s: float
    retry_delay_s: float  # delay between BLE reconnect attempts


@dataclass(frozen=True)
class AppConfig:
    mqtt: MqttConfig
    device: DeviceConfig


def load_config(
    default_path: Path = DEFAULT_CONFIG_PATH, local_path: Path | None = None
) -> AppConfig:
    """Load config.default.ini, then layer config.ini (if present) on top."""
    parser = configparser.ConfigParser()
    parser.read(default_path)
    if local_path is not None and local_path.exists():
        parser.read(local_path)
    else:
        logger.info("No local config override at %s; using defaults only", local_path)

    mqtt = parser["mqtt"]
    device = parser["device"]

    return AppConfig(
        mqtt=MqttConfig(
            host=mqtt.get("host", "localhost"),
            port=mqtt.getint("port", 1883),
            username=mqtt.get("username") or None,
            password=mqtt.get("password") or None,
            client_id=mqtt.get("client_id", "driver-wattcycle"),
            reconnect_min_delay=mqtt.getfloat("reconnect_min_delay", 1.0),
            reconnect_max_delay=mqtt.getfloat("reconnect_max_delay", 30.0),
        ),
        device=DeviceConfig(
            id=device.get("id", "wattcycle"),
            update_interval_ms=device.getint("update_interval_ms", 3000),
            mac=device.get("mac", ""),
            uuid_variant=device.get("uuid_variant", "auto"),
            hci_adapter=device.get("hci_adapter", "hci0"),
            connect_timeout_s=device.getfloat("connect_timeout_s", 10.0),
            retry_delay_s=device.getfloat("retry_delay_s", 5.0),
        ),
    )
