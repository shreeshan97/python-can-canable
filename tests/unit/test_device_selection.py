"""Unit tests for USB discovery and serial-based device selection."""

from __future__ import annotations

import pytest

from canable.device import (
    CanableUsbDevice,
    DeviceSelector,
    format_channel,
    list_devices,
    parse_channel,
    select_device,
)
from canable.exceptions import DeviceNotFoundError
from canable.protocol import PID, VID


def test_parse_and_format_channel() -> None:
    """Channel strings accept ``SERIAL:INDEX`` and plain serial forms."""
    assert parse_channel(None) == (None, 0)
    assert parse_channel("209D3889594550052") == ("209D3889594550052", 0)
    assert parse_channel("209D3889594550052:2") == ("209D3889594550052", 2)
    assert format_channel("209D3889594550052", 0) == "209D3889594550052:0"
    with pytest.raises(ValueError):
        parse_channel("209D3889594550052:bad")
    with pytest.raises(ValueError):
        parse_channel("209D3889594550052:-1")
    with pytest.raises(ValueError):
        format_channel("")


def test_list_devices_uses_fake_usb_backend(usb_bus: object) -> None:
    """Discovery returns descriptors from the injected USB backend."""
    fake = usb_bus.add(serial="AAA", product="CanableDevBoard FDCAN CAN adapter")
    devices = list_devices()
    assert len(devices) == 1
    assert devices[0].device is fake
    assert devices[0].serial_number == "AAA"
    assert devices[0].port_path == "1.2"
    assert devices[0].vid == VID
    assert devices[0].pid == PID


def test_select_device_by_serial(usb_bus: object) -> None:
    """Serial selection chooses the matching adapter among several."""
    usb_bus.add(serial="TARGET", product="CanableDevBoard FDCAN CAN adapter")
    usb_bus.add(serial="REFERENCE", product="FYSETC UCAN", manufacturer="FYSETC")
    selected = select_device(DeviceSelector(serial="target"))
    assert selected.serial_number == "TARGET"


def test_select_device_by_other_selectors(usb_bus: object) -> None:
    """Product, manufacturer, bus, and address selectors are honored."""
    usb_bus.add(serial="ONE", product="CanableDevBoard FDCAN", manufacturer="ElmueSoft", bus=1, address=4)
    usb_bus.add(serial="TWO", product="FYSETC UCAN", manufacturer="FYSETC", bus=1, address=5)
    assert select_device(DeviceSelector(product="canabledevboard")).serial_number == "ONE"
    assert select_device(DeviceSelector(manufacturer="fysetc")).serial_number == "TWO"
    assert select_device(DeviceSelector(bus=1, address=5)).serial_number == "TWO"


def test_select_device_errors(usb_bus: object) -> None:
    """Missing, ambiguous, and multi-channel selections raise clear errors."""
    with pytest.raises(DeviceNotFoundError):
        select_device(DeviceSelector(serial="MISSING"))
    usb_bus.add(serial="A")
    usb_bus.add(serial="B")
    with pytest.raises(DeviceNotFoundError, match="multiple"):
        select_device(DeviceSelector())
    with pytest.raises(DeviceNotFoundError, match="multi-channel"):
        select_device(DeviceSelector(serial="A", index=1))


def test_usb_device_open_close_lifecycle(usb_bus: object) -> None:
    """Opening detaches gs_usb and closing reattaches it exactly once."""
    fake = usb_bus.add()
    info = list_devices()[0]
    transport = CanableUsbDevice(info, channel=0, timeout_ms=250)
    assert not transport.is_open
    assert transport.endpoint_in == 0x81
    assert transport.endpoint_out == 0x02
    assert transport.interface == 0

    transport.open()
    transport.open()  # idempotent
    assert transport.is_open
    assert fake.detached
    assert fake.claimed
    transport.close()
    transport.close()  # idempotent
    assert fake.released
    assert fake.reattached
    assert transport.reattached_kernel_driver
    assert not transport.kernel_driver_detached
    assert not transport.is_open

    with pytest.raises(Exception):
        transport.read()
