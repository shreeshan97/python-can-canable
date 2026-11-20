"""Fixtures and helpers for opt-in live-hardware tests.

The target adapter is always opened through the python-can plugin interface
(``can.Bus(interface="canable", ...)``). Reference adapters are described by
:data:`REFERENCE_ADAPTERS`; the FYSETC UCAN is the configured classic reference.
FD-capable references can be added through ``CANABLE_FD_SERIAL`` or by extending
the registry.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import can
import pytest

DEFAULT_CANABLE_SERIAL = "209D3889594550052"
DEFAULT_CANDLE_SERIAL = "003500465446571320343032"

# CiA-recommended classic CAN bitrates. The FYSETC candle reference has a
# 48 MHz clock, so python-can-candle's BRP <= 64 helper cannot express 10/20/50
# kbit/s; the adapter-specific override below supplies equivalent timings.
POPULAR_BITRATES = (
    10_000,
    20_000,
    50_000,
    100_000,
    125_000,
    250_000,
    500_000,
    800_000,
    1_000_000,
)

_CANDLE_LOW_BITRATE_PRESCALERS = {10_000: 600, 20_000: 300, 50_000: 120}


@dataclass(frozen=True)
class ReferenceAdapter:
    """Description of one adapter used as the opposite CAN node.

    Attributes:
        name: Short identifier used in pytest IDs and skip messages.
        interface: python-can interface name, for example ``"candle"``.
        serial: USB serial number used to select the adapter.
        supports_fd: Whether this adapter can transmit and receive CAN FD.
        fd_data_bitrate: Default FD data-phase bitrate when ``supports_fd``.
        low_bitrate_prescalers: BRP values for rates the generic timing helper
            cannot express, keyed by nominal bitrate.
        classic_bitrates: Nominal bitrates used for this adapter in the classic
            adapter↔adapter matrix.
    """

    name: str
    interface: str
    serial: str
    supports_fd: bool = False
    fd_data_bitrate: int = 2_000_000
    low_bitrate_prescalers: Dict[int, int] = field(default_factory=dict)
    classic_bitrates: Tuple[int, ...] = field(
        default_factory=lambda: POPULAR_BITRATES
    )

    @property
    def channel(self) -> str:
        """Return the python-can channel string for this adapter."""
        return f"{self.serial}:0"

    def bus_options(
        self,
        bitrate: int,
        *,
        fd: bool = False,
        data_bitrate: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build python-can options for opening this reference adapter.

        Args:
            bitrate: Nominal bitrate in bit/s.
            fd: Whether to enable CAN FD.
            data_bitrate: FD data-phase bitrate; uses the adapter default when
                omitted.

        Returns:
            Keyword arguments for :func:`can.Bus`.
        """
        options: Dict[str, Any] = {
            "interface": self.interface,
            "channel": self.channel,
            "bitrate": int(bitrate),
            "sample_point": 87.5,
            "ignore_config": True,
        }
        if fd:
            options["fd"] = True
            options["data_bitrate"] = int(data_bitrate or self.fd_data_bitrate)
            options["data_sample_point"] = 87.5
        return options


FYSETC_UCAN = ReferenceAdapter(
    name="fysetec-ucan",
    interface="candle",
    serial=os.environ.get("CANDLE_SERIAL", DEFAULT_CANDLE_SERIAL),
    supports_fd=False,
    low_bitrate_prescalers=dict(_CANDLE_LOW_BITRATE_PRESCALERS),
)

REFERENCE_ADAPTERS: Tuple[ReferenceAdapter, ...] = (FYSETC_UCAN,)

_FD_SERIAL = os.environ.get("CANABLE_FD_SERIAL")
if _FD_SERIAL:
    _FD_CLASSIC_RATES = tuple(
        int(value)
        for value in os.environ.get("CANABLE_FD_BITRATES", "").split(",")
        if value.strip()
    ) or POPULAR_BITRATES
    REFERENCE_ADAPTERS += (
        ReferenceAdapter(
            name="fd-reference",
            interface=os.environ.get("CANABLE_FD_INTERFACE", "candle"),
            serial=_FD_SERIAL,
            supports_fd=True,
            fd_data_bitrate=int(os.environ.get("CANABLE_FD_DATA_BITRATE", "2000000")),
            classic_bitrates=_FD_CLASSIC_RATES,
        ),
    )

FD_REFERENCE_ADAPTERS = tuple(
    adapter for adapter in REFERENCE_ADAPTERS if adapter.supports_fd
)

REFERENCE_PARAMS = tuple(
    pytest.param(adapter, id=adapter.name) for adapter in REFERENCE_ADAPTERS
)
FD_REFERENCE_PARAMS = tuple(
    pytest.param(adapter, id=adapter.name) for adapter in FD_REFERENCE_ADAPTERS
) or (
    pytest.param(
        None,  # type: ignore[arg-type]
        id="no-fd-adapter",
        marks=pytest.mark.skip(
            reason="set CANABLE_FD_SERIAL to register an FD reference"
        ),
    ),
)
CLASSIC_RATE_PARAMS = tuple(
    pytest.param(
        adapter,
        rate,
        id=f"{adapter.name}-{rate // 1000}k",
    )
    for adapter in REFERENCE_ADAPTERS
    for rate in adapter.classic_bitrates
)


def pytest_collection_modifyitems(config: Any, items: List[Any]) -> None:
    """Gate live tests on ``CANABLE_HW`` and keep stress tests opt-in.

    Args:
        config: Pytest configuration, used to inspect the ``-m`` expression.
        items: Collected pytest items.
    """
    if os.environ.get("CANABLE_HW") != "1":
        skip = pytest.mark.skip(reason="set CANABLE_HW=1 to run live-hardware tests")
        for item in items:
            item.add_marker(skip)
        return

    mark_expression = str(getattr(config.option, "markexpr", "") or "")
    if "stress" not in mark_expression:
        skip = pytest.mark.skip(reason="opt-in stress test; rerun with -m stress")
        for item in items:
            if item.get_closest_marker("stress"):
                item.add_marker(skip)


def _exception_text(exc: BaseException) -> str:
    """Collect an exception and its causes into one searchable string."""
    parts: List[str] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        parts.append(str(current))
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return " ".join(parts)


def _permission_denied(exc: BaseException) -> bool:
    """Return whether an exception indicates missing raw-USB permissions."""
    text = _exception_text(exc).casefold()
    return (
        "libusb_error_access" in text
        or "access denied" in text
        or getattr(exc, "errno", None) == 13
    )


def _close_open_candle_devices() -> None:
    """Close any candle devices left open by a partially initialized bus."""
    try:
        import candle_api
    except ImportError:
        return
    for device in candle_api.list_device():
        if device.is_open:
            device.close()


def _open_bus(options: dict[str, Any], label: str) -> can.BusABC:
    """Open a bus, skipping with an actionable message for environment errors.

    Args:
        options: Keyword arguments for :func:`can.Bus`.
        label: Human-readable adapter name used in messages. ``"canable"``
            identifies the device under test; all other labels are references.

    Returns:
        The opened bus.

    Raises:
        Exception: Re-raises initialization errors for the device under test.
    """
    try:
        return can.Bus(**options)
    except (can.CanInitializationError, RuntimeError) as exc:
        if options.get("interface") == "candle":
            _close_open_candle_devices()
        if _permission_denied(exc):
            pytest.skip(
                f"raw USB access denied for {label}; install udev/99-canable.rules "
                "and replug, or run with sudo"
            )
        if label != "canable":
            pytest.skip(f"{label} adapter is unavailable: {exc}")
        raise


@contextmanager
def _reference_timing_override(
    adapter: ReferenceAdapter, target_bitrate: int
) -> Iterator[None]:
    """Apply adapter-specific timing workarounds while a bus is constructed.

    Args:
        adapter: Reference adapter being opened.
        target_bitrate: Requested nominal bitrate in bit/s.

    Yields:
        ``None`` while the temporary timing override is active.
    """
    prescaler = adapter.low_bitrate_prescalers.get(target_bitrate)
    if prescaler is None:
        yield
        return

    original = can.BitTiming.from_sample_point

    def from_sample_point(
        f_clock: int, bitrate: int, sample_point: float = 69.0
    ) -> Any:
        """Return the 8-TQ timing for an otherwise unavailable low bitrate."""
        if bitrate == target_bitrate:
            return SimpleNamespace(
                tseg1=6,
                tseg2=1,
                sjw=1,
                brp=prescaler,
                sample_point=87.5,
            )
        return original(f_clock, bitrate, sample_point)

    setattr(can.BitTiming, "from_sample_point", from_sample_point)
    try:
        yield
    finally:
        setattr(can.BitTiming, "from_sample_point", original)


def close_bus(bus: can.BusABC) -> None:
    """Shut down a bus and close any plugin-owned USB device.

    ``python-can-candle`` keeps its libusb device open after ``shutdown()``.
    Tests must close it explicitly before reopening the same reference adapter.

    Args:
        bus: Bus created by a hardware-test factory.
    """
    if not getattr(bus, "_is_shutdown", False):
        bus.shutdown()
    device = getattr(bus, "_device", None)
    if device is not None and getattr(device, "is_open", False):
        device.close()


@pytest.fixture(scope="session")
def canable_serial() -> str:
    """Return the serial number of the CANable board under test."""
    return os.environ.get("CANABLE_SERIAL", DEFAULT_CANABLE_SERIAL)


@pytest.fixture(scope="session")
def bitrate() -> int:
    """Return the nominal CAN bitrate shared by both adapters."""
    return int(os.environ.get("CANABLE_BITRATE", "500000"))


@pytest.fixture(scope="session")
def timeout() -> float:
    """Return the receive timeout in seconds."""
    return float(os.environ.get("CANABLE_TIMEOUT", "2.0"))


@pytest.fixture
def canable_bus_factory(
    canable_serial: str, bitrate: int, timeout: float
) -> Iterator[Callable[..., can.BusABC]]:
    """Provide a factory that opens the target through the ``canable`` plugin."""
    buses: List[can.BusABC] = []

    def factory(**overrides: Any) -> can.BusABC:
        options: dict[str, Any] = {
            "interface": "canable",
            "channel": f"{canable_serial}:0",
            "bitrate": bitrate,
            "ignore_config": True,
            "timeout": timeout,
        }
        options.update(overrides)
        bus = _open_bus(options, "canable")
        buses.append(bus)
        return bus

    yield factory
    for bus in reversed(buses):
        close_bus(bus)


@pytest.fixture
def reference_bus_factory(
    bitrate: int,
) -> Iterator[Callable[..., can.BusABC]]:
    """Provide a factory that opens any registered reference adapter."""
    buses: List[can.BusABC] = []

    def factory(adapter: ReferenceAdapter, **overrides: Any) -> can.BusABC:
        options = adapter.bus_options(bitrate)
        options.update(overrides)
        with _reference_timing_override(adapter, int(options["bitrate"])):
            bus = _open_bus(options, adapter.name)
        buses.append(bus)
        return bus

    yield factory
    for bus in reversed(buses):
        close_bus(bus)


def matches(expected: can.Message, received: can.Message) -> bool:
    """Return whether a received frame carries the expected CAN payload.

    Args:
        expected: Message that was sent.
        received: Candidate received message.

    Returns:
        ``True`` when ID, frame kind, and data match.
    """
    return (
        received.arbitration_id == expected.arbitration_id
        and received.is_extended_id == expected.is_extended_id
        and received.is_remote_frame == expected.is_remote_frame
        and bytes(received.data) == bytes(expected.data)
    )


def receive_matching(
    bus: can.BusABC, expected: can.Message, timeout: float
) -> Optional[can.Message]:
    """Wait for a message matching ``expected``, ignoring error frames.

    Args:
        bus: Plugin-created bus to read from.
        expected: Message to match.
        timeout: Maximum wait time in seconds.

    Returns:
        The matching message, or ``None`` on timeout.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        received = bus.recv(min(remaining, 0.1))
        if received is None or received.is_error_frame:
            continue
        if matches(expected, received):
            return received


def transfer(
    sender: can.BusABC,
    receiver: can.BusABC,
    message: can.Message,
    timeout: float,
    expect_success: bool = True,
) -> Optional[can.Message]:
    """Send a frame while a reader thread waits on the other adapter.

    Args:
        sender: Bus used to transmit.
        receiver: Bus used to receive.
        message: Frame to transmit.
        timeout: Maximum wait time in seconds.
        expect_success: Whether a matching frame must be received.

    Returns:
        The matching received frame, or ``None`` when none arrived.
    """
    result: dict[str, can.Message] = {}

    def reader() -> None:
        found = receive_matching(receiver, message, timeout)
        if found is not None:
            result["message"] = found

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    time.sleep(0.02)  # Start the receiver before the transmitter.
    try:
        sender.send(message)
    finally:
        thread.join(timeout=timeout + 1.0)
    received = result.get("message")
    if expect_success:
        assert received is not None, f"no matching frame received for {message}"
    else:
        assert received is None, f"unexpected frame received for {message}"
    return received
