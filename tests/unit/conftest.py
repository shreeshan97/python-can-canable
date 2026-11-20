"""Shared fakes for the hardware-free unit test suite."""

from __future__ import annotations

import struct
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
import usb.core

from canable.protocol import (
    BOARD_INFO_FORMAT,
    CAPABILITY_CLASSIC_FORMAT,
    CAPABILITY_FD_FORMAT,
    DEVICE_VERSION_FORMAT,
    ERROR_STATE_FORMAT,
    HOST_FORMAT_MAGIC,
    PID,
    REQUEST_IN,
    REQUEST_OUT,
    VID,
    BoardFlag,
    DeviceFlags,
    Feedback,
    Request,
)


def timing_limits_tuple(
    seg1_min: int = 1,
    seg1_max: int = 128,
    seg2_min: int = 1,
    seg2_max: int = 32,
    sjw_max: int = 4,
    brp_min: int = 1,
    brp_max: int = 512,
    brp_inc: int = 1,
) -> tuple[int, ...]:
    """Build a ``kTimeMinMax`` field tuple for test capability buffers."""
    return (
        seg1_min,
        seg1_max,
        seg2_min,
        seg2_max,
        sjw_max,
        brp_min,
        brp_max,
        brp_inc,
    )


def capability_bytes(
    clock: int = 160_000_000,
    feature: int = int(DeviceFlags.PROTOCOL_ELMUE),
    limits: Optional[tuple[int, ...]] = None,
) -> bytes:
    """Build one ``kCapabilityClassic``-shaped test buffer."""
    values = timing_limits_tuple() if limits is None else limits
    return struct.pack(CAPABILITY_CLASSIC_FORMAT, feature, clock, *values)


def capability_fd_bytes(
    clock: int = 160_000_000,
    feature: int = int(DeviceFlags.PROTOCOL_ELMUE | DeviceFlags.CAN_FD | DeviceFlags.BIT_TIMING_FD),
) -> bytes:
    """Build a 72-byte ``kCapabilityFD`` test buffer."""
    values = timing_limits_tuple()
    return struct.pack(CAPABILITY_FD_FORMAT, feature, clock, *values, *values)


def device_version_bytes() -> bytes:
    """Build a valid ``kDeviceVersion`` test buffer."""
    return struct.pack(DEVICE_VERSION_FORMAT, 1, 2, 3, 0, 0x0250, 0x0100)


def board_info_bytes(board_name: str = "CanableDevBoard FDCAN") -> bytes:
    """Build a valid ``kBoardInfo`` test buffer."""
    return struct.pack(
        BOARD_INFO_FORMAT,
        0x468,
        b"STM32G431xx",
        board_name.encode("ascii"),
        int(BoardFlag.QUARTZ_IN_USE),
    )


def error_state_bytes() -> bytes:
    """Build a valid ``kErrorState`` test buffer."""
    return struct.pack(ERROR_STATE_FORMAT, 0, 0, 0)


class FakeUsbDevice:
    """Scriptable stand-in for ``usb.core.Device``."""

    def __init__(
        self,
        serial: str = "209D3889594550052",
        product: str = "CanableDevBoard FDCAN CAN adapter",
        manufacturer: str = "ElmueSoft (netcult.ch/elmue)",
        bus: int = 1,
        address: int = 4,
        port_numbers: tuple[int, ...] = (1, 2),
    ) -> None:
        """Initialize descriptors, scripted responses, and call logs."""
        self.idVendor = VID
        self.idProduct = PID
        self.serial_number = serial
        self.product = product
        self.manufacturer = manufacturer
        self.bus = bus
        self.address = address
        self.port_numbers = port_numbers

        self.ctrl_calls: List[tuple[Any, ...]] = []
        self.bulk_writes: List[bytes] = []
        self.read_calls: List[tuple[int, int, int]] = []
        self.responses: Dict[Request, bytes] = {
            Request.GET_DEVICE_VERSION: device_version_bytes(),
            Request.GET_CAPABILITIES: capability_bytes(),
            Request.GET_CAPABILITIES_FD: capability_fd_bytes(),
            Request.GET_BOARD_INFO: board_info_bytes(),
            Request.GET_ERROR_STATE: error_state_bytes(),
            Request.GET_PIN_STATUS: b"\x03\x00",
        }
        self.feedback = int(Feedback.SUCCESS)
        self.feedback_script: List[int] = []
        self.incoming = bytearray()
        self.kernel_active = True
        self.claimed = False
        self.released = False
        self.detached = False
        self.reattached = False
        self.configured = False

    def prepare_default_open(self) -> None:
        """Validate the host-format magic used during initialization."""
        self.expected_host_format = struct.pack("<I", HOST_FORMAT_MAGIC)

    def get_active_configuration(self) -> object:
        """Return a dummy active configuration."""
        return object()

    def set_configuration(self) -> None:
        """Record a configuration request."""
        self.configured = True

    def is_kernel_driver_active(self, interface: int) -> bool:
        """Report whether the kernel driver is active."""
        return self.kernel_active

    def detach_kernel_driver(self, interface: int) -> None:
        """Record a kernel driver detach."""
        self.detached = True
        self.kernel_active = False

    def attach_kernel_driver(self, interface: int) -> None:
        """Record a kernel driver reattach."""
        self.reattached = True
        self.kernel_active = True

    def claim_interface(self, interface: int) -> None:
        """Record an interface claim."""
        self.claimed = True

    def release_interface(self, interface: int) -> None:
        """Record an interface release."""
        self.released = True

    def ctrl_transfer(
        self,
        bm_request_type: int,
        request: int,
        value: int,
        index: int,
        data_or_length: Any,
        timeout: Optional[int] = None,
    ) -> Any:
        """Record a control transfer and return a scripted IN response."""
        self.ctrl_calls.append(
            (bm_request_type, request, value, index, data_or_length, timeout)
        )
        if bm_request_type == REQUEST_IN:
            length = int(data_or_length)
            if request == int(Request.GET_LAST_ERROR):
                if self.feedback_script:
                    feedback = self.feedback_script.pop(0)
                else:
                    feedback = self.feedback
                return bytes([feedback])
            return bytes(self.responses.get(Request(request), b"")[:length])
        if bm_request_type == REQUEST_OUT:
            return len(bytes(data_or_length))
        raise AssertionError(f"unexpected bmRequestType 0x{bm_request_type:02X}")

    def read(self, endpoint: int, size: int, timeout: int) -> bytes:
        """Return queued IN bytes or raise a USB timeout."""
        self.read_calls.append((endpoint, size, timeout))
        if not self.incoming:
            raise usb.core.USBTimeoutError("no queued IN data")
        chunk = bytes(self.incoming[:size])
        del self.incoming[:size]
        return chunk

    def write(self, endpoint: int, data: bytes, timeout: int) -> int:
        """Record an OUT transfer."""
        self.bulk_writes.append(bytes(data))
        return len(data)

    def queue_in(self, data: bytes) -> None:
        """Append bytes to the bulk IN stream."""
        self.incoming.extend(data)

    def out_requests(self) -> List[tuple[Request, bytes, int]]:
        """Return decoded OUT control requests for assertions."""
        return [
            (Request(request), bytes(data), value)
            for bm_request_type, request, value, _index, data, _timeout in self.ctrl_calls
            if bm_request_type == REQUEST_OUT
        ]


@pytest.fixture
def usb_bus(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Replace pyusb enumeration with a controllable fake device list."""
    devices: List[FakeUsbDevice] = []

    def fake_find(**_kwargs: Any) -> List[FakeUsbDevice]:
        return list(devices)

    monkeypatch.setattr(usb.core, "find", fake_find)

    def add_device(**kwargs: Any) -> FakeUsbDevice:
        device = FakeUsbDevice(**kwargs)
        devices.append(device)
        return device

    return SimpleNamespace(devices=devices, add=add_device)
