"""Adapter-to-adapter tests using the registered reference adapters."""

from __future__ import annotations

from typing import Callable

import can
import pytest

from conftest import (
    CLASSIC_RATE_PARAMS,
    REFERENCE_PARAMS,
    ReferenceAdapter,
    close_bus,
    transfer,
)

pytestmark = pytest.mark.hardware


@pytest.mark.parametrize(("adapter", "rate"), CLASSIC_RATE_PARAMS)
def test_all_popular_bitrates(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    timeout: float,
    adapter: ReferenceAdapter,
    rate: int,
) -> None:
    """Frames pass in both directions at every popular classic bitrate."""
    target = canable_bus_factory(bitrate=rate)
    reference = reference_bus_factory(adapter, bitrate=rate)
    base_id = 0x700 + adapter.classic_bitrates.index(rate)
    try:
        transfer(
            target,
            reference,
            can.Message(
                arbitration_id=base_id,
                data=b"to-ref",
                is_extended_id=False,
            ),
            timeout,
        )
        transfer(
            reference,
            target,
            can.Message(
                arbitration_id=base_id + 1,
                data=b"to-can",
                is_extended_id=False,
            ),
            timeout,
        )
        assert target.error_frame_count == 0
    finally:
        close_bus(reference)


@pytest.mark.parametrize("adapter", REFERENCE_PARAMS)
def test_reference_both_directions(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    timeout: float,
    adapter: ReferenceAdapter,
) -> None:
    """Frames pass in both directions with no target-side error reports."""
    target = canable_bus_factory()
    reference = reference_bus_factory(adapter)
    try:
        target_to_reference = can.Message(
            arbitration_id=0x301,
            data=b"to-ref",
            is_extended_id=False,
        )
        transfer(target, reference, target_to_reference, timeout)

        reference_to_target = can.Message(
            arbitration_id=0x302,
            data=b"to-can",
            is_extended_id=False,
        )
        transfer(reference, target, reference_to_target, timeout)
        assert target.error_frame_count == 0
    finally:
        close_bus(reference)


@pytest.mark.stress
@pytest.mark.parametrize("adapter", REFERENCE_PARAMS)
def test_reference_alternating_stress(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    timeout: float,
    adapter: ReferenceAdapter,
) -> None:
    """Twenty alternating frames use unique IDs and preserve every payload."""
    target = canable_bus_factory()
    reference = reference_bus_factory(adapter)
    try:
        for index in range(20):
            sender, receiver = (
                (target, reference) if index % 2 == 0 else (reference, target)
            )
            message = can.Message(
                arbitration_id=0x400 + index,
                data=bytes([index, 0xA5, 0x5A, index ^ 0xFF]),
                is_extended_id=False,
            )
            transfer(sender, receiver, message, timeout)
        assert target.error_frame_count == 0
    finally:
        close_bus(reference)
