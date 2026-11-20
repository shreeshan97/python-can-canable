"""Elmü CANable 2.5 USB protocol constants and serialization helpers.

The constants and layouts in this module are derived from the MIT-licensed ElmüSoft
Candlelight 2.5 firmware. See ``docs/Protocol.md`` and ``NOTICE`` for attribution.

Only the new Elmü 2.5 frame protocol is implemented here; the legacy 80-byte
Candlelight/gs_usb frame format is deliberately absent.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Iterator, Optional, Tuple

import can

__all__ = [
    "VID",
    "PID",
    "BIT_TIMING_FORMAT",
    "DEVICE_MODE_FORMAT",
    "TIME_MIN_MAX_FORMAT",
    "CAPABILITY_CLASSIC_FORMAT",
    "CAPABILITY_FD_FORMAT",
    "DEVICE_VERSION_FORMAT",
    "ERROR_STATE_FORMAT",
    "BOARD_INFO_FORMAT",
    "FILTER_FORMAT",
    "PIN_STATUS_FORMAT",
    "HEADER_FORMAT",
    "TX_FRAME_FORMAT",
    "RX_FRAME_FORMAT",
    "RX_FRAME_TIMESTAMP_FORMAT",
    "TX_ECHO_FORMAT",
    "TX_ECHO_TIMESTAMP_FORMAT",
    "ERROR_FRAME_FORMAT",
    "ERROR_FRAME_TIMESTAMP_FORMAT",
    "BUSLOAD_FORMAT",
    "BLOB_HEADER_FORMAT",
    "MAX_BLOB_SIZE",
    "HOST_FORMAT_MAGIC",
    "Request",
    "DeviceMode",
    "DeviceFlags",
    "Feedback",
    "FrameFlags",
    "CanIdFlags",
    "MessageType",
    "FilterOperation",
    "PinOperation",
    "PinID",
    "PinStatusFlag",
    "BoardFlag",
    "ErrorAppFlag",
    "TimingLimits",
    "BitTiming",
    "Capability",
    "DeviceVersion",
    "BoardInfo",
    "ErrorState",
    "Filter",
    "pack_bit_timing",
    "pack_device_mode",
    "unpack_capability_classic",
    "unpack_capability_fd",
    "unpack_device_version",
    "unpack_error_state",
    "unpack_board_info",
    "pack_filter",
    "pack_pin_status",
    "unpack_pin_status",
    "pack_tx_frame",
    "parse_rx_frame",
    "parse_echo_frame",
    "parse_error_frame",
    "parse_busload_frame",
    "parse_string_frame",
    "parse_blob_header",
    "iter_blob_frames",
    "feedback_name",
]

VID = 0x1D50
PID = 0x606F
MAX_BLOB_SIZE = 2048
HOST_FORMAT_MAGIC = 0xBEEF

BIT_TIMING_FORMAT = "<IIIII"
DEVICE_MODE_FORMAT = "<II"
TIME_MIN_MAX_FORMAT = "<IIIIIIII"
CAPABILITY_CLASSIC_FORMAT = "<IIIIIIIIII"
CAPABILITY_FD_FORMAT = "<IIIIIIIIIIIIIIIIII"
DEVICE_VERSION_FORMAT = "<BBBBII"
ERROR_STATE_FORMAT = "<III"
BOARD_INFO_FORMAT = "<H25s25sI"
FILTER_FORMAT = "<BIIBB6s"
PIN_STATUS_FORMAT = "<HHII"
HEADER_FORMAT = "<BB"
TX_FRAME_FORMAT = "<BBBIB"
RX_FRAME_FORMAT = "<BBBI"
RX_FRAME_TIMESTAMP_FORMAT = "<BBBII"
TX_ECHO_FORMAT = "<BBB"
TX_ECHO_TIMESTAMP_FORMAT = "<BBBI"
ERROR_FRAME_FORMAT = "<BBI8s"
ERROR_FRAME_TIMESTAMP_FORMAT = "<BBI8sI"
BUSLOAD_FORMAT = "<BBB"
BLOB_HEADER_FORMAT = "<BB"

REQUEST_OUT = 0x41
REQUEST_IN = 0xC1


class Request(IntEnum):
    """Vendor control requests understood by Elmü 2.5 firmware."""

    SET_HOST_FORMAT = 0
    SET_BIT_TIMING = 1
    SET_DEVICE_MODE = 2
    GET_CAPABILITIES = 4
    GET_DEVICE_VERSION = 5
    GET_TIMESTAMP = 6
    IDENTIFY = 7
    SET_BIT_TIMING_FD = 10
    GET_CAPABILITIES_FD = 11
    SET_TERMINATION = 12
    GET_TERMINATION = 13
    GET_ERROR_STATE = 14
    GET_BOARD_INFO = 20
    SET_FILTER = 21
    GET_LAST_ERROR = 22
    SET_BUSLOAD_REPORT = 23
    SET_PIN_STATUS = 24
    GET_PIN_STATUS = 25
    READ_FLASH = 26
    WRITE_FLASH = 27


class DeviceMode(IntEnum):
    """Values for ``kDeviceMode.mode``."""

    RESET = 0
    START = 1


class DeviceFlags(IntFlag):
    """Device mode and capability flags."""

    NONE = 0
    LISTEN_ONLY = 0x00001
    LOOPBACK = 0x00002
    ONE_SHOT = 0x00008
    TIMESTAMP = 0x00010
    IDENTIFY = 0x00020
    CAN_FD = 0x00100
    BIT_TIMING_FD = 0x00400
    TERMINATION = 0x00800
    GET_ERROR_STATE = 0x02000
    PROTOCOL_ELMUE = 0x04000
    SEND_USB_BLOBS = 0x08000


class Feedback(IntEnum):
    """Firmware feedback codes returned by ``GET_LAST_ERROR``."""

    SUCCESS = 2
    INVALID_COMMAND = ord("1")
    INVALID_PARAMETER = ord("2")
    ADAPTER_MUST_BE_OPEN = ord("3")
    ADAPTER_MUST_BE_CLOSED = ord("4")
    ERROR_FROM_HAL = ord("5")
    UNSUPPORTED_FEATURE = ord("6")
    TX_BUFFER_FULL = ord("7")
    BUS_IS_OFF = ord("8")
    NO_TX_IN_SILENT_MODE = ord("9")
    BAUDRATE_NOT_SET = ord("9") + 1
    OPT_BYTES_PROGR_FAILED = ord("9") + 2
    RESET_REQUIRED = ord("9") + 3
    PARAM_OUT_OF_RANGE = ord("9") + 4


class FrameFlags(IntFlag):
    """Flags in the ``flags`` byte of a CAN frame."""

    NONE = 0
    FDF = 0x02
    BRS = 0x04
    ESI = 0x08


class CanIdFlags(IntFlag):
    """Flags OR-ed into the 32-bit CAN ID field."""

    ERROR = 0x20000000
    RTR = 0x40000000
    EXTENDED = 0x80000000
    MASK_11 = 0x000007FF
    MASK_29 = 0x1FFFFFFF


class MessageType(IntEnum):
    """Elmü 2.5 USB message types."""

    TX_FRAME = 10
    TX_ECHO = 11
    RX_FRAME = 12
    ERROR = 13
    STRING = 14
    BUSLOAD = 15
    TX_BLOB = 16
    RX_BLOB = 17


class FilterOperation(IntEnum):
    """Operations for ``kFilter.Operation``."""

    HOST_CLEAR = 0
    HOST_PASS_11 = 1
    HOST_PASS_29 = 2
    BRIDGE_CLEAR = 10
    BRIDGE_PASS_11 = 11
    BRIDGE_PASS_29 = 12
    BRIDGE_BLOCK_11 = 13
    BRIDGE_BLOCK_29 = 14


class PinOperation(IntEnum):
    """Operations for ``kPinStatus.Operation``."""

    RESET = 0
    SET = 1
    TRISTATE = 2
    PULL_DOWN = 3
    PULL_UP = 4
    DISABLE = 5
    ENABLE = 6


class PinID(IntEnum):
    """Supported pin identifiers."""

    BOOT0 = 1


class PinStatusFlag(IntFlag):
    """Bits returned by ``GET_PIN_STATUS``."""

    HIGH = 0x01
    ENABLED = 0x02


class BoardFlag(IntFlag):
    """Bits in ``kBoardInfo.BoardFlags``."""

    QUARTZ_IN_USE = 0x00000001
    USB_HIGH_SPEED = 0x00000002


class ErrorAppFlag(IntFlag):
    """Controller flags stored in error-report data byte 5."""

    NO_ERROR = 0x00
    CAN_RX_FAIL = 0x01
    CAN_TX_FAIL = 0x02
    CAN_TX_OVERFLOW = 0x04
    USB_IN_OVERFLOW = 0x08
    CAN_TX_TIMEOUT = 0x10


@dataclass(frozen=True)
class TimingLimits:
    """Controller timing limits from ``kTimeMinMax``.

    Attributes:
        seg1_min: Minimum time segment 1 in time quanta.
        seg1_max: Maximum time segment 1 in time quanta.
        seg2_min: Minimum time segment 2 in time quanta.
        seg2_max: Maximum time segment 2 in time quanta.
        sjw_max: Maximum synchronization jump width.
        brp_min: Minimum bitrate prescaler.
        brp_max: Maximum bitrate prescaler.
        brp_inc: Prescaler increment (normally 1).
    """

    seg1_min: int
    seg1_max: int
    seg2_min: int
    seg2_max: int
    sjw_max: int
    brp_min: int
    brp_max: int
    brp_inc: int


@dataclass(frozen=True)
class BitTiming:
    """Firmware bit-timing values from ``kBitTiming``.

    Attributes:
        prop: Propagation segment; this driver normally uses 1.
        seg1: Phase segment 1; ``prop + seg1`` is the effective segment 1.
        seg2: Phase segment 2.
        sjw: Synchronization jump width.
        brp: Bitrate prescaler.
    """

    prop: int
    seg1: int
    seg2: int
    sjw: int
    brp: int


@dataclass(frozen=True)
class Capability:
    """Decoded ``kCapabilityClassic`` or one half of ``kCapabilityFD``.

    Attributes:
        feature: Feature flags as ``DeviceFlags``.
        fclk_can: CAN clock frequency in Hz.
        limits: Controller timing limits.
    """

    feature: int
    fclk_can: int
    limits: TimingLimits


@dataclass(frozen=True)
class DeviceVersion:
    """Decoded ``kDeviceVersion``.

    Attributes:
        hal_ver_high: HAL major version.
        hal_ver_mid: HAL minor version.
        hal_ver_low: HAL patch version.
        icount: Interface count minus one.
        sw_version_bcd: Firmware version in BCD.
        hw_version_bcd: Hardware version in BCD.
    """

    hal_ver_high: int
    hal_ver_mid: int
    hal_ver_low: int
    icount: int
    sw_version_bcd: int
    hw_version_bcd: int


@dataclass(frozen=True)
class BoardInfo:
    """Decoded ``kBoardInfo``.

    Attributes:
        mcu_device_id: STM32 device ID code.
        mcu_name: MCU name from the firmware build.
        board_name: Board name from the firmware build.
        board_flags: ``BoardFlag`` bits.
    """

    mcu_device_id: int
    mcu_name: str
    board_name: str
    board_flags: int


@dataclass(frozen=True)
class ErrorState:
    """Decoded ``kErrorState``.

    Attributes:
        state: Bus state code (0 = active, 3 = bus off).
        rx_err: Receive error counter.
        tx_err: Transmit error counter.
    """

    state: int
    rx_err: int
    tx_err: int


@dataclass(frozen=True)
class Filter:
    """Decoded ``kFilter`` request body.

    Attributes:
        operation: ``FilterOperation`` value.
        can_id: Acceptance filter value.
        mask: Acceptance mask.
        index: Bridge filter index.
        dest_channel: Bridge destination channel.
    """

    operation: int
    can_id: int
    mask: int
    index: int
    dest_channel: int


def feedback_name(code: int) -> str:
    """Return a descriptive name for a firmware feedback code.

    Args:
        code: Numeric feedback code.

    Returns:
        The enum name, or ``UNKNOWN`` if the code is not defined.
    """
    try:
        return Feedback(code).name
    except ValueError:
        return "UNKNOWN"


def _unpack_struct(
    fmt: str, data: bytes, message: str, *, exact: bool = False
) -> Tuple[int, ...]:
    """Unpack a fixed prefix, rejecting short or unexpected-length buffers.

    Args:
        fmt: ``struct`` format string for the fixed prefix.
        data: Buffer to decode.
        message: Error message for an invalid buffer length.
        exact: Require the buffer to contain no bytes past the prefix.

    Returns:
        Unpacked field values.

    Raises:
        ValueError: If the buffer is too short or, with ``exact``, too long.
    """
    size = struct.calcsize(fmt)
    if len(data) < size or (exact and len(data) != size):
        raise ValueError(message)
    return struct.unpack(fmt, data[:size])


def pack_bit_timing(timing: BitTiming) -> bytes:
    """Pack a ``kBitTiming`` structure.

    Args:
        timing: Timing values to encode.

    Returns:
        Packed little-endian structure bytes.
    """
    return struct.pack(
        BIT_TIMING_FORMAT, timing.prop, timing.seg1, timing.seg2, timing.sjw, timing.brp
    )


def pack_device_mode(mode: DeviceMode, flags: int) -> bytes:
    """Pack a ``kDeviceMode`` structure.

    Args:
        mode: Reset or start mode.
        flags: Device flags to apply.

    Returns:
        Packed little-endian structure bytes.
    """
    return struct.pack(DEVICE_MODE_FORMAT, int(mode), int(flags))


def unpack_capability_classic(data: bytes) -> Capability:
    """Decode a ``kCapabilityClassic`` response.

    Args:
        data: Exactly 40 bytes returned by ``GET_CAPABILITIES``.

    Returns:
        Decoded capability information.

    Raises:
        ValueError: If the buffer length is not 40 bytes.
    """
    values = _unpack_struct(
        CAPABILITY_CLASSIC_FORMAT,
        data,
        "classic capability buffer must be 40 bytes",
        exact=True,
    )
    return Capability(values[0], values[1], TimingLimits(*values[2:]))


def unpack_capability_fd(data: bytes) -> Tuple[Capability, Capability]:
    """Decode a ``kCapabilityFD`` response.

    Args:
        data: Exactly 72 bytes returned by ``GET_CAPABILITIES_FD``.

    Returns:
        A ``(nominal, data)`` tuple. Both entries share the single feature and
        clock fields; each has its own timing limits.

    Raises:
        ValueError: If the buffer length is not 72 bytes.
    """
    values = _unpack_struct(
        CAPABILITY_FD_FORMAT,
        data,
        "FD capability buffer must be 72 bytes",
        exact=True,
    )
    feature, clock = values[0], values[1]
    nominal = Capability(feature, clock, TimingLimits(*values[2:10]))
    data_phase = Capability(feature, clock, TimingLimits(*values[10:18]))
    return nominal, data_phase


def unpack_device_version(data: bytes) -> DeviceVersion:
    """Decode a ``kDeviceVersion`` response.

    Args:
        data: Exactly 12 bytes returned by ``GET_DEVICE_VERSION``.

    Returns:
        Decoded version information.

    Raises:
        ValueError: If the buffer length is not 12 bytes.
    """
    return DeviceVersion(
        *_unpack_struct(
            DEVICE_VERSION_FORMAT,
            data,
            "device version buffer must be 12 bytes",
            exact=True,
        )
    )


def unpack_error_state(data: bytes) -> ErrorState:
    """Decode a ``kErrorState`` response.

    Args:
        data: Exactly 12 bytes returned by ``GET_ERROR_STATE``.

    Returns:
        Decoded error state.

    Raises:
        ValueError: If the buffer length is not 12 bytes.
    """
    return ErrorState(
        *_unpack_struct(
            ERROR_STATE_FORMAT,
            data,
            "error state buffer must be 12 bytes",
            exact=True,
        )
    )


def _decode_string(raw: bytes) -> str:
    """Decode a firmware fixed-width string, stripping NUL padding."""
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def unpack_board_info(data: bytes) -> BoardInfo:
    """Decode a ``kBoardInfo`` response.

    Args:
        data: Exactly 56 bytes returned by ``GET_BOARD_INFO``.

    Returns:
        Decoded board information.

    Raises:
        ValueError: If the buffer length is not 56 bytes.
    """
    mcu_id, mcu_name, board_name, flags = _unpack_struct(
        BOARD_INFO_FORMAT,
        data,
        "board info buffer must be 56 bytes",
        exact=True,
    )
    return BoardInfo(mcu_id, _decode_string(mcu_name), _decode_string(board_name), flags)


def pack_filter(
    operation: FilterOperation,
    can_id: int,
    mask: int,
    index: int = 0,
    dest_channel: int = 0,
) -> bytes:
    """Pack a ``kFilter`` structure.

    Args:
        operation: Filter operation to perform.
        can_id: Acceptance filter value.
        mask: Acceptance mask.
        index: Bridge filter index.
        dest_channel: Bridge destination channel.

    Returns:
        Packed little-endian structure bytes.
    """
    return struct.pack(
        FILTER_FORMAT, int(operation), can_id, mask, index, dest_channel, b"\x00" * 6
    )


def pack_pin_status(operation: PinOperation, pin_id: PinID) -> bytes:
    """Pack a ``kPinStatus`` structure.

    Args:
        operation: Pin operation to perform.
        pin_id: Target pin identifier.

    Returns:
        Packed little-endian structure bytes.
    """
    return struct.pack(PIN_STATUS_FORMAT, int(operation), int(pin_id), 0, 0)


def unpack_pin_status(data: bytes) -> PinStatusFlag:
    """Decode the 2-byte pin status response.

    Args:
        data: Two bytes returned by ``GET_PIN_STATUS``.

    Returns:
        Decoded pin status flags.

    Raises:
        ValueError: If the buffer length is not 2 bytes.
    """
    if len(data) != 2:
        raise ValueError("pin status buffer must be 2 bytes")
    return PinStatusFlag(struct.unpack("<H", data)[0])


def pack_tx_frame(message: can.Message, marker: int = 0) -> bytes:
    """Pack a host-to-device ``kTxFrameElmue`` message.

    Args:
        message: CAN message to transmit.
        marker: Echo marker, 0 to disable echoes or 1..255 to request one.

    Returns:
        Complete USB message including the 2-byte header.

    Raises:
        ValueError: If the marker is outside 0..255.
    """
    if not 0 <= marker <= 0xFF:
        raise ValueError("marker must be in 0..255")

    flags = FrameFlags.NONE
    if message.is_fd:
        flags |= FrameFlags.FDF
    if message.bitrate_switch:
        flags |= FrameFlags.BRS
    if message.error_state_indicator:
        flags |= FrameFlags.ESI

    can_id = message.arbitration_id
    if message.is_extended_id:
        can_id |= int(CanIdFlags.EXTENDED)
    if message.is_remote_frame:
        can_id |= int(CanIdFlags.RTR)

    if message.is_remote_frame:
        payload = bytes([message.dlc])
    else:
        payload = bytes(message.data)

    return (
        struct.pack(
            TX_FRAME_FORMAT,
            8 + len(payload),
            int(MessageType.TX_FRAME),
            int(flags),
            can_id,
            marker,
        )
        + payload
    )


def _decode_rx_frame(
    data: bytes, timestamps_enabled: bool, channel: int
) -> Tuple[can.Message, Optional[int]]:
    """Decode an RX frame and return its raw microsecond timestamp separately."""
    fmt = RX_FRAME_TIMESTAMP_FORMAT if timestamps_enabled else RX_FRAME_FORMAT
    fields = _unpack_struct(fmt, data, "RX frame is shorter than its fixed header")
    _size, _msg_type, flags, can_id = fields[:4]
    timestamp_us = fields[4] if timestamps_enabled else None
    base_size = struct.calcsize(fmt)
    payload = data[base_size:]
    is_extended = bool(can_id & int(CanIdFlags.EXTENDED))
    is_remote = bool(can_id & int(CanIdFlags.RTR))
    arbitration_id = can_id & (
        int(CanIdFlags.MASK_29) if is_extended else int(CanIdFlags.MASK_11)
    )

    if is_remote:
        dlc = payload[0] if payload else 0
        frame_data = b""
    else:
        frame_data = payload
        dlc = len(payload)

    message = can.Message(
        timestamp=0.0,
        arbitration_id=arbitration_id,
        is_extended_id=is_extended,
        is_remote_frame=is_remote,
        is_fd=bool(flags & int(FrameFlags.FDF)),
        bitrate_switch=bool(flags & int(FrameFlags.BRS)),
        error_state_indicator=bool(flags & int(FrameFlags.ESI)),
        is_rx=True,
        dlc=dlc,
        data=frame_data,
        channel=channel,
    )
    return message, timestamp_us


def parse_rx_frame(
    data: bytes, timestamps_enabled: bool, channel: int = 0
) -> can.Message:
    """Decode a device-to-host ``kRxFrameElmue`` message.

    Args:
        data: Complete message including the 2-byte header.
        timestamps_enabled: Whether the device includes a 4-byte timestamp.
        channel: Channel index to attach to the returned message.

    Returns:
        Decoded CAN message with its timestamp expressed in seconds.

    Raises:
        ValueError: If the message is shorter than its fixed header.
    """
    message, timestamp_us = _decode_rx_frame(data, timestamps_enabled, channel)
    if timestamp_us is not None:
        message.timestamp = float(timestamp_us) / 1_000_000.0
    return message


def parse_echo_frame(data: bytes, timestamps_enabled: bool) -> Tuple[int, Optional[int]]:
    """Decode a ``kTxEchoElmue`` message.

    Args:
        data: Complete message including the 2-byte header.
        timestamps_enabled: Whether the device includes a 4-byte timestamp.

    Returns:
        A ``(marker, timestamp_us)`` tuple; timestamp is ``None`` when disabled.

    Raises:
        ValueError: If the message is too short.
    """
    if timestamps_enabled:
        fields = _unpack_struct(
            TX_ECHO_TIMESTAMP_FORMAT, data, "TX echo frame is too short"
        )
        return fields[2], fields[3]
    fields = _unpack_struct(TX_ECHO_FORMAT, data, "TX echo frame is too short")
    return fields[2], None


def parse_error_frame(data: bytes, timestamps_enabled: bool) -> Tuple[int, bytes, Optional[int]]:
    """Decode an ``kErrorElmue`` message.

    Args:
        data: Complete message including the 2-byte header.
        timestamps_enabled: Whether the device includes a 4-byte timestamp.

    Returns:
        A ``(err_id, err_data, timestamp_us)`` tuple.

    Raises:
        ValueError: If the message is too short.
    """
    if timestamps_enabled:
        fields = _unpack_struct(
            ERROR_FRAME_TIMESTAMP_FORMAT, data, "error frame is too short"
        )
        return fields[2], fields[3], fields[4]
    fields = _unpack_struct(ERROR_FRAME_FORMAT, data, "error frame is too short")
    return fields[2], fields[3], None


def parse_busload_frame(data: bytes) -> int:
    """Decode a ``kBusloadElmue`` message.

    Args:
        data: Complete message including the 2-byte header.

    Returns:
        Bus load percentage.

    Raises:
        ValueError: If the message is too short.
    """
    return _unpack_struct(BUSLOAD_FORMAT, data, "busload frame is too short")[2]


def parse_string_frame(data: bytes) -> str:
    """Decode a ``kStringElmue`` debug message.

    Args:
        data: Complete message including the 2-byte header.

    Returns:
        The ASCII payload decoded as UTF-8 with replacement for invalid bytes.
    """
    return bytes(data[2:]).decode("utf-8", errors="replace")


def parse_blob_header(data: bytes) -> Tuple[int, MessageType]:
    """Decode the two-byte ``kBlob`` header that follows a blob message header.

    Args:
        data: Complete blob message including the common 2-byte header.

    Returns:
        A ``(frame_count, msg_type)`` tuple.

    Raises:
        ValueError: If the blob header is missing or the message type is unknown.
    """
    if len(data) < 4:
        raise ValueError("blob message is too short")
    frame_count = data[2]
    msg_type = MessageType(data[3])
    return frame_count, msg_type


def _iter_blob_subframes(data: bytes, frame_count: int) -> Iterator[bytes]:
    """Yield blob subframes when the header has already been decoded."""
    if not data or data[0] != len(data):
        raise ValueError("blob declared size does not match the message length")
    offset = 4
    for _ in range(frame_count):
        if offset + 2 > len(data):
            raise ValueError("blob subframe header is truncated")
        size = data[offset]
        if size < 2 or offset + size > len(data):
            raise ValueError(f"invalid blob subframe size {size} at offset {offset}")
        yield bytes(data[offset : offset + size])
        offset += size


def iter_blob_frames(data: bytes) -> Iterator[bytes]:
    """Iterate over the subframes packed into a blob message.

    Args:
        data: Complete blob message including the common 2-byte header.

    Yields:
        Each complete subframe, including its own 2-byte header.

    Raises:
        ValueError: If the blob header or any subframe size is invalid.
    """
    frame_count, _msg_type = parse_blob_header(data)
    yield from _iter_blob_subframes(data, frame_count)
