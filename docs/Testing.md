# Testing

The test suite is split into two independent parts:

- **Unit tests** (`tests/unit/`) are deterministic, hardware-free, and run by default.
- **Live-hardware tests** (`tests/hardware/`) are opt-in and require the two connected
  adapters and USB access.

There is no shared root `conftest.py` and no cross-imports between the suites.

## Quick reference

```sh
# Unit suite (default; safe everywhere)
pytest
pytest tests/unit -q

# Live hardware suite (explicit path + gate)
CANABLE_HW=1 pytest tests/hardware -q
CANABLE_HW=1 pytest tests/hardware/test_hw_reference.py -q
CANABLE_HW=1 pytest tests/hardware -m stress -q
```

`pyproject.toml` sets `testpaths = ["tests/unit"]`, so a bare `pytest` never collects the
hardware suite. Hardware tests also skip themselves unless `CANABLE_HW=1` is set.

## Dependencies

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[test,hardware]"
```

The unit suite needs only `python-can` and `pytest`. The hardware suite additionally needs
`python-can-candle` for the reference adapter and raw USB access via `pyusb`.

## Unit tests

Location: `tests/unit/`. Own `conftest.py` provides an injectable fake USB device; no real
USB calls occur.

| File | Coverage |
| --- | --- |
| `test_protocol.py` | Packed struct sizes/fields, feedback codes, device flags, CAN ID/frame flags, TX/RX/echo/error/string/busload/blob frames, malformed sizes, timestamp rollover. |
| `test_bit_timing.py` | `BitTiming`/`BitTimingFd` selection for 48 MHz and 160 MHz, classic and FD, capability limits, SJW bounds, deterministic tie-breaking, invalid candidates. |
| `test_device_selection.py` | `SERIAL:INDEX` parsing, plain serial, selector precedence, invalid index, matching by serial/product/manufacturer/vid/pid/bus/address. |
| `test_bus_unit.py` | Initialization call order, last-error checks after every OUT request, flag composition, runtime bitrate state machine, filter conversion, receive buffer/blob handling, idempotent shutdown. |
| `test_detection.py` | `CanableBus._detect_available_configs()` and `can.detect_available_configs("canable")` channel format via monkeypatch. |

Unit-test vectors come from the Elmü Python sample in the firmware mirror, not from guesses.
In particular, `kCapabilityFD` is asserted to be 72 bytes and `FBK_Success` is 2.

## Live-hardware tests

Location: `tests/hardware/`. Own `conftest.py` handles environment gating, device selection,
and cleanup. Tests drive the driver **only** through the python-can plugin interface:

```python
bus = can.Bus(
    interface="canable",
    channel=f"{CANABLE_SERIAL}:0",
    bitrate=CANABLE_BITRATE,
    ignore_config=True,
)
```

Low-level public methods such as `bus.get_board_info()` are called on that plugin-created
object. Direct `CanableBus(...)` construction is never used in hardware tests.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CANABLE_HW` | unset | Must be `1` to run any hardware test. |
| `CANABLE_SERIAL` | `209D3889594550052` | CanableDevBoard FDCAN under test. |
| `CANDLE_SERIAL` | `003500465446571320343032` | Reference adapter for `interface="candle"`. |
| `CANABLE_BITRATE` | `500000` | Nominal bitrate for both adapters. |
| `CANABLE_TIMEOUT` | `2.0` | Receive timeout in seconds. |

Both adapters are `1d50:606f`, so selection is **by serial only**. Never rely on `can0` or
`can1` names: they have swapped after reflashing. Confirm with sysfs if in doubt:

```sh
ls -l /sys/class/net | grep can
ip -d link show can0
ip -d link show can1
for p in /sys/bus/usb/devices/*; do
  [ -r "$p/serial" ] && echo "$p $(cat "$p/product") $(cat "$p/serial")"
done
```

### Inventory

| File | Purpose |
| --- | --- |
| `test_hw_identity.py` | `can.detect_available_configs("canable")` lists the board; `get_device_version()`, `get_capabilities()`, `get_capabilities_fd()`, `get_board_info()`; board name starts with `CanableDevBoard`; capability flag is present; clock is 160 MHz. |
| `test_hw_loopback.py` | Classic send/receive via plugin with `receive_own_messages=True`; 11-bit and 29-bit IDs; DLC 0 and 8; remote frame; zero error frames. |
| `test_hw_reference.py` | Adapter↔adapter both directions using `interface="candle"` for the reference. Receiver thread starts before sender, ignores/counts error frames, asserts ID/payload match. A `stress` marker runs 20 alternating iterations with unique IDs. |
| `test_hw_runtime.py` | Bitrate changes 500000 → 250000 → 500000 with frame checks at each step; acceptance filters; CAN FD send/receive if capability and timing allow; read-only pin status. |
| `test_hw_shutdown.py` | Idempotent shutdown and clean kernel-driver state via `bus.kernel_driver_restored`. |

### Reference adapter

`python-can-candle` is used as the known-working reference:

```python
reference = can.Bus(
    interface="candle",
    channel=f"{CANDLE_SERIAL}:0",
    bitrate=CANABLE_BITRATE,
    ignore_config=True,
)
```

Pass `bitrate` explicitly: candle defaults to 1 MHz. Select by serial because both adapters
share VID/PID. A SocketCAN variant is not part of this suite; the earlier SocketCAN baseline
remains documented in the board bring-up repository.

### Driver contention and permissions

- Hardware tests must run serially (no `pytest-xdist`). Opening either plugin detaches
  `gs_usb`; the corresponding SocketCAN interface disappears while owned.
- Do not mix SocketCAN and plugin access to the same device in one test.
- Install `udev/99-canable.rules` and replug, or run with `sudo`. Tests skip with an
  actionable message on `LIBUSB_ERROR_ACCESS`.
- Plain `lsusb` prints `OpenMoko, Inc. Geschwister Schneider CAN adapter` for both devices;
  that name comes from the host USB ID database, not the firmware.

### Unsafe operations

`disable_boot0()` changes STM32 option bytes. It is implemented in the library but has
**no test code** in this repository. Do not call it during test runs.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Hardware tests all skip | Set `CANABLE_HW=1` and verify both serials. |
| `LIBUSB_ERROR_ACCESS` | Install the udev rule, replug, or use `sudo`. |
| No frames received | Confirm 500000 bitrate, loopback off, unique IDs, and that `gs_usb` was detached cleanly. |
| Error frames counted | Check bitrate/clock, termination, and CANH/CANL; zero errors is the pass criterion. |
| Reference device not found | Confirm `python-can-candle` is installed and `CANDLE_SERIAL` matches sysfs. |
| `can0`/`can1` missing | Expected while a plugin owns the USB device; use the plugin, not SocketCAN. |
