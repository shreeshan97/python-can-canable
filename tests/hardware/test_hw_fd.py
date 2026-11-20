"""CAN FD transfer tests for registered FD-capable reference adapters."""

from __future__ import annotations

from typing import Callable

import can
import pytest

from canable.protocol import DeviceFlags
from conftest import (
    FD_REFERENCE_ADAPTERS,
    FD_REFERENCE_PARAMS,
    ReferenceAdapter,
    close_bus,
    transfer,
)

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.fd,
    pytest.mark.skipif(
        not FD_REFERENCE_ADAPTERS,
        reason=(
            "no FD-capable reference adapter registered; set CANABLE_FD_SERIAL "
            "to enable these tests"
        ),
    ),
]


def _fd_message(can_id: int, payload: bytes) -> can.Message:
    """Build a standard-ID CAN FD frame with bitrate switching enabled."""
    return can.Message(
        arbitration_id=can_id,
        data=payload,
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
    )


@pytest.mark.parametrize("adapter", FD_REFERENCE_PARAMS)
@pytest.mark.parametrize("payload_length", [16, 64], ids=["16-byte", "64-byte"])
def test_fd_send_receive(
    canable_bus_factory: Callable[..., can.BusABC],
    reference_bus_factory: Callable[..., can.BusABC],
    timeout: float,
    adapter: ReferenceAdapter,
    payload_length: int,
) -> None:
    """FD frames pass in both directions with an FD-capable reference."""
    target = canable_bus_factory(
        fd=True,
        data_bitrate=adapter.fd_data_bitrate,
    )
    nominal, data = target.get_capabilities_fd()
    if not (
        nominal.feature & int(DeviceFlags.CAN_FD)
        and data.feature & int(DeviceFlags.BIT_TIMING_FD)
    ):
        pytest.skip("firmware does not advertise CAN FD with data bit timing")

    reference = reference_bus_factory(
        adapter,
        fd=True,
        data_bitrate=adapter.fd_data_bitrate,
    )
    try:
        transfer(
            target,
            reference,
            _fd_message(0x600, bytes(range(payload_length))),
            timeout,
        )
        transfer(
            reference,
            target,
            _fd_message(0x601, bytes(range(payload_length - 1, -1, -1))),
            timeout,
        )
        assert target.error_frame_count == 0
    finally:
        close_bus(reference)
