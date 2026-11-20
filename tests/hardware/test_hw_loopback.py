"""Classic CAN loopback tests through the ``canable`` plugin."""

from __future__ import annotations

from typing import Callable

import can
import pytest

from conftest import receive_matching

pytestmark = pytest.mark.hardware


@pytest.mark.parametrize(
    ("can_id", "data", "is_extended", "is_remote", "dlc"),
    [
        pytest.param(0x123, b"", False, False, 0, id="empty-11-bit"),
        pytest.param(
            0x321,
            b"\x11\x22\x33\x44\x55\x66\x77\x88",
            False,
            False,
            8,
            id="full-11-bit",
        ),
        pytest.param(0x1ABCDEF, b"ABCD", True, False, 4, id="29-bit"),
        pytest.param(0x124, b"", False, True, 8, id="remote-11-bit"),
    ],
)
def test_loopback_variants(
    canable_bus_factory: Callable[..., can.BusABC],
    timeout: float,
    can_id: int,
    data: bytes,
    is_extended: bool,
    is_remote: bool,
    dlc: int,
) -> None:
    """Sent frames are echoed back with matching ID, kind, and payload."""
    bus = canable_bus_factory(
        loop_back=True,
        receive_own_messages=True,
    )
    sent = can.Message(
        arbitration_id=can_id,
        data=data,
        dlc=dlc,
        is_extended_id=is_extended,
        is_remote_frame=is_remote,
        is_rx=False,
    )
    bus.send(sent)
    received = receive_matching(bus, sent, timeout)
    assert received is not None
    assert received.is_rx
    assert bus.error_frame_count == 0
