"""USB discovery and transport for Elmü CANable 2.5 adapters.

Both the CanableDevBoard FDCAN and the reference FYSETC adapter use VID/PID
``1d50:606f``, so device selection must be based on serial number or other USB
attributes, never on VID/PID alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

import usb.core

from .exceptions import (
    CanableInitializationError,
    CanableOperationError,
    DeviceNotFoundError,
    FeedbackError,
)
from .protocol import (
    MAX_BLOB_SIZE,
    PID,
    REQUEST_IN,
    REQUEST_OUT,
    VID,
    Feedback,
    Request,
)

__all__ = [
    "UsbDeviceInfo",
    "DeviceSelector",
    "format_channel",
    "parse_channel",
    "list_devices",
    "detect_available_configs",
    "select_device",
    "CanableUsbDevice",
]

LOG = logging.getLogger(__name__)

# Channel 0 is the only channel supported by this driver. The remaining entries
# encode the firmware's reserved multi-channel endpoint mapping.
_INTERFACES = (0, 2, 3)
_ENDPOINTS = ((0x81, 0x02), (0x83, 0x04), (0x85, 0x06))


@dataclass(frozen=True)
class UsbDeviceInfo:
    """Descriptive metadata for one USB device.

    Attributes:
        device: The underlying ``usb.core.Device`` object.
        vid: USB vendor ID.
        pid: USB product ID.
        serial_number: USB serial number, when reported by the device.
        product: USB product string, when reported by the device.
        manufacturer: USB manufacturer string, when reported by the device.
        bus: USB bus number.
        address: USB device address on the bus.
        port_path: Physical USB port path, when available.
    """

    device: Any
    vid: int
    pid: int
    serial_number: Optional[str]
    product: Optional[str]
    manufacturer: Optional[str]
    bus: Optional[int]
    address: Optional[int]
    port_path: Optional[str]

    @property
    def identity(self) -> str:
        """Return a short human-readable device identifier."""
        if self.serial_number:
            return f"serial {self.serial_number}"
        return f"bus {self.bus} address {self.address}"


@dataclass(frozen=True)
class DeviceSelector:
    """Criteria used to choose one adapter from the USB bus.

    Attributes:
        serial: Exact serial number to match.
        product: Case-insensitive substring to match in the product string.
        manufacturer: Case-insensitive substring to match in the manufacturer string.
        vid: USB vendor ID to match.
        pid: USB product ID to match.
        bus: USB bus number to match.
        address: USB device address to match.
        index: CAN channel index to open.
    """

    serial: Optional[str] = None
    product: Optional[str] = None
    manufacturer: Optional[str] = None
    vid: int = VID
    pid: int = PID
    bus: Optional[int] = None
    address: Optional[int] = None
    index: int = 0

    def matches(self, info: UsbDeviceInfo) -> bool:
        """Return whether a device satisfies every configured criterion.

        Args:
            info: Device metadata to test.

        Returns:
            ``True`` when all criteria match, otherwise ``False``.
        """
        if info.vid != self.vid or info.pid != self.pid:
            return False
        if self.serial is not None:
            if not info.serial_number or self.serial.casefold() != info.serial_number.casefold():
                return False
        if self.product is not None:
            if not info.product or self.product.casefold() not in info.product.casefold():
                return False
        if self.manufacturer is not None:
            if (
                not info.manufacturer
                or self.manufacturer.casefold() not in info.manufacturer.casefold()
            ):
                return False
        if self.bus is not None and _as_int(info.bus) != self.bus:
            return False
        if self.address is not None and _as_int(info.address) != self.address:
            return False
        return True

    def describe(self) -> str:
        """Return a human-readable description of the selection criteria."""
        parts = [f"vid=0x{self.vid:04X}", f"pid=0x{self.pid:04X}"]
        for name in ("serial", "product", "manufacturer", "bus", "address"):
            value = getattr(self, name)
            if value is not None:
                parts.append(f"{name}={value}")
        return ", ".join(parts)


def _as_int(value: Any) -> Optional[int]:
    """Convert a USB bus/address value to an integer when possible."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_string(device: Any, name: str) -> Optional[str]:
    """Read an optional string descriptor, tolerating unsupported devices."""
    try:
        value = getattr(device, name)
    except (AttributeError, NotImplementedError, ValueError, usb.core.USBError):
        return None
    if value is None:
        return None
    return str(value)


def _safe_int(device: Any, name: str) -> Optional[int]:
    """Read an optional integer attribute, tolerating unsupported devices."""
    try:
        value = getattr(device, name)
    except (AttributeError, NotImplementedError, ValueError, usb.core.USBError):
        return None
    return _as_int(value)


def _port_path(device: Any) -> Optional[str]:
    """Build the physical USB port path from ``port_numbers`` or parents."""
    try:
        ports = tuple(getattr(device, "port_numbers") or ())
    except (AttributeError, NotImplementedError, ValueError, usb.core.USBError):
        ports = ()
    if not ports:
        try:
            parent = getattr(device, "parent", None)
        except (AttributeError, NotImplementedError, ValueError, usb.core.USBError):
            parent = None
        reversed_ports: List[int] = []
        while parent is not None:
            try:
                numbers = tuple(getattr(parent, "port_numbers") or ())
            except (AttributeError, NotImplementedError, ValueError, usb.core.USBError):
                numbers = ()
            if numbers:
                reversed_ports.insert(0, int(numbers[-1]))
            try:
                parent = getattr(parent, "parent", None)
            except (AttributeError, NotImplementedError, ValueError, usb.core.USBError):
                parent = None
        ports = tuple(reversed_ports)
    return ".".join(str(int(port)) for port in ports) if ports else None


def _make_info(device: Any) -> UsbDeviceInfo:
    """Build :class:`UsbDeviceInfo` from a raw USB device object."""
    return UsbDeviceInfo(
        device=device,
        vid=_safe_int(device, "idVendor") or VID,
        pid=_safe_int(device, "idProduct") or PID,
        serial_number=_safe_string(device, "serial_number"),
        product=_safe_string(device, "product"),
        manufacturer=_safe_string(device, "manufacturer"),
        bus=_safe_int(device, "bus"),
        address=_safe_int(device, "address"),
        port_path=_port_path(device),
    )


def format_channel(serial: str, index: int = 0) -> str:
    """Format a channel string in ``SERIAL:INDEX`` form.

    Args:
        serial: Device serial number.
        index: CAN channel index.

    Returns:
        The formatted channel string.

    Raises:
        ValueError: If the serial is empty or the index is negative.
    """
    if not serial:
        raise ValueError("a serial number is required to format a channel")
    if index < 0:
        raise ValueError("channel index must not be negative")
    return f"{serial}:{index}"


def parse_channel(channel: Any) -> Tuple[Optional[str], int]:
    """Parse a channel string into serial number and channel index.

    Args:
        channel: ``SERIAL:INDEX`` string, plain serial string, or ``None``.

    Returns:
        A ``(serial, index)`` tuple. The serial is ``None`` when no channel was
        supplied; a plain serial implies index 0.

    Raises:
        ValueError: If the index is not a non-negative integer.
    """
    if channel is None:
        return None, 0
    text = str(channel).strip()
    if not text:
        return None, 0
    if ":" in text:
        serial, _, suffix = text.rpartition(":")
        if not serial:
            raise ValueError(f"invalid channel {channel!r}: empty serial")
        index = _as_int(suffix)
        if index is None or index < 0:
            raise ValueError(f"invalid channel {channel!r}: bad index")
        return serial, index
    return text, 0


def list_devices(
    backend: Any = None, vid: int = VID, pid: int = PID
) -> List[UsbDeviceInfo]:
    """List connected CANable 2.5 devices.

    Args:
        backend: Optional pyusb backend override, useful for unit tests.
        vid: Vendor ID to search for.
        pid: Product ID to search for.

    Returns:
        A list of device metadata records.

    Raises:
        CanableInitializationError: If USB enumeration fails.
    """
    kwargs: dict[str, Any] = {
        "find_all": True,
        "idVendor": vid,
        "idProduct": pid,
    }
    if backend is not None:
        kwargs["backend"] = backend
    try:
        found = usb.core.find(**kwargs)
    except Exception as exc:  # pyusb backends raise several error types here
        raise CanableInitializationError(f"USB enumeration failed: {exc}") from exc
    if found is None:
        return []
    if isinstance(found, usb.core.Device):
        found = [found]
    return [_make_info(device) for device in found]


def detect_available_configs() -> List[dict[str, Any]]:
    """Return python-can configurations for all connected CANable devices.

    Returns:
        A list of configuration dictionaries with ``interface`` and
        ``channel`` keys.
    """
    configs: List[dict[str, Any]] = []
    for info in list_devices():
        serial = info.serial_number or f"usb{info.bus or 0}_{info.address or 0}"
        configs.append(
            {
                "interface": "canable",
                "channel": format_channel(serial, 0),
                "serial_number": info.serial_number,
                "product": info.product,
            }
        )
    return configs


def select_device(
    selector: DeviceSelector,
    devices: Optional[Sequence[UsbDeviceInfo]] = None,
    backend: Any = None,
) -> UsbDeviceInfo:
    """Select exactly one device using the supplied criteria.

    Args:
        selector: Selection criteria.
        devices: Optional pre-enumerated device list.
        backend: Optional pyusb backend override.

    Returns:
        The single matching device metadata record.

    Raises:
        DeviceNotFoundError: If no device matches or the match is ambiguous.
    """
    if selector.index > 0:
        raise DeviceNotFoundError("multi-channel operation is not supported")
    available = list(devices) if devices is not None else list_devices(backend=backend)
    matches = [info for info in available if selector.matches(info)]
    if not matches:
        raise DeviceNotFoundError(
            f"no CANable device matches {selector.describe()}; "
            f"found {[info.identity for info in available]}"
        )
    if len(matches) > 1:
        raise DeviceNotFoundError(
            f"multiple CANable devices match {selector.describe()}; "
            "select by serial, bus, or address"
        )
    return matches[0]


class CanableUsbDevice:
    """Open USB transport for one CANable channel.

    Args:
        info: Device metadata returned by :func:`list_devices`.
        channel: CAN channel index. Only index 0 is supported.
        timeout_ms: USB transfer timeout in milliseconds.

    Raises:
        CanableInitializationError: If the channel index is unsupported.
    """

    def __init__(
        self,
        info: UsbDeviceInfo,
        channel: int = 0,
        timeout_ms: int = 1000,
    ) -> None:
        """Initialize the transport without opening the USB device."""
        if channel < 0 or channel >= len(_INTERFACES):
            raise CanableInitializationError(f"unsupported channel index {channel}")
        self.info = info
        self.channel = channel
        self.interface = _INTERFACES[channel]
        self.endpoint_in, self.endpoint_out = _ENDPOINTS[channel]
        self.timeout_ms = timeout_ms
        self._is_open = False
        self._kernel_detached = False
        self.reattached_kernel_driver = False

    @property
    def is_open(self) -> bool:
        """Return whether the interface is currently claimed."""
        return self._is_open

    @property
    def kernel_driver_detached(self) -> bool:
        """Return whether this transport currently owns the kernel driver slot."""
        return self._kernel_detached

    @property
    def channel_info(self) -> str:
        """Return a descriptive channel string."""
        return f"canable:{self.info.identity} channel {self.channel}"

    def open(self) -> None:
        """Configure the device, detach ``gs_usb``, and claim the interface.

        Returns:
            ``None``.

        Raises:
            CanableInitializationError: If the device cannot be opened.
        """
        if self._is_open:
            return
        device = self.info.device
        try:
            if device.get_active_configuration() is None:
                device.set_configuration()
        except (AttributeError, NotImplementedError):
            pass
        except usb.core.USBError as exc:
            raise CanableInitializationError(
                f"cannot configure {self.info.identity}: {exc}"
            ) from exc

        try:
            active = bool(device.is_kernel_driver_active(self.interface))
        except (AttributeError, NotImplementedError):
            active = False
        except usb.core.USBError as exc:
            raise CanableInitializationError(
                f"cannot query kernel driver for {self.info.identity}: {exc}"
            ) from exc
        if active:
            try:
                device.detach_kernel_driver(self.interface)
            except (AttributeError, NotImplementedError):
                LOG.debug("kernel driver detach is unsupported on this backend")
            except usb.core.USBError as exc:
                raise CanableInitializationError(
                    f"cannot detach kernel driver from {self.info.identity}: {exc}"
                ) from exc
            else:
                self._kernel_detached = True

        try:
            device.claim_interface(self.interface)
        except (AttributeError, NotImplementedError):
            pass
        except usb.core.USBError as exc:
            raise CanableInitializationError(
                f"cannot claim interface {self.interface} on {self.info.identity}: {exc}"
            ) from exc
        self._is_open = True

    def ctrl_in(self, request: Request, length: int, value: int = 0) -> bytes:
        """Issue a vendor IN control transfer.

        Args:
            request: Control request to issue.
            length: Number of bytes to read.
            value: ``wValue`` field; the channel index by default.

        Returns:
            Bytes returned by the device.

        Raises:
            CanableOperationError: If the transfer fails.
        """
        self._ensure_open()
        try:
            data = self.info.device.ctrl_transfer(
                REQUEST_IN, int(request), value & 0xFFFF, 0, length, self.timeout_ms
            )
        except usb.core.USBError as exc:
            raise CanableOperationError(f"{request.name} control IN failed: {exc}") from exc
        return bytes(data)

    def ctrl_out(self, request: Request, data: bytes = b"", value: int = 0) -> None:
        """Issue a vendor OUT control transfer and verify firmware feedback.

        Args:
            request: Control request to issue.
            data: Payload bytes.
            value: ``wValue`` field; the channel index by default.

        Returns:
            ``None``.

        Raises:
            CanableOperationError: If the transfer fails.
            FeedbackError: If firmware reports a non-success feedback code.
        """
        self._ensure_open()
        try:
            self.info.device.ctrl_transfer(
                REQUEST_OUT,
                int(request),
                value & 0xFFFF,
                0,
                bytes(data),
                self.timeout_ms,
            )
        except usb.core.USBError as exc:
            raise CanableOperationError(f"{request.name} control OUT failed: {exc}") from exc

        raw = self.ctrl_in(Request.GET_LAST_ERROR, 1)
        feedback = raw[0] if raw else 0
        if feedback != int(Feedback.SUCCESS):
            raise FeedbackError(feedback, request.name)

    def read(self, size: int = MAX_BLOB_SIZE, timeout_ms: Optional[int] = None) -> bytes:
        """Read from the bulk IN endpoint.

        Args:
            size: Maximum number of bytes to read.
            timeout_ms: Optional per-call timeout override in milliseconds.

        Returns:
            Received bytes, or an empty ``bytes`` object on USB timeout.

        Raises:
            CanableOperationError: If a non-timeout USB error occurs.
        """
        self._ensure_open()
        timeout = self.timeout_ms if timeout_ms is None else timeout_ms
        try:
            return bytes(self.info.device.read(self.endpoint_in, size, max(1, timeout)))
        except usb.core.USBTimeoutError:
            return b""
        except usb.core.USBError as exc:
            raise CanableOperationError(f"bulk IN failed: {exc}") from exc

    def write(self, data: bytes) -> None:
        """Write a complete message to the bulk OUT endpoint.

        Args:
            data: Message bytes to transmit.

        Returns:
            ``None``.

        Raises:
            ValueError: If the payload exceeds the firmware blob limit.
            CanableOperationError: If the transfer is short or fails.
        """
        self._ensure_open()
        payload = bytes(data)
        if len(payload) > MAX_BLOB_SIZE:
            raise ValueError(f"USB message of {len(payload)} bytes exceeds {MAX_BLOB_SIZE}")
        try:
            written = self.info.device.write(self.endpoint_out, payload, self.timeout_ms)
        except usb.core.USBError as exc:
            raise CanableOperationError(f"bulk OUT failed: {exc}") from exc
        if written != len(payload):
            raise CanableOperationError(
                f"short bulk OUT transfer: wrote {written} of {len(payload)} bytes"
            )

    def close(self) -> None:
        """Release the interface and reattach the original kernel driver.

        Returns:
            ``None``.
        """
        if not self._is_open:
            return
        device = self.info.device
        try:
            device.release_interface(self.interface)
        except (AttributeError, NotImplementedError, ValueError, usb.core.USBError) as exc:
            LOG.debug("could not release interface %d: %s", self.interface, exc)
        if self._kernel_detached:
            try:
                device.attach_kernel_driver(self.interface)
            except (AttributeError, NotImplementedError, ValueError, usb.core.USBError) as exc:
                LOG.warning("could not reattach kernel driver: %s", exc)
            else:
                self.reattached_kernel_driver = True
            self._kernel_detached = False
        self._is_open = False

    def _ensure_open(self) -> None:
        """Raise when the USB interface has not been claimed."""
        if not self._is_open:
            raise CanableOperationError("USB interface is not open")

    def __enter__(self) -> "CanableUsbDevice":
        """Open the device on context entry."""
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        """Close the device on context exit."""
        self.close()
