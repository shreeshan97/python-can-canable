"""Runtime reconfiguration tests through the ``canable`` plugin."""

from __future__ import annotations

from typing import Callable

import can
import pytest

from conftest import close_bus, transfer

pytestmark = pytest.mark.hardware


def _message(can_id: int, payload: bytes) -> can.Message:
    """Build a standard-ID classic frame with the given ID and payload."""
    return can.Message(
        arbitration_id=can_id,
        data=payload,
        is_extended_id=False,
    )


def test_runtime_bitrate_changes(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    bitrate: int,
    timeout: float,
) -> None:
    """Bitrate changes at runtime keep both adapters in sync."""
    target = canable_bus_factory()
    reference = reference_bus_factory()
    try:
        transfer(target, reference, _message(0x501, b"initial"), timeout)
    finally:
        close_bus(reference)

    for new_bitrate, can_id in ((250_000, 0x502), (bitrate, 0x503)):
        target.set_bitrate(new_bitrate)
        reference = reference_bus_factory(bitrate=new_bitrate)
        try:
            transfer(reference, target, _message(can_id, b"rate"), timeout)
        finally:
            close_bus(reference)

    assert target.error_frame_count == 0


def test_acceptance_filters(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    timeout: float,
) -> None:
    """Host pass filters accept the selected ID and block other IDs."""
    target = canable_bus_factory()
    reference = reference_bus_factory()
    try:
        target.set_filters([{"can_id": 0x123, "can_mask": 0x7FF, "extended": False}])
        transfer(reference, target, _message(0x123, b"pass"), timeout)
        transfer(
            reference,
            target,
            _message(0x124, b"blocked"),
            timeout / 2,
            expect_success=False,
        )
        target.clear_filters()
        transfer(reference, target, _message(0x124, b"unblock"), timeout)
    finally:
        close_bus(reference)


def test_pin_status_read_only(
    canable_bus_factory: Callable[..., can.BusABC],
) -> None:
    """BOOT0 pin status is readable without changing option bytes."""
    bus = canable_bus_factory()
    status = bus.get_pin_status()
    assert int(status) in (0, 1, 2, 3)
