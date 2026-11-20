"""Runtime reconfiguration tests through the ``canable`` plugin."""

from __future__ import annotations

from typing import Callable

import can
import pytest

from canable.protocol import DeviceFlags
from conftest import transfer

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
    with reference_bus_factory() as reference:
        transfer(target, reference, _message(0x501, b"initial"), timeout)

    for new_bitrate, can_id in ((250_000, 0x502), (bitrate, 0x503)):
        target.set_bitrate(new_bitrate)
        with reference_bus_factory(bitrate=new_bitrate) as reference:
            transfer(reference, target, _message(can_id, b"rate"), timeout)

    assert target.error_frame_count == 0


def test_acceptance_filters(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    timeout: float,
) -> None:
    """Host pass filters accept the selected ID and block other IDs."""
    target = canable_bus_factory()
    with reference_bus_factory() as reference:
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
        transfer(reference, target, _message(0x124, b"unblocked"), timeout)


def test_fd_send_receive_if_supported(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    timeout: float,
) -> None:
    """CAN FD frames pass when both adapters support the requested timing."""
    try:
        target = canable_bus_factory(fd=True, data_bitrate=2_000_000)
    except can.CanError as exc:
        pytest.skip(f"CANable FD timing is unavailable: {exc}")

    nominal, data = target.get_capabilities_fd()
    if not (
        nominal.feature & int(DeviceFlags.CAN_FD)
        and data.feature & int(DeviceFlags.BIT_TIMING_FD)
    ):
        pytest.skip("firmware does not advertise CAN FD with data bit timing")

    reference = reference_bus_factory(fd=True, data_bitrate=2_000_000)
    message = can.Message(
        arbitration_id=0x600,
        data=bytes(range(16)),
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
    )
    transfer(target, reference, message, timeout)


def test_pin_status_read_only(
    canable_bus_factory: Callable[..., can.BusABC],
) -> None:
    """BOOT0 pin status is readable without changing option bytes."""
    bus = canable_bus_factory()
    status = bus.get_pin_status()
    assert int(status) in (0, 1, 2, 3)
