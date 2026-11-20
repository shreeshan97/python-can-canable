"""Unit tests for python-can plugin discovery."""

from __future__ import annotations

from importlib import metadata

import can

from canable import CanableBus, detect_available_configs
from canable.device import format_channel


def test_entry_point_is_registered() -> None:
    """The installed distribution exposes the ``canable`` entry point."""
    try:
        entry_points = metadata.entry_points(group="can.interface")
    except TypeError:  # Python 3.9 compatibility
        entry_points = metadata.entry_points().get("can.interface", [])
    entry = next((ep for ep in entry_points if ep.name == "canable"), None)
    assert entry is not None
    assert entry.value == "canable.bus:CanableBus"
    assert can.interface._get_class_for_interface("canable") is CanableBus


def test_direct_detection_uses_serial_channel(usb_bus: object) -> None:
    """Direct detection returns channels in ``SERIAL:CHANNEL`` form."""
    usb_bus.add(serial="TARGET", product="CanableDevBoard FDCAN CAN adapter")
    configs = detect_available_configs()
    assert configs == [
        {
            "interface": "canable",
            "channel": format_channel("TARGET", 0),
            "serial_number": "TARGET",
            "product": "CanableDevBoard FDCAN CAN adapter",
        }
    ]


def test_python_can_detection_uses_class_hook(usb_bus: object) -> None:
    """``can.detect_available_configs`` calls the bus class method."""
    usb_bus.add(serial="TARGET")
    configs = can.detect_available_configs("canable", timeout=0.5)
    assert configs
    assert configs[0]["interface"] == "canable"
    assert configs[0]["channel"] == "TARGET:0"


def test_detection_without_serial_uses_usb_location(usb_bus: object) -> None:
    """Devices that deny string descriptors still get a stable fallback channel."""
    device = usb_bus.add(serial=None, bus=1, address=7)
    device.product = None
    configs = CanableBus._detect_available_configs()
    assert configs[0]["channel"] == "usb1_7:0"
