"""Unit tests for Elmü 2.5 protocol serialization and parsing."""

from __future__ import annotations

import struct

import can
import pytest

from canable import protocol as p
from conftest import capability_bytes, capability_fd_bytes


def test_struct_sizes_match_firmware() -> None:
    """Packed struct sizes must match the Elmü firmware sample."""
    expected = {
        p.BIT_TIMING_FORMAT: 20,
        p.DEVICE_MODE_FORMAT: 8,
        p.TIME_MIN_MAX_FORMAT: 32,
        p.CAPABILITY_CLASSIC_FORMAT: 40,
        p.CAPABILITY_FD_FORMAT: 72,
        p.DEVICE_VERSION_FORMAT: 12,
        p.ERROR_STATE_FORMAT: 12,
        p.BOARD_INFO_FORMAT: 56,
        p.FILTER_FORMAT: 17,
        p.PIN_STATUS_FORMAT: 12,
        p.HEADER_FORMAT: 2,
        p.TX_FRAME_FORMAT: 8,
        p.RX_FRAME_FORMAT: 7,
        p.RX_FRAME_TIMESTAMP_FORMAT: 11,
        p.TX_ECHO_FORMAT: 3,
        p.TX_ECHO_TIMESTAMP_FORMAT: 7,
        p.ERROR_FRAME_FORMAT: 14,
        p.ERROR_FRAME_TIMESTAMP_FORMAT: 18,
        p.BUSLOAD_FORMAT: 3,
        p.BLOB_HEADER_FORMAT: 2,
    }
    for fmt, size in expected.items():
        assert struct.calcsize(fmt) == size


def test_feedback_codes_and_flags() -> None:
    """Feedback codes and protocol flags must use firmware values."""
    assert int(p.Feedback.SUCCESS) == 2
    assert int(p.Feedback.INVALID_COMMAND) == 0x31
    assert int(p.Feedback.PARAM_OUT_OF_RANGE) == 0x3D
    assert int(p.DeviceFlags.PROTOCOL_ELMUE) == 0x04000
    assert int(p.DeviceFlags.SEND_USB_BLOBS) == 0x08000
    assert int(p.CanIdFlags.EXTENDED) == 0x80000000
    assert int(p.CanIdFlags.RTR) == 0x40000000
    assert int(p.CanIdFlags.ERROR) == 0x20000000
    assert int(p.FrameFlags.FDF) == 0x02
    assert int(p.FrameFlags.BRS) == 0x04
    assert int(p.FrameFlags.ESI) == 0x08
    assert p.feedback_name(0x31) == "INVALID_COMMAND"
    assert p.feedback_name(0x99) == "UNKNOWN"


def test_pack_timing_and_device_mode() -> None:
    """Timing and mode structures are packed little-endian."""
    timing = p.BitTiming(prop=1, seg1=15, seg2=3, sjw=3, brp=16)
    assert p.pack_bit_timing(timing) == struct.pack("<IIIII", 1, 15, 3, 3, 16)
    mode = p.pack_device_mode(p.DeviceMode.START, p.DeviceFlags.PROTOCOL_ELMUE)
    assert mode == struct.pack("<II", 1, 0x04000)


def test_unpack_capabilities() -> None:
    """Classic and FD capability buffers decode clock, flags, and limits."""
    classic = p.unpack_capability_classic(capability_bytes())
    assert classic.fclk_can == 160_000_000
    assert classic.feature & int(p.DeviceFlags.PROTOCOL_ELMUE)
    assert classic.limits.seg1_max == 128
    assert classic.limits.brp_max == 512

    nominal, data = p.unpack_capability_fd(capability_fd_bytes())
    assert nominal.fclk_can == 160_000_000
    assert data.fclk_can == 160_000_000
    assert nominal.limits.brp_inc == 1

    with pytest.raises(ValueError):
        p.unpack_capability_classic(b"\x00" * 39)
    with pytest.raises(ValueError):
        p.unpack_capability_fd(b"\x00" * 71)


def test_unpack_version_board_error_and_pin() -> None:
    """Version, board, error-state, and pin-status records decode correctly."""
    version = p.unpack_device_version(struct.pack(p.DEVICE_VERSION_FORMAT, 1, 2, 3, 0, 0x0250, 0x0100))
    assert (version.hal_ver_high, version.hal_ver_mid, version.hal_ver_low) == (1, 2, 3)
    assert version.sw_version_bcd == 0x0250

    board = p.unpack_board_info(
        struct.pack(p.BOARD_INFO_FORMAT, 0x468, b"STM32G431xx", b"CanableDevBoard FDCAN", 1)
    )
    assert board.board_name == "CanableDevBoard FDCAN"
    assert board.mcu_name == "STM32G431xx"

    state = p.unpack_error_state(struct.pack(p.ERROR_STATE_FORMAT, 3, 120, 200))
    assert (state.state, state.rx_err, state.tx_err) == (3, 120, 200)

    assert p.unpack_pin_status(b"\x03\x00") == (
        p.PinStatusFlag.HIGH | p.PinStatusFlag.ENABLED
    )
    with pytest.raises(ValueError):
        p.unpack_pin_status(b"\x00")


def test_pack_tx_frame_classic() -> None:
    """A classic TX frame has the expected header and ID flags."""
    message = can.Message(
        arbitration_id=0x123,
        data=bytes.fromhex("1122334455667788"),
        is_extended_id=False,
        is_rx=False,
    )
    frame = p.pack_tx_frame(message, marker=7)
    assert frame[0] == 16
    assert frame[1] == int(p.MessageType.TX_FRAME)
    assert frame[2] == 0
    assert struct.unpack("<I", frame[3:7])[0] == 0x123
    assert frame[7] == 7
    assert frame[8:] == message.data


def test_pack_tx_frame_extended_remote_fd() -> None:
    """Extended, remote, and FD flags are encoded in TX frames."""
    remote = can.Message(
        arbitration_id=0x1ABCDEF,
        is_extended_id=True,
        is_remote_frame=True,
        dlc=8,
        is_rx=False,
    )
    frame = p.pack_tx_frame(remote)
    raw_id = struct.unpack("<I", frame[3:7])[0]
    assert raw_id & int(p.CanIdFlags.EXTENDED)
    assert raw_id & int(p.CanIdFlags.RTR)
    assert frame[8] == 8

    fd_message = can.Message(
        arbitration_id=0x123,
        data=b"\x01\x02",
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
        error_state_indicator=True,
        is_rx=False,
    )
    fd_frame = p.pack_tx_frame(fd_message)
    assert fd_frame[2] == 0x0E
    with pytest.raises(ValueError):
        p.pack_tx_frame(fd_message, marker=256)


def test_parse_rx_frame_variants() -> None:
    """RX frames decode IDs, flags, payloads, and microsecond timestamps."""
    payload = b"ABCD"
    header = struct.pack(p.RX_FRAME_FORMAT, 11, int(p.MessageType.RX_FRAME), 0, 0x321)
    message = p.parse_rx_frame(header + payload, False)
    assert message.arbitration_id == 0x321
    assert not message.is_extended_id
    assert bytes(message.data) == payload
    assert message.dlc == 4
    assert message.timestamp == 0.0

    raw_id = 0x1ABCDEF | int(p.CanIdFlags.EXTENDED)
    flags = int(p.FrameFlags.FDF | p.FrameFlags.BRS)
    timestamped = struct.pack(
        p.RX_FRAME_TIMESTAMP_FORMAT,
        16,
        int(p.MessageType.RX_FRAME),
        flags,
        raw_id,
        1_500_000,
    )
    message = p.parse_rx_frame(timestamped + b"\x01\x02", True, channel=3)
    assert message.arbitration_id == 0x1ABCDEF
    assert message.is_extended_id
    assert message.is_fd
    assert message.bitrate_switch
    assert message.channel == 3
    assert message.timestamp == 1.5

    remote_id = 0x123 | int(p.CanIdFlags.RTR)
    remote = struct.pack(p.RX_FRAME_FORMAT, 8, int(p.MessageType.RX_FRAME), 0, remote_id)
    message = p.parse_rx_frame(remote + b"\x08", False)
    assert message.is_remote_frame
    assert message.dlc == 8
    assert bytes(message.data) == b""

    with pytest.raises(ValueError):
        p.parse_rx_frame(b"\x07\x0c\x00", False)


def test_parse_echo_error_busload_and_string() -> None:
    """Auxiliary Elmü messages decode into their documented fields."""
    echo = struct.pack(p.TX_ECHO_FORMAT, 3, int(p.MessageType.TX_ECHO), 9)
    assert p.parse_echo_frame(echo, False) == (9, None)
    echo_ts = struct.pack(p.TX_ECHO_TIMESTAMP_FORMAT, 7, int(p.MessageType.TX_ECHO), 9, 42)
    assert p.parse_echo_frame(echo_ts, True) == (9, 42)

    error = struct.pack(
        p.ERROR_FRAME_FORMAT,
        14,
        int(p.MessageType.ERROR),
        0x08,
        bytes.fromhex("0000000000000008"),
    )
    err_id, err_data, ts = p.parse_error_frame(error, False)
    assert err_id == 0x08
    assert err_data[7] == 8
    assert ts is None

    busload = struct.pack(p.BUSLOAD_FORMAT, 3, int(p.MessageType.BUSLOAD), 37)
    assert p.parse_busload_frame(busload) == 37

    string = struct.pack(p.HEADER_FORMAT, 6, int(p.MessageType.STRING)) + b"hello"
    assert p.parse_string_frame(string) == "hello"


def test_blob_iteration_validates_subframes() -> None:
    """Blob subframes are walked using each header's declared size."""
    subframe = p.pack_tx_frame(
        can.Message(arbitration_id=0x1, data=b"\xAA", is_extended_id=False)
    )
    blob = struct.pack(p.HEADER_FORMAT, 4 + len(subframe), int(p.MessageType.RX_BLOB))
    blob += bytes([1, int(p.MessageType.RX_FRAME)]) + subframe
    assert p.parse_blob_header(blob) == (1, p.MessageType.RX_FRAME)
    assert list(p.iter_blob_frames(blob)) == [subframe]

    bad = blob[:4] + bytes([255]) + blob[5:]
    with pytest.raises(ValueError):
        list(p.iter_blob_frames(bad))
    with pytest.raises(ValueError):
        p.parse_blob_header(b"\x04\x11")
