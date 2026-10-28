"""BLE client for a JBD/DH04 BMS (e.g. WattCycle batteries) via bleak/BlueZ.

Approach mirrors dbus-serialbattery's lltjbd_ble.py (venus-os PR #441),
minus the dbus layer: connect by MAC, auto-detect the GATT characteristic
pair (standard JBD ff01/ff02 vs DH04 fff1/fff2), keep the notification
subscription open for the whole session, send a JBD read command and
reassemble the fragmented notification response into one frame.

Hardware I/O is not unit-tested; read() is, against a fake BleakClient
(tests/test_ble.py). The frame math it relies on is jbd.py, tested against
live captures. Verified on the hub against a WattCycle 314Ah Super Mini
(DH04).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from driver_wattcycle.jbd import (
    REG_CELLS,
    REG_GENERAL,
    BatteryReading,
    build_read_command,
    decode_cells,
    decode_general,
    extract_frames,
    validate_frame,
)

logger = logging.getLogger(__name__)

# (write/tx, notify/rx) characteristic pairs, per BMS variant
UUIDS = {
    "jbd": (
        "0000ff02-0000-1000-8000-00805f9b34fb",
        "0000ff01-0000-1000-8000-00805f9b34fb",
    ),
    "dh04": (
        "0000fff2-0000-1000-8000-00805f9b34fb",
        "0000fff1-0000-1000-8000-00805f9b34fb",
    ),
}
RESPONSE_TIMEOUT_S = 5.0


class BleError(Exception):
    """A read could not be completed; the client is disconnected and the
    caller should retry after a delay."""


class WattCycleBle:
    def __init__(
        self,
        mac: str,
        uuid_variant: str = "auto",
        adapter: str = "hci0",
        connect_timeout_s: float = 10.0,
    ) -> None:
        if uuid_variant not in ("auto", *UUIDS):
            raise ValueError(f"unknown uuid_variant {uuid_variant!r}; use auto/jbd/dh04")
        self._mac = mac
        self._variant = uuid_variant
        self._adapter = adapter
        self._connect_timeout_s = connect_timeout_s
        self._client: BleakClient | None = None
        self._tx_uuid: str | None = None
        self._rx_uuid: str | None = None
        self._buffer = bytearray()
        self._response: asyncio.Future[bytes] | None = None

    def _detect_uuids(self, client: BleakClient) -> tuple[str, str]:
        """Pick the (tx, rx) pair from the device's GATT table; DH04 first
        (more specific), standard JBD as fallback."""
        available = {
            str(char.uuid) for service in client.services for char in service.characteristics
        }
        for name in ("dh04", "jbd"):
            tx, rx = UUIDS[name]
            if tx in available and rx in available:
                logger.info("Using %s UUIDs (%s/%s)", name, rx[4:8], tx[4:8])
                return tx, rx
        raise BleakError(f"no JBD characteristic pair advertised: {sorted(available)}")

    def _on_notify(self, _sender: int, data: bytearray) -> None:
        self._buffer.extend(data)
        frames, _ = extract_frames(bytes(self._buffer))
        if frames and self._response is not None and not self._response.done():
            self._response.set_result(frames[0])

    async def _ensure_connected(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        await self.close()
        logger.info("Connecting to %s on %s", self._mac, self._adapter)
        # Connect by address first: works without an advertisement, which
        # matters because this BMS stops advertising while a (possibly
        # stale, e.g. after an unclean shutdown) link is up -- bluez just
        # reuses the existing bearer.
        client = BleakClient(self._mac, timeout=self._connect_timeout_s)
        try:
            await client.connect()
        except BleakError as exc:
            # Device unknown to bluez (cold start): discover it, then connect.
            logger.info("Direct connect failed (%s); scanning for %s", exc, self._mac)
            device = await BleakScanner.find_device_by_address(
                self._mac, timeout=self._connect_timeout_s, adapter=self._adapter
            )
            if device is None:
                raise BleakError(f"device {self._mac} not found while scanning") from exc
            client = BleakClient(device, timeout=self._connect_timeout_s)
            await client.connect()
        self._client = client
        if self._variant == "auto":
            self._tx_uuid, self._rx_uuid = self._detect_uuids(client)
        else:
            self._tx_uuid, self._rx_uuid = UUIDS[self._variant]
        self._buffer.clear()
        await client.start_notify(self._rx_uuid, self._on_notify)
        await asyncio.sleep(0.2)  # let the subscription settle before first write

    async def _request(self, reg: int) -> bytes:
        """Send one read command and return the validated response payload."""
        assert self._client is not None and self._tx_uuid is not None
        self._buffer.clear()
        self._response = asyncio.get_running_loop().create_future()
        await self._client.write_gatt_char(self._tx_uuid, build_read_command(reg), response=False)
        frame = await asyncio.wait_for(self._response, timeout=RESPONSE_TIMEOUT_S)
        return validate_frame(frame, expect_reg=reg)

    async def read(self) -> BatteryReading:
        """One poll: request 0x03 (general), then 0x04 (cell voltages).

        0x03 failing is a failed poll (BleError). 0x04 failing only drops the
        cell data for this poll: the pack info is still good, and a silent
        cell register publishes no cells (hub ADR 0022).
        """
        try:
            await self._ensure_connected()
            reading = decode_general(await self._request(REG_GENERAL))
        except (TimeoutError, BleakError, ValueError, AssertionError) as exc:
            # ValueError: a desynced notification stream yields a frame that
            # fails validation -- a data error, not a link error, but the
            # safe response is the same: drop the link (resets the buffer)
            # and let the poll loop retry (see tests/test_ble.py).
            await self.close()
            raise BleError(f"read from {self._mac} failed: {exc}") from exc
        try:
            cells = decode_cells(await self._request(REG_CELLS), reading.cell_count)
            if cells is None:
                logger.warning("cell payload does not match cell_count %d", reading.cell_count)
        except (TimeoutError, ValueError) as exc:
            logger.warning("cell voltage read failed (%s); publishing no cells this poll", exc)
            cells = None
        except BleakError as exc:
            await self.close()
            raise BleError(f"read from {self._mac} failed: {exc}") from exc
        return replace(reading, cell_voltages_v=cells)

    async def close(self) -> None:
        client, self._client = self._client, None
        self._tx_uuid = self._rx_uuid = None
        if client is not None:
            try:
                await client.disconnect()
            except BleakError:
                logger.debug("disconnect error (ignored)", exc_info=True)
