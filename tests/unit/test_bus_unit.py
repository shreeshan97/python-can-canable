"""Unit tests for :class:`canable.bus.CanableBus` using a fake USB device."""

from __future__ import annotations

import struct

import can
import pytest

from canable import protocol as p
from canable.exceptions import CanableInitializationError
from conftest import board_info_bytes, capability_bytes


def make_bus(usb_bus: object, **kwargs: object) -> can.BusABC:
    """Open a plugin-created bus against the fake USB device."""
    device = usb_bus.add()
    options = {
        "interface": "canable",
        "channel": device.serial_number,
        "ignore_config": True,
    }
    options.update(kwargs)
    return can.Bus(**options)


def rx_frame(can_id: int, data: bytes, timestamp: int | None = None) -> bytes:
    """Build a complete fake RX frame message."""
    extended = can_id > 0x7FF
    raw_id = can_id | (int(p.CanIdFlags.EXTENDED) if extended else 0)
    if timestamp is None:
        size = struct.calcsize(p.RX_FRAME_FORMAT) + len(data)
        return (
            struct.pack(
                p.RX_FRAME_FORMAT, size, int(p.MessageType.RX_FRAME), 0, raw_id
            )
            + data
        )
    size = struct.calcsize(p.RX_FRAME_TIMESTAMP_FORMAT) + len(data)
    return (
        struct.pack(
            p.RX_FRAME_TIMESTAMP_FORMAT,
            size,
            int(p.MessageType.RX_FRAME),
            0,
            raw_id,
            timestamp,
        )
        + data
    )


def echo_frame(marker: int, timestamp: int | None = None) -> bytes:
    """Build a complete fake TX echo message."""
    if timestamp is None:
        return struct.pack(p.TX_ECHO_FORMAT, 3, int(p.MessageType.TX_ECHO), marker)
    return struct.pack(p.TX_ECHO_TIMESTAMP_FORMAT, 7, int(p.MessageType.TX_ECHO), marker, timestamp)


def test_initialization_call_order(usb_bus: object) -> None:
    """Initialization follows the documented Elmü 2.5 command sequence."""
    bus = make_bus(usb_bus)
    device = usb_bus.devices[0]

    requests = [request for request, _data, _value in device.out_requests()]
    assert requests == [
        p.Request.SET_HOST_FORMAT,
        p.Request.SET_DEVICE_MODE,
        p.Request.SET_BIT_TIMING,
        p.Request.SET_FILTER,
        p.Request.SET_DEVICE_MODE,
    ]
    assert device.responses[p.Request.GET_DEVICE_VERSION]
    assert device.claimed
    assert device.detached

    calls = device.ctrl_calls
    for index, call in enumerate(calls):
        bm_request_type, request, _value, _idx, _data, _timeout = call
        if bm_request_type != p.REQUEST_OUT:
            continue
        next_call = calls[index + 1]
        assert next_call[0] == p.REQUEST_IN
        assert next_call[1] == int(p.Request.GET_LAST_ERROR)

    host_format = device.out_requests()[0][1]
    assert host_format == struct.pack("<I", p.HOST_FORMAT_MAGIC)
    reset_mode, reset_flags = struct.unpack(p.DEVICE_MODE_FORMAT, device.out_requests()[1][1])
    assert reset_mode == int(p.DeviceMode.RESET)
    assert reset_flags & int(p.DeviceFlags.PROTOCOL_ELMUE)
    start_mode, start_flags = struct.unpack(p.DEVICE_MODE_FORMAT, device.out_requests()[-1][1])
    assert start_mode == int(p.DeviceMode.START)
    assert start_flags & int(p.DeviceFlags.PROTOCOL_ELMUE)
    assert bus.get_board_info().board_name == "CanableDevBoard FDCAN"
    assert bus.get_device_version().hal_ver_high == 1
    assert bus.get_capabilities().fclk_can == 160_000_000
    bus.shutdown()


def test_feedback_failure_aborts_initialization(usb_bus: object) -> None:
    """A non-success feedback code prevents the bus from opening."""
    device = usb_bus.add()
    device.feedback_script = [int(p.Feedback.INVALID_PARAMETER)]
    with pytest.raises(CanableInitializationError) as excinfo:
        can.Bus(interface="canable", channel=device.serial_number, ignore_config=True)
    assert "INVALID_PARAMETER" in str(excinfo.value.__cause__) or "0x32" in str(
        excinfo.value.__cause__
    )
    assert not device.claimed or device.released


def test_board_identity_validation(usb_bus: object) -> None:
    """Only CanableDevBoard firmware names are accepted."""
    device = usb_bus.add()
    device.responses[p.Request.GET_BOARD_INFO] = board_info_bytes("OtherBoard")
    with pytest.raises(CanableInitializationError, match="not a CanableDevBoard"):
        can.Bus(interface="canable", channel=device.serial_number, ignore_config=True)

    device2 = usb_bus.add(serial="NOELMUE")
    device2.responses[p.Request.GET_CAPABILITIES] = capability_bytes(feature=0)
    with pytest.raises(CanableInitializationError, match="Elm"):
        can.Bus(interface="canable", channel=device2.serial_number, ignore_config=True)


def test_send_receive_and_echo(usb_bus: object) -> None:
    """TX, RX, echo correlation, error reporting, and busload decoding work."""
    bus = make_bus(usb_bus, receive_own_messages=True, timestamps=True)
    device = usb_bus.devices[0]

    sent = can.Message(arbitration_id=0x321, data=b"\x11\x22", is_extended_id=False)
    bus.send(sent)
    assert len(device.bulk_writes) == 1
    assert device.bulk_writes[0][1] == int(p.MessageType.TX_FRAME)
    assert device.bulk_writes[0][7] == 1

    device.queue_in(echo_frame(1, 1234))
    echoed = bus.recv(0.2)
    assert echoed is not None
    assert echoed.arbitration_id == sent.arbitration_id
    assert bytes(echoed.data) == bytes(sent.data)
    assert echoed.is_rx
    assert echoed.timestamp == pytest.approx(0.001234)

    device.queue_in(rx_frame(0x1ABCDEF, b"payload", timestamp=5000))
    received = bus.recv(0.2)
    assert received is not None
    assert received.arbitration_id == 0x1ABCDEF
    assert bytes(received.data) == b"payload"

    error = struct.pack(
        p.ERROR_FRAME_TIMESTAMP_FORMAT,
        18,
        int(p.MessageType.ERROR),
        0x08,
        bytes.fromhex("0000000000000008"),
        6000,
    )
    device.queue_in(error)
    assert bus.recv(0.05) is None
    assert bus.error_frame_count == 1

    busload = struct.pack(p.BUSLOAD_FORMAT, 3, int(p.MessageType.BUSLOAD), 42)
    device.queue_in(busload)
    assert bus.recv(0.05) is None
    assert bus.busload_percent == 42
    bus.shutdown()


def test_blob_and_malformed_messages(usb_bus: object) -> None:
    """Blob subframes are queued and malformed frames do not break reception."""
    bus = make_bus(usb_bus)
    device = usb_bus.devices[0]
    first = rx_frame(0x10, b"A")
    second = rx_frame(0x11, b"B")
    blob_body = bytes([2, int(p.MessageType.RX_FRAME)]) + first + second
    blob = struct.pack(p.BLOB_HEADER_FORMAT, len(blob_body) + 2, int(p.MessageType.RX_BLOB))
    device.queue_in(blob + blob_body)

    first_rx = bus.recv(0.2)
    second_rx = bus.recv(0.2)
    assert first_rx is not None and first_rx.arbitration_id == 0x10
    assert second_rx is not None and second_rx.arbitration_id == 0x11

    device.queue_in(b"\x01\x0c\x00\x00")
    assert bus.recv(0.05) is None
    bus.shutdown()


def test_timestamp_rollover(usb_bus: object) -> None:
    """Firmware one-hour timestamp rollover is compensated in host timestamps."""
    bus = make_bus(usb_bus, timestamps=True)
    device = usb_bus.devices[0]
    device.queue_in(rx_frame(0x20, b"1", timestamp=3_599_000_000))
    before = bus.recv(0.2)
    device.queue_in(rx_frame(0x20, b"2", timestamp=1_000_000))
    after = bus.recv(0.2)
    assert before is not None and after is not None
    assert after.timestamp > before.timestamp
    assert after.timestamp - before.timestamp == pytest.approx(2.0, abs=0.01)
    bus.shutdown()


def test_filter_programming(usb_bus: object) -> None:
    """Python-can filters map to host pass filters and restart the channel."""
    bus = make_bus(usb_bus)
    device = usb_bus.devices[0]
    bus.set_filters([{"can_id": 0x123, "can_mask": 0x7FF}])
    filter_ops = [
        data[0]
        for request, data, _value in device.out_requests()
        if request == p.Request.SET_FILTER
    ]
    assert filter_ops[-3:] == [
        int(p.FilterOperation.HOST_CLEAR),
        int(p.FilterOperation.HOST_PASS_11),
        int(p.FilterOperation.HOST_PASS_29),
    ]
    bus.set_filters([{"can_id": 0x1ABCDEF, "can_mask": 0x1FFFFFFF, "extended": True}])
    filter_ops = [
        data[0]
        for request, data, _value in device.out_requests()
        if request == p.Request.SET_FILTER
    ]
    assert filter_ops[-2:] == [
        int(p.FilterOperation.HOST_CLEAR),
        int(p.FilterOperation.HOST_PASS_29),
    ]
    bus.shutdown()


def test_runtime_bitrate_and_shutdown(usb_bus: object) -> None:
    """Bitrate changes restart the channel and shutdown is idempotent."""
    bus = make_bus(usb_bus)
    device = usb_bus.devices[0]
    initial_timing = [
        data for request, data, _value in device.out_requests() if request == p.Request.SET_BIT_TIMING
    ][0]
    bus.set_bitrate(250_000)
    timings = [
        data for request, data, _value in device.out_requests() if request == p.Request.SET_BIT_TIMING
    ]
    assert timings[-1] != initial_timing
    assert [r for r, _d, _v in device.out_requests()].count(p.Request.SET_DEVICE_MODE) >= 4

    bus.shutdown()
    assert device.released
    assert device.reattached
    assert bus.kernel_driver_restored
    calls_after_shutdown = len(device.ctrl_calls)
    bus.shutdown()
    assert len(device.ctrl_calls) == calls_after_shutdown
