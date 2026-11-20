"""List CANable adapters discovered by the ``canable`` python-can plugin.

Usage::

    python examples/list_devices.py
"""

from __future__ import annotations

import can


def main() -> None:
    """Print one configuration per connected CANable adapter."""
    configs = can.detect_available_configs("canable", timeout=1.0)
    if not configs:
        print("No CANable adapters found. Check USB access and the udev rule.")
        return
    for config in configs:
        print(f"interface={config['interface']} channel={config['channel']}")


if __name__ == "__main__":
    main()
