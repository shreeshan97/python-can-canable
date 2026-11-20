"""List CANable adapters discovered by the ``canable`` python-can plugin.

Usage::

    python examples/list_devices.py
"""

from __future__ import annotations

import sys

import can


def _usb_hint() -> str:
    """Return a short platform-specific USB diagnostic hint.

    Returns:
        Text describing the first host setup item to check on this platform.
    """
    if sys.platform.startswith("linux"):
        return "on Linux, install udev/99-canable.rules and replug"
    if sys.platform == "win32":
        return (
            "on Windows, confirm automatic WinUSB installation and a libusb 1.0 "
            "runtime for PyUSB"
        )
    return "check the host USB driver/backend and raw USB access"


def main() -> None:
    """Print one configuration per connected CANable adapter."""
    configs = can.detect_available_configs("canable", timeout=1.0)
    if not configs:
        print(f"No CANable adapters found; {_usb_hint()}.")
        return
    for config in configs:
        print(f"interface={config['interface']} channel={config['channel']}")


if __name__ == "__main__":
    main()
