"""``python-can`` bus implementation for Elmü CANable 2.5 adapters."""

from __future__ import annotations

import logging
import struct
import threading
import time
from collections import deque
from copy import copy
from typing import Any, Deque, Dict, List, Optional, Tuple

import can

from .bit_timing import select_classic_timing, select_fd_timing
from .device import (
    CanableUsbDevice,
    DeviceSelector,
    UsbDeviceInfo,
    detect_available_configs,
    parse_channel,
    select_device,
)
from .exceptions import CanableInitializationError, CanableOperationError
from .protocol import (
    HOST_FORMAT_MAGIC,
    MAX_BLOB_SIZE,
    BoardInfo,
    CanIdFlags,
    Capability,
    DeviceFlags,
    DeviceMode,
    DeviceVersion,
    FilterOperation,
    MessageType,
    PinID,
    PinOperation,
    PID,
    Request,
    VID,
    ErrorState,
    _decode_rx_frame,
    _iter_blob_subframes,
    pack_bit_timing,
    pack_device_mode,
    pack_filter,
    pack_pin_status,
    pack_tx_frame,
    parse_blob_header,
    parse_busload_frame,
    parse_echo_frame,
    parse_error_frame,
    parse_string_frame,
    unpack_board_info,
    unpack_capability_classic,
    unpack_capability_fd,
    unpack_device_version,
    unpack_error_state,
    unpack_pin_status,
)

__all__ = ["CanableBus"]

LOG = logging.getLogger(__name__)

_TIMESTAMP_ROLLOVER_US = 3_600_000_000
_TIMESTAMP_SLOP_US = 1_000_000
_ECHO_TTL_S = 10.0
_BUFFER_COMPACT_THRESHOLD = 4096


class CanableBus(can.BusABC):
    """python-can interface for a single Elmü CANable 2.5 channel.

    The driver supports only the Elmü 2.5 USB frame protocol; legacy 80-byte
    Candlelight frames are never sent or accepted. Construct the bus through
    python-can with ``interface="canable"`` and a ``SERIAL:INDEX`` channel.

    Args:
        channel: ``SERIAL:INDEX`` channel string. A plain serial implies index 0.
        can_filters: Python-can receive filters.
        bitrate: Nominal CAN bitrate in bit/s.
        sample_point: Nominal sample point in percent.
        fd: Enable CAN FD mode.
        data_bitrate: CAN FD data-phase bitrate in bit/s; defaults to 2 Mbit/s
            when ``fd`` is enabled.
        data_sample_point: CAN FD data-phase sample point in percent.
        listen_only: Enable silent mode.
        loop_back: Enable controller loopback.
        one_shot: Disable automatic retransmission.
        timestamps: Request hardware timestamps from the firmware.
        receive_own_messages: Request an echo marker and return sent frames via
            :meth:`recv`.
        send_blobs: Enable firmware USB blob optimization for received frames.
        serial_number: Select a device by USB serial number.
        product: Select a device by case-insensitive product substring.
        manufacturer: Select a device by case-insensitive manufacturer substring.
        vid: Vendor ID selector; defaults to the CANable VID.
        pid: Product ID selector; defaults to the CANable PID.
        bus: USB bus number selector.
        address: USB device address selector.
        index: CAN channel index; overrides the channel suffix.
        ignore_config: Accepted for python-can compatibility; not used.
        timeout: USB transfer timeout in seconds.
        usb_backend: Optional pyusb backend override for tests.
        **kwargs: Additional python-can configuration keys are ignored with a
            debug log message.

    Raises:
        CanableInitializationError: If no device matches or firmware validation
            fails.
        ValueError: If constructor arguments are inconsistent.
    """

    channel_info = "canable"

    def __init__(
        self,
        channel: Any = None,
        can_filters: Optional[Any] = None,
        bitrate: int = 500_000,
        sample_point: float = 87.5,
        fd: bool = False,
        data_bitrate: Optional[int] = None,
        data_sample_point: float = 87.5,
        listen_only: bool = False,
        loop_back: bool = False,
        one_shot: bool = False,
        timestamps: bool = False,
        receive_own_messages: bool = False,
        send_blobs: bool = False,
        serial_number: Optional[str] = None,
        product: Optional[str] = None,
        manufacturer: Optional[str] = None,
        vid: int = VID,
        pid: int = PID,
        bus: Optional[int] = None,
        address: Optional[int] = None,
        index: Optional[int] = None,
        ignore_config: bool = True,
        timeout: float = 1.0,
        usb_backend: Any = None,
        **kwargs: Any,
    ) -> None:
        """Initialize device selection state and open the selected adapter."""
        self._device: Optional[CanableUsbDevice] = None
        self._buffer = bytearray()
        self._buffer_offset = 0
        self._message_queue: Deque[can.Message] = deque()
        self._pending_echoes: Dict[int, Tuple[float, can.Message]] = {}
        self._next_marker = 1
        self._filters: Optional[List[dict[str, Any]]] = None
        self._lock = threading.RLock()
        self._read_lock = threading.Lock()
        self._channel_started = False
        self._shutdown_done = False
        self._receive_own_messages = bool(receive_own_messages)
        self._timestamps = bool(timestamps)
        self._timestamp_offset_us = 0
        self._last_timestamp_us: Optional[int] = None
        self.error_frames: List[dict[str, Any]] = []
        self.busload_percent: Optional[int] = None

        if kwargs:
            LOG.debug("ignoring unsupported python-can options: %s", sorted(kwargs))
        if ignore_config:
            LOG.debug("ignore_config requested; explicit constructor values are used")

        channel_serial, channel_index = parse_channel(channel)
        if index is not None:
            if channel_index not in (0, int(index)):
                raise ValueError("channel index and index argument disagree")
            channel_index = int(index)
        if serial_number is None:
            serial_number = channel_serial
        elif channel_serial and serial_number.casefold() != channel_serial.casefold():
            raise ValueError("serial_number and channel serial disagree")

        selector = DeviceSelector(
            serial=serial_number,
            product=product,
            manufacturer=manufacturer,
            vid=int(vid),
            pid=int(pid),
            bus=None if bus is None else int(bus),
            address=None if address is None else int(address),
            index=channel_index,
        )
        info: UsbDeviceInfo = select_device(selector, backend=usb_backend)

        self._bitrate = int(bitrate)
        self._sample_point = float(sample_point)
        self._fd = bool(fd or data_bitrate is not None)
        self._data_bitrate = int(data_bitrate) if data_bitrate is not None else 2_000_000
        self._data_sample_point = float(data_sample_point)
        self._listen_only = bool(listen_only)
        self._loop_back = bool(loop_back)
        self._one_shot = bool(one_shot)
        self._send_blobs = bool(send_blobs)
        self._capabilities: Optional[Capability] = None
        self._capabilities_fd: Optional[tuple[Capability, Capability]] = None
        self._version: Optional[DeviceVersion] = None
        self._board_info: Optional[BoardInfo] = None
        self._nominal_timing: Optional[Any] = None
        self._data_timing: Optional[Any] = None
        self._device_flags = self._compose_device_flags()

        self._device = CanableUsbDevice(
            info, channel=channel_index, timeout_ms=max(1, int(timeout * 1000))
        )
        self.channel = channel
        self.channel_info = self._device.channel_info
        super().__init__(channel=channel, can_filters=can_filters)
        try:
            self._initialize_device()
        except Exception:
            self.shutdown()
            raise

    @staticmethod
    def _detect_available_configs() -> List[dict[str, Any]]:
        """Return python-can configurations for connected CANable devices.

        Returns:
            Configuration dictionaries containing ``interface`` and ``channel``.
        """
        return detect_available_configs()

    @property
    def capabilities(self) -> Optional[Capability]:
        """Classic CAN capabilities reported by the firmware."""
        return self._capabilities

    @property
    def capabilities_fd(self) -> Optional[tuple[Capability, Capability]]:
        """Nominal and data-phase CAN FD capabilities reported by the firmware."""
        return self._capabilities_fd

    @property
    def error_frame_count(self) -> int:
        """Number of firmware error reports received since opening the bus."""
        return len(self.error_frames)

    @property
    def kernel_driver_restored(self) -> bool:
        """Return whether the kernel driver state was restored on shutdown.

        Returns ``True`` when no driver was detached, or when the previously
        detached driver was reattached successfully.
        """
        if self._device is None:
            return False
        return (
            not self._device.kernel_driver_detached
            or self._device.reattached_kernel_driver
        )

    def _compose_device_flags(self) -> DeviceFlags:
        """Build device-mode flags from constructor options."""
        flags = DeviceFlags.NONE
        if self._listen_only:
            flags |= DeviceFlags.LISTEN_ONLY
        if self._loop_back:
            flags |= DeviceFlags.LOOPBACK
        if self._one_shot:
            flags |= DeviceFlags.ONE_SHOT
        if self._timestamps:
            flags |= DeviceFlags.TIMESTAMP
        if self._fd:
            flags |= DeviceFlags.CAN_FD | DeviceFlags.BIT_TIMING_FD
        if self._send_blobs:
            flags |= DeviceFlags.SEND_USB_BLOBS
        return flags

    def _initialize_device(self) -> None:
        """Open USB, validate firmware identity, and start the CAN channel.

        Returns:
            ``None``.

        Raises:
            CanableInitializationError: If firmware identity or capabilities are
                not compatible with this driver.
        """
        device = self._require_device()
        try:
            device.open()
            device.ctrl_out(
                Request.SET_HOST_FORMAT, struct.pack("<I", HOST_FORMAT_MAGIC)
            )
            device.ctrl_out(
                Request.SET_DEVICE_MODE,
                pack_device_mode(DeviceMode.RESET, DeviceFlags.PROTOCOL_ELMUE),
            )

            self._version = unpack_device_version(
                device.ctrl_in(Request.GET_DEVICE_VERSION, 12)
            )
            self._capabilities = unpack_capability_classic(
                device.ctrl_in(Request.GET_CAPABILITIES, 40)
            )
            try:
                self._capabilities_fd = unpack_capability_fd(
                    device.ctrl_in(Request.GET_CAPABILITIES_FD, 72)
                )
            except (CanableOperationError, ValueError) as exc:
                LOG.debug("device did not return FD capabilities: %s", exc)
                self._capabilities_fd = None
            self._board_info = unpack_board_info(
                device.ctrl_in(Request.GET_BOARD_INFO, 56)
            )

            if not self._board_info.board_name.casefold().startswith("canable"):
                raise CanableInitializationError(
                    "firmware board name "
                    f"{self._board_info.board_name!r} is not a CanableDevBoard"
                )
            if not self._capabilities.feature & int(DeviceFlags.PROTOCOL_ELMUE):
                raise CanableInitializationError(
                    "device does not support the Elmü 2.5 protocol"
                )
            if self._capabilities.feature & int(DeviceFlags.GET_ERROR_STATE):
                self._device_flags |= DeviceFlags.GET_ERROR_STATE

            self._compute_timings()
            self._configure_channel(reset=False)
        except CanableInitializationError:
            raise
        except Exception as exc:
            raise CanableInitializationError(f"cannot initialize CANable device: {exc}") from exc

    def _select_timings(
        self,
        bitrate: Optional[int] = None,
        sample_point: Optional[float] = None,
        data_bitrate: Optional[int] = None,
        data_sample_point: Optional[float] = None,
    ) -> Tuple[Any, Optional[Any]]:
        """Compute timings from settings without mutating the bus.

        Args:
            bitrate: Optional nominal bitrate override.
            sample_point: Optional nominal sample-point override.
            data_bitrate: Optional FD data-bitrate override.
            data_sample_point: Optional FD data sample-point override.

        Returns:
            A ``(nominal, data)`` timing pair; ``data`` is ``None`` in classic mode.

        Raises:
            CanableInitializationError: If no compatible timing is available.
        """
        nominal_rate = self._bitrate if bitrate is None else int(bitrate)
        nominal_point = self._sample_point if sample_point is None else float(sample_point)
        data_rate = self._data_bitrate if data_bitrate is None else int(data_bitrate)
        data_point = (
            self._data_sample_point
            if data_sample_point is None
            else float(data_sample_point)
        )
        capability = self._require_capability()
        try:
            if self._fd:
                if self._capabilities_fd is None:
                    raise CanableInitializationError("device does not support CAN FD")
                nominal, data = self._capabilities_fd
                return select_fd_timing(
                    nominal_rate, nominal_point, data_rate, data_point, nominal, data
                )
            return (
                select_classic_timing(nominal_rate, nominal_point, capability),
                None,
            )
        except ValueError as exc:
            raise CanableInitializationError(str(exc)) from exc

    def _compute_timings(self) -> None:
        """Cache firmware timing values computed from the current settings."""
        self._nominal_timing, self._data_timing = self._select_timings()

    def _apply_settings(
        self,
        *,
        bitrate: Optional[int] = None,
        sample_point: Optional[float] = None,
        data_bitrate: Optional[int] = None,
        data_sample_point: Optional[float] = None,
    ) -> None:
        """Compute, apply, and activate new timing settings atomically.

        Args:
            bitrate: Optional nominal bitrate override.
            sample_point: Optional nominal sample-point override.
            data_bitrate: Optional FD data-bitrate override.
            data_sample_point: Optional FD data sample-point override.

        Raises:
            CanableOperationError: If timing computation or activation fails.
        """
        try:
            nominal, data = self._select_timings(
                bitrate, sample_point, data_bitrate, data_sample_point
            )
        except CanableInitializationError as exc:
            raise CanableOperationError(str(exc)) from exc

        previous = (
            self._bitrate,
            self._sample_point,
            self._data_bitrate,
            self._data_sample_point,
            self._nominal_timing,
            self._data_timing,
        )
        if bitrate is not None:
            self._bitrate = int(bitrate)
        if sample_point is not None:
            self._sample_point = float(sample_point)
        if data_bitrate is not None:
            self._data_bitrate = int(data_bitrate)
        if data_sample_point is not None:
            self._data_sample_point = float(data_sample_point)
        self._nominal_timing, self._data_timing = nominal, data
        try:
            self._restart_channel()
        except Exception:
            (
                self._bitrate,
                self._sample_point,
                self._data_bitrate,
                self._data_sample_point,
                self._nominal_timing,
                self._data_timing,
            ) = previous
            raise

    def _send_timing(self) -> None:
        """Send cached nominal and optional data-phase timing to the firmware."""
        device = self._require_device()
        if self._nominal_timing is None:
            raise CanableOperationError("bit timing has not been computed")
        device.ctrl_out(Request.SET_BIT_TIMING, pack_bit_timing(self._nominal_timing))
        if self._fd:
            if self._data_timing is None:
                raise CanableOperationError("FD data timing has not been computed")
            device.ctrl_out(
                Request.SET_BIT_TIMING_FD, pack_bit_timing(self._data_timing)
            )

    def _program_filters(self) -> None:
        """Program host acceptance filters while the channel is closed."""
        device = self._require_device()
        device.ctrl_out(Request.SET_FILTER, pack_filter(FilterOperation.HOST_CLEAR, 0, 0))
        for can_filter in self._filters or []:
            can_id = int(can_filter.get("can_id", 0))
            can_mask = int(can_filter.get("can_mask", can_filter.get("mask", 0)))
            if "extended" in can_filter:
                extended = bool(can_filter["extended"])
                widths = [extended]
            else:
                widths = [False, True]
            for width in widths:
                mask_width = CanIdFlags.MASK_29 if width else CanIdFlags.MASK_11
                operation = (
                    FilterOperation.HOST_PASS_29 if width else FilterOperation.HOST_PASS_11
                )
                device.ctrl_out(
                    Request.SET_FILTER,
                    pack_filter(operation, can_id & mask_width, can_mask & mask_width),
                )

    def _configure_channel(self, *, reset: bool = True) -> None:
        """Program timing and filters, then start the CAN channel.

        Args:
            reset: Send a RESET command first. Initialization already resets the
                device before reading capabilities, so it skips this step.
        """
        device = self._require_device()
        if reset:
            device.ctrl_out(
                Request.SET_DEVICE_MODE,
                pack_device_mode(DeviceMode.RESET, DeviceFlags.PROTOCOL_ELMUE),
            )
        self._channel_started = False
        self._send_timing()
        self._program_filters()
        device.ctrl_out(
            Request.SET_DEVICE_MODE,
            pack_device_mode(
                DeviceMode.START,
                self._device_flags | DeviceFlags.PROTOCOL_ELMUE,
            ),
        )
        self._channel_started = True

    def _restart_channel(self) -> None:
        """Stop, reprogram, and restart the CAN channel."""
        if not self._require_device().is_open:
            return
        self._configure_channel()

    def _require_device(self) -> CanableUsbDevice:
        """Return the open USB transport or raise."""
        if self._device is None:
            raise CanableOperationError("CANable device is not available")
        return self._device

    def _require_capability(self) -> Capability:
        """Return classic capabilities or raise."""
        if self._capabilities is None:
            raise CanableOperationError("device capabilities have not been read")
        return self._capabilities

    def _prune_pending_echoes(self, now: Optional[float] = None) -> None:
        """Drop echo entries older than the configured TTL.

        The caller must hold :attr:`_lock`.

        Args:
            now: Current monotonic time; fetched when omitted.
        """
        current = time.monotonic() if now is None else now
        stale = [
            marker
            for marker, (created, _message) in self._pending_echoes.items()
            if current - created > _ECHO_TTL_S
        ]
        for marker in stale:
            self._pending_echoes.pop(marker, None)

    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
        """Transmit one CAN frame.

        Args:
            msg: Message to transmit.
            timeout: Ignored; USB transfer timeouts are set at construction time.

        Returns:
            ``None``.

        Raises:
            ValueError: If an error frame is passed for transmission.
            CanableOperationError: If the USB write fails.
        """
        if msg.is_error_frame:
            raise ValueError("error frames cannot be sent")
        marker = 0
        if self._receive_own_messages:
            with self._lock:
                now = time.monotonic()
                self._prune_pending_echoes(now)
                marker = self._next_marker
                self._next_marker = marker % 0xFF + 1
                echo = copy(msg)
                echo.is_rx = True
                echo.channel = self.channel
                self._pending_echoes[marker] = (now, echo)
        try:
            self._require_device().write(pack_tx_frame(msg, marker=marker))
        except Exception:
            with self._lock:
                self._pending_echoes.pop(marker, None)
            raise

    def _recv_internal(
        self, timeout: Optional[float]
    ) -> tuple[Optional[can.Message], bool]:
        """Read parsed messages from internal buffers.

        Args:
            timeout: Seconds to wait for a message, or ``None`` to wait forever.

        Returns:
            A ``(message, filtered)`` tuple. ``filtered`` is always ``False`` so
            python-can applies its software filters.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                if self._message_queue:
                    return self._message_queue.popleft(), False

            with self._read_lock:
                with self._lock:
                    if self._message_queue:
                        return self._message_queue.popleft(), False
                remaining_ms = None
                if deadline is not None:
                    remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                    if remaining_ms == 0:
                        return None, False
                chunk = self._require_device().read(
                    MAX_BLOB_SIZE, timeout_ms=remaining_ms
                )
                if chunk:
                    with self._lock:
                        self._buffer.extend(chunk)
                        self._process_buffer()

    def _process_buffer(self) -> None:
        """Parse complete Elmü messages from the USB receive buffer.

        Returns:
            ``None``.
        """
        buffer = self._buffer
        offset = self._buffer_offset
        available = len(buffer) - offset
        while available >= 2:
            size = buffer[offset]
            if size < 2:
                LOG.warning("discarding invalid message size %d", size)
                offset += 1
                available -= 1
                continue
            if available < size:
                break
            raw = bytes(buffer[offset : offset + size])
            offset += size
            available -= size
            self._handle_message(raw)

        if offset == len(buffer):
            buffer.clear()
            offset = 0
        elif offset >= _BUFFER_COMPACT_THRESHOLD:
            del buffer[:offset]
            offset = 0
        self._buffer_offset = offset

    def _handle_message(self, raw: bytes, depth: int = 0) -> None:
        """Dispatch one complete Elmü message.

        Args:
            raw: Complete message including its two-byte header.
            depth: Current blob expansion depth, used to bound recursion.
        """
        if depth > 2:
            LOG.warning("ignoring nested Elmü blob at depth %d", depth)
            return
        try:
            msg_type = MessageType(raw[1])
        except ValueError:
            LOG.warning("ignoring unknown Elmü message type 0x%02X", raw[1])
            return

        try:
            if msg_type == MessageType.RX_FRAME:
                message, timestamp_us = _decode_rx_frame(
                    raw, self._timestamps, self.channel
                )
                if timestamp_us is not None:
                    message.timestamp = self._adjust_timestamp(timestamp_us)
                self._message_queue.append(message)
            elif msg_type == MessageType.TX_ECHO:
                marker, timestamp_us = parse_echo_frame(raw, self._timestamps)
                self._handle_echo(marker, timestamp_us)
            elif msg_type == MessageType.ERROR:
                err_id, err_data, timestamp_us = parse_error_frame(raw, self._timestamps)
                self.error_frames.append(
                    {
                        "err_id": err_id,
                        "data": err_data,
                        "timestamp_us": timestamp_us,
                    }
                )
                LOG.debug("CANable error report 0x%08X: %s", err_id, err_data.hex())
            elif msg_type == MessageType.STRING:
                LOG.debug("CANable firmware: %s", parse_string_frame(raw))
            elif msg_type == MessageType.BUSLOAD:
                self.busload_percent = parse_busload_frame(raw)
            elif msg_type in (MessageType.RX_BLOB, MessageType.TX_BLOB):
                self._handle_blob(raw, msg_type, depth)
            elif msg_type == MessageType.TX_FRAME:
                LOG.debug("ignoring TX frame received from device")
        except ValueError as exc:
            LOG.warning("malformed Elmü %s message: %s", msg_type.name, exc)

    def _handle_blob(self, raw: bytes, msg_type: MessageType, depth: int) -> None:
        """Expand a blob message into its subframes.

        Args:
            raw: Complete blob message.
            msg_type: Blob message type.
            depth: Current blob expansion depth.
        """
        try:
            frame_count, inner_type = parse_blob_header(raw)
        except ValueError as exc:
            LOG.warning("malformed blob header: %s", exc)
            return
        LOG.debug("received blob with %d frames of type %s", frame_count, inner_type.name)
        try:
            for subframe in _iter_blob_subframes(raw, frame_count):
                self._handle_message(subframe, depth + 1)
        except ValueError as exc:
            LOG.warning("malformed blob payload: %s", exc)

    def _handle_echo(self, marker: int, timestamp_us: Optional[int]) -> None:
        """Correlate a TX echo marker with a previously sent message.

        Args:
            marker: Echo marker reported by firmware.
            timestamp_us: Optional hardware timestamp in microseconds.
        """
        entry = self._pending_echoes.pop(marker, None)
        if entry is None:
            LOG.debug("ignoring unknown TX echo marker %d", marker)
            return
        _created, message = entry
        if timestamp_us is not None:
            message.timestamp = self._adjust_timestamp(timestamp_us)
        self._message_queue.append(message)

    def _adjust_timestamp(self, timestamp_us: int) -> float:
        """Convert a firmware microsecond timestamp to monotonic seconds.

        Args:
            timestamp_us: Firmware timestamp in microseconds.

        Returns:
            Timestamp in seconds with one-hour rollover compensation.
        """
        if (
            self._last_timestamp_us is not None
            and timestamp_us + _TIMESTAMP_SLOP_US < self._last_timestamp_us
        ):
            self._timestamp_offset_us += _TIMESTAMP_ROLLOVER_US
        self._last_timestamp_us = timestamp_us
        return (self._timestamp_offset_us + timestamp_us) / 1_000_000.0

    def set_filters(self, filters: Optional[Any] = None) -> None:
        """Store and, when open, program python-can receive filters.

        Args:
            filters: Iterable of python-can filter dictionaries, or ``None``.

        Returns:
            ``None``.
        """
        self._filters = list(filters) if filters else None
        if self._device is not None and self._device.is_open and self._channel_started:
            self._restart_channel()

    def clear_filters(self) -> None:
        """Remove all hardware and software receive filters."""
        self.set_filters(None)

    def set_bitrate(
        self, bitrate: int, sample_point: Optional[float] = None
    ) -> None:
        """Change the nominal bitrate while the device is open.

        Args:
            bitrate: New nominal bitrate in bit/s.
            sample_point: New sample point in percent; keeps the current value
                when omitted.

        Returns:
            ``None``.

        Raises:
            ValueError: If the bitrate is not positive.
            CanableOperationError: If no compatible timing exists.
        """
        if bitrate <= 0:
            raise ValueError("bitrate must be positive")
        self._apply_settings(bitrate=bitrate, sample_point=sample_point)

    def set_data_bitrate(
        self, data_bitrate: int, sample_point: Optional[float] = None
    ) -> None:
        """Change the CAN FD data-phase bitrate while the device is open.

        Args:
            data_bitrate: New data-phase bitrate in bit/s.
            sample_point: New data sample point in percent.

        Returns:
            ``None``.

        Raises:
            CanableOperationError: If the bus is not in FD mode or timing fails.
        """
        if not self._fd:
            raise CanableOperationError("CAN FD is not enabled")
        if data_bitrate <= 0:
            raise ValueError("data_bitrate must be positive")
        self._apply_settings(data_bitrate=data_bitrate, data_sample_point=sample_point)

    def get_capabilities(self) -> Capability:
        """Return the cached classic capability record.

        Returns:
            Decoded classic capabilities.
        """
        return self._require_capability()

    def get_capabilities_fd(self) -> tuple[Capability, Capability]:
        """Return cached nominal and data-phase FD capability records.

        Returns:
            A ``(nominal, data)`` tuple.

        Raises:
            CanableOperationError: If the device has no FD capabilities.
        """
        if self._capabilities_fd is None:
            raise CanableOperationError("device does not support CAN FD")
        return self._capabilities_fd

    def get_device_version(self) -> DeviceVersion:
        """Return the cached firmware version record."""
        if self._version is None:
            raise CanableOperationError("device version has not been read")
        return self._version

    def get_board_info(self) -> BoardInfo:
        """Return the cached board information record."""
        if self._board_info is None:
            raise CanableOperationError("board information has not been read")
        return self._board_info

    def get_error_state(self) -> ErrorState:
        """Poll the deprecated firmware error-state register.

        Returns:
            Decoded ``kErrorState`` record.
        """
        return unpack_error_state(self._require_device().ctrl_in(Request.GET_ERROR_STATE, 12))

    def get_last_error(self) -> int:
        """Return the firmware's last feedback code.

        Returns:
            Numeric feedback value; ``2`` means success.
        """
        raw = self._require_device().ctrl_in(Request.GET_LAST_ERROR, 1)
        return raw[0] if raw else 0

    def get_pin_status(self, pin_id: PinID = PinID.BOOT0) -> Any:
        """Read the status of a firmware-controlled pin.

        Args:
            pin_id: Pin identifier to query.

        Returns:
            Decoded pin status flags.
        """
        raw = self._require_device().ctrl_in(
            Request.GET_PIN_STATUS, 2, value=int(pin_id)
        )
        return unpack_pin_status(raw)

    def identify(self, enable: bool = True) -> None:
        """Enable or disable firmware LED identification mode.

        Args:
            enable: ``True`` to blink LEDs, ``False`` to stop.
        """
        self._require_device().ctrl_out(
            Request.IDENTIFY, struct.pack("<I", 1 if enable else 0)
        )

    def set_busload_report(self, interval_100ms: int) -> None:
        """Configure periodic bus-load reports.

        Args:
            interval_100ms: Report interval in 100 ms units; ``0`` disables reports.

        Raises:
            ValueError: If the interval is outside 0..255.
        """
        if not 0 <= interval_100ms <= 255:
            raise ValueError("interval must be between 0 and 255")
        self._require_device().ctrl_out(
            Request.SET_BUSLOAD_REPORT, bytes([interval_100ms])
        )

    def disable_boot0(self) -> None:
        """Disable the STM32 BOOT0 pin in option bytes.

        Returns:
            ``None``.

        Warning:
            This permanently changes option bytes. It is never called
            automatically and is deliberately not exercised by tests.
        """
        self._require_device().ctrl_out(
            Request.SET_PIN_STATUS,
            pack_pin_status(PinOperation.DISABLE, PinID.BOOT0),
        )

    def shutdown(self) -> None:
        """Stop the channel, release USB, and reattach the kernel driver.

        Returns:
            ``None``.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        if self._device is not None and self._device.is_open:
            try:
                self._device.ctrl_out(
                    Request.SET_DEVICE_MODE,
                    pack_device_mode(DeviceMode.RESET, DeviceFlags.PROTOCOL_ELMUE),
                )
            except Exception as exc:
                LOG.warning("could not stop CANable channel during shutdown: %s", exc)
            self._device.close()
        self._channel_started = False
        super().shutdown()
