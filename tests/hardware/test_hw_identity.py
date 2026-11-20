"""Live-hardware tests for plugin discovery and firmware identity."""

from __future__ import annotations

from typing import Callable

import can
import pytest

from canable.protocol import DeviceFlags

pytestmark = pytest.mark.hardware


def test_detect_available_configs_lists_board(
    canable_serial: str, timeout: float
) -> None:
    """The board appears in ``can.detect_available_configs("canable")``."""
    configs = can.detect_available_configs("canable", timeout=min(timeout, 1.0))
    channels = [str(config.get("channel", "")) for config in configs]
    serials = [channel.split(":", 1)[0].casefold() for channel in channels]
    assert canable_serial.casefold() in serials
    assert any(config.get("interface") == "canable" for config in configs)


def test_firmware_identity(canable_bus_factory: Callable[..., can.BusABC]) -> None:
    """Firmware reports the expected board, flags, and 160 MHz clock."""
    bus = canable_bus_factory()
    board = bus.get_board_info()
    version = bus.get_device_version()
    classic = bus.get_capabilities()
    nominal, data = bus.get_capabilities_fd()

    assert board.board_name.casefold().startswith("canabledevboard")
    assert version.icount >= 0
    assert classic.feature & int(DeviceFlags.PROTOCOL_ELMUE)
    assert classic.fclk_can == 160_000_000
    assert nominal.fclk_can == 160_000_000
    assert data.fclk_can == 160_000_000
