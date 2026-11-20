"""Clean shutdown tests through the ``canable`` plugin."""

from __future__ import annotations

from typing import Callable

import can
import pytest

pytestmark = pytest.mark.hardware


def test_shutdown_is_idempotent(
    canable_bus_factory: Callable[..., can.BusABC],
) -> None:
    """Repeated shutdown is safe and restores the kernel driver state."""
    bus = canable_bus_factory()
    bus.shutdown()
    assert bus.kernel_driver_restored
    bus.shutdown()
