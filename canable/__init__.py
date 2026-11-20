"""python-can plugin for Elmü CANable 2.5 USB-CAN adapters.

The package exposes :class:`~canable.bus.CanableBus` as the ``canable``
python-can interface. Only the Elmü 2.5 USB protocol is supported.
"""

from .bus import CanableBus
from .device import detect_available_configs, list_devices

__version__ = "0.1.0"

__all__ = ["CanableBus", "detect_available_configs", "list_devices", "__version__"]
