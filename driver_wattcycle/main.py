#!/usr/bin/env python3
"""driver-wattcycle entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from driver_wattcycle.config import DEFAULT_CONFIG_PATH, load_config
from driver_wattcycle.driver import run


def main() -> None:
    parser = argparse.ArgumentParser(description="driver-wattcycle: MQTT WattCycle driver")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "-c", "--config", default="config.ini", help="Path to local config.ini override"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    config = load_config(default_path=DEFAULT_CONFIG_PATH, local_path=Path(args.config))
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
