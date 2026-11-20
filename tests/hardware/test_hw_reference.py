"""Adapter-to-adapter tests using the candle plugin as the reference."""

from __future__ import annotations

from typing import Callable

import can
import pytest

from conftest import transfer

pytestmark = pytest.mark.hardware


def test_reference_both_directions(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    timeout: float,
) -> None:
    """Frames pass in both directions with no target-side error reports."""
    target = canable_bus_factory()
    reference = reference_bus_factory()

    target_to_reference = can.Message(
        arbitration_id=0x301,
        data=b"to-reference",
        is_extended_id=False,
    )
    transfer(target, reference, target_to_reference, timeout)

    reference_to_target = can.Message(
        arbitration_id=0x302,
        data=b"to-canable",
        is_extended_id=False,
    )
    transfer(reference, target, reference_to_target, timeout)
    assert target.error_frame_count == 0


@pytest.mark.stress
def test_reference_alternating_stress(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    timeout: float,
) -> None:
    """Twenty alternating frames use unique IDs and preserve every payload."""
    target = canable_bus_factory()
    reference = reference_bus_factory()

    for index in range(20):
        sender, receiver = (target, reference) if index % 2 == 0 else (reference, target)
        message = can.Message(
            arbitration_id=0x400 + index,
            data=bytes([index, 0xA5, 0x5A, index ^ 0xFF]),
            is_extended_id=False,
        )
        transfer(sender, receiver, message, timeout)

    assert target.error_frame_count == 0
