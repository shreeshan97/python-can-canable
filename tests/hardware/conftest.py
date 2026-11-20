"""Fixtures and helpers for opt-in live-hardware tests.

The target adapter is always opened through the python-can plugin interface
(``can.Bus(interface="canable", ...)``). The reference adapter uses the
``python-can-candle`` plugin with ``interface="candle"``.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Iterator, List, Optional

import can
import pytest

DEFAULT_CANABLE_SERIAL = "209D3889594550052"
DEFAULT_CANDLE_SERIAL = "003500465446571320343032"


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


def _open_bus(options: dict[str, Any], label: str) -> can.BusABC:
    """Open a bus, skipping with an actionable message for environment errors."""
    try:
        return can.Bus(**options)
    except can.CanInitializationError as exc:
        if _permission_denied(exc):
            pytest.skip(
                f"raw USB access denied for {label}; install udev/99-canable.rules "
                "and replug, or run with sudo"
            )
        if label == "reference":
            pytest.skip(f"reference adapter is unavailable: {exc}")
        raise


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
def candle_serial() -> str:
    """Return the serial number of the candle reference adapter."""
    return os.environ.get("CANDLE_SERIAL", DEFAULT_CANDLE_SERIAL)


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
    candle_serial: str, bitrate: int
) -> Iterator[Callable[..., can.BusABC]]:
    """Provide a factory that opens the reference through ``interface="candle"``."""
    buses: List[can.BusABC] = []

    def factory(**overrides: Any) -> can.BusABC:
        options: dict[str, Any] = {
            "interface": "candle",
            "channel": f"{candle_serial}:0",
            "bitrate": bitrate,
            "ignore_config": True,
        }
        options.update(overrides)
        bus = _open_bus(options, "reference")
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
