# Implement Plan: `python-can-canable` Elmü 2.5 Driver

## Goal

Create a Python package in **this repository** that registers a `python-can` plugin named
`canable` and talks directly to **ElmüSoft CANable 2.5 / Candlelight 2.5** firmware over USB
using the **new Elmü 2.5 protocol only**.

This repository replaces the earlier "docs/artifacts only" arrangement. The driver, tests,
and documentation all live here. The board-specific bring-up record remains at
<https://github.com/shreeshan97/CanableDevBoard_FDCAN>.

## Decisions

- Plugin/entry-point name: **`canable` only**.
- Protocol: **Elmü 2.5 protocol only**; no legacy 80-byte Candlelight/gs_usb frame fallback.
- The old FYSETC UCAN adapter is **out of scope** for this plugin. It is used only as the
  reference adapter in live adapter-to-adapter tests, through `python-can-candle`.
- Live-hardware tests use the **python-can plugin interface only**
  (`can.Bus(interface="canable", ...)`, `can.detect_available_configs("canable")`).
- Unit tests and live-hardware tests are **completely separate** suites (see
  [Testing.md](Testing.md)).
- Unsafe pin/option-byte operations have an API (`disable_boot0()`) but **no test code**.
- All Python modules, classes, and public functions use **Google-style docstrings**
  (`Args:`, `Returns:`, `Raises:`).
- Do not push commits unless explicitly asked.

## Target hardware

- CanableDevBoard FDCAN, STM32G431, 8 MHz HSE.
- USB serial: `209D3889594550052`.
- USB VID/PID: `1d50:606f`.
- USB product string: `CanableDevBoard FDCAN CAN adapter`.
- USB manufacturer string: `ElmueSoft (netcult.ch/elmue)`.
- Firmware board name from `ELM_ReqGetBoardInfo`: `CanableDevBoard FDCAN`.
- Reported FDCAN clock: `160 MHz`.

Reference adapter (not supported by this plugin):

- Old FYSETC UCAN, serial `003500465446571320343032`, VID/PID `1d50:606f`, 48 MHz clock.
- Used through `python-can-candle` (`interface="candle"`) in live tests.
- Both adapters share VID/PID, so **always select by serial**, never by VID/PID alone.
- Plain `lsusb` reports the generic name `OpenMoko, Inc. Geschwister Schneider CAN adapter`
  for both; use sysfs `product`/`serial` instead.

## Reference sources

The original temporary checkout `/tmp/elmue-canable` is not required. Use the public mirror:

```text
https://github.com/Elmue/CANable-2.5-firmware-Slcan-and-Candlelight
```

Key files:

```text
SampleApplication Python/Source/Candlelight_def.py   # exact enums/struct layouts
Firmware/Candlelight/candlelight_def.h
Firmware/Candlelight/control.c
Firmware/Candlelight/buffer.c
Firmware/Candlelight/usb_class.c
Firmware/settings.h
Firmware/system.c
```

Board bring-up and validation history:

```text
https://github.com/shreeshan97/CanableDevBoard_FDCAN
docs/Testing.md, scripts/test_can_loopback.py, scripts/test_can_between_adapters.py
```

The Elmü firmware is MIT licensed. Constants and layouts derived from it are attributed in
`NOTICE` and summarized in [Protocol.md](Protocol.md).

## Repository layout

```text
pyproject.toml
README.md
LICENSE
NOTICE
canable/
  __init__.py        # exports CanableBus, list_devices, __version__
  bus.py             # CanableBus(can.BusABC)
  device.py          # USB discovery, open/claim/detach, control transfers
  protocol.py        # Elmü constants, structs, pack/unpack, feedback codes
  bit_timing.py      # capability-aware classic/FD timing selection
  exceptions.py
udev/
  99-canable.rules
tests/
  unit/              # hardware-free; default pytest target
    conftest.py
    test_protocol.py
    test_bit_timing.py
    test_device_selection.py
    test_bus_unit.py
    test_detection.py
  hardware/          # opt-in; explicit path + CANABLE_HW=1
    conftest.py
    test_hw_identity.py
    test_hw_loopback.py
    test_hw_reference.py
    test_hw_runtime.py
    test_hw_shutdown.py
examples/
  list_devices.py
  simple_send_receive.py
docs/
  Implement.md
  Protocol.md
  Testing.md
```

## Packaging

`pyproject.toml` must include a PEP 517 build system and the python-can plugin entry point:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "python-can-canable"
version = "0.1.0"
requires-python = ">=3.9"
license = "MIT"
dependencies = [
  "python-can>=4.2",
  "pyusb>=1.2",
]

[project.optional-dependencies]
test = ["pytest>=7"]
hardware = ["python-can-candle>=1.2"]

[project.entry-points."can.interface"]
canable = "canable.bus:CanableBus"

[tool.setuptools]
packages = ["canable"]

[tool.pytest.ini_options]
testpaths = ["tests/unit"]
markers = [
  "hardware: live-hardware test (not collected by default)",
  "stress: repeated alternating frames (opt-in)",
]
```

`python-can>=4.2` is required because `BitTiming.from_sample_point()` and `BitTimingFd`
are unavailable in older releases. The validated reference environment uses
`python-can==4.6.1` and `pyusb==1.3.1`.

Development setup:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[test,hardware]"
.venv/bin/pytest
```

## Public API

Primary usage:

```python
import can

with can.Bus(
    interface="canable",
    channel="209D3889594550052:0",  # SERIAL:CHANNEL
    bitrate=500_000,
    sample_point=87.5,
    fd=False,
    data_bitrate=2_000_000,
    ignore_config=True,
) as bus:
    bus.send(can.Message(arbitration_id=0x123, data=[1, 2, 3, 4]))
```

Constructor parameters to support:

- `channel`: `"SERIAL:INDEX"` primary form; also accept plain serial when channel 0 is intended.
- `bitrate`: nominal CAN bitrate, default `500000`.
- `sample_point`: nominal sample point percent, default `87.5`.
- `fd`: enable CAN FD; requires `data_bitrate`.
- `data_bitrate`, `data_sample_point`.
- `listen_only`, `loop_back`, `one_shot`, `timestamps`, `send_blobs`.
- `serial_number`, `product`, `manufacturer`, `vid`, `pid`, `bus`, `address`, `index`.
- `receive_own_messages`: software echo of sent frames when hardware echo is disabled.
- `can_filters`, `ignore_config=True` (recommended).

Public methods:

- `set_bitrate(bitrate, sample_point=87.5)`
- `set_data_bitrate(data_bitrate, sample_point=87.5)`
- `get_capabilities()`, `get_capabilities_fd()`
- `get_device_version()`, `get_board_info()`
- `get_error_state()`, `get_last_error()`
- `identify(enable=True)`
- `set_filters(filters)` / `clear_filters()`
- `set_busload_report(interval_100ms)`
- `get_pin_status(pin_id=PINID_BOOT0)`
- `disable_boot0()` — explicit warning; never called automatically and never tested.
- Standard `send()`, `recv()`, `shutdown()`.

`CanableBus` must implement the class method `_detect_available_configs()` because
python-can's `can.detect_available_configs()` calls that hook on the bus class. It returns
configs with `SERIAL:CHANNEL` channels.

Module-level:

```python
from canable import list_devices

for dev in list_devices():
    print(dev.serial_number, dev.product, dev.board_name)
```

## USB / protocol details

Full details are in [Protocol.md](Protocol.md). Summary:

- VID `0x1D50`, PID `0x606F`.
- Channel 0 uses interface 0, IN `0x81`, OUT `0x02`. Interface 1 is DFU and is never claimed.
- Control transfers use `bmRequestType` `0x41` (OUT) / `0xC1` (IN), `wValue = channel index`,
  `wIndex = 0`.
- After every OUT control request, call `ELM_ReqGetLastError` and require `FBK_Success = 2`;
  otherwise raise a descriptive `CanInitializationError` or `CanOperationError`. The firmware
  cannot stall EP0 in the OUT-data stage.
- `ELM_DevFlagProtocolElmue` is included in both the reset and start device-mode commands.
  It stays enabled until all channels are closed.
- Only the Elmü frame types (`MSG_TxFrame`, `MSG_TxEcho`, `MSG_RxFrame`, `MSG_Error`,
  `MSG_String`, `MSG_Busload`, `MSG_RxBlob`) are handled. No 80-byte legacy frame path.

### Initialization sequence

1. Enumerate and select device by serial/product/manufacturer/bus/address.
2. `set_configuration()` if needed.
3. Detach kernel driver from interface 0 if active.
4. `claim_interface(0)`.
5. `GS_ReqSetHostFormat(uint32 0xBEEF)`, then check last error.
6. `GS_ReqSetDeviceMode(reset, ELM_DevFlagProtocolElmue)`, check last error.
7. Read `GS_ReqGetDeviceVersion`, `GS_ReqGetCapabilities`, `GS_ReqGetCapabilitiesFD`.
8. Read `ELM_ReqGetBoardInfo`; validate board name starts with `CanableDevBoard`.
9. Validate `ELM_DevFlagProtocolElmue` in capabilities; otherwise raise a clear
   "not an Elmü CANable 2.5 device" error.
10. Compute and send nominal bit timing with `GS_ReqSetBitTiming`.
11. If `fd=True`, send data bit timing with `GS_ReqSetBitTimingFD`.
12. `GS_ReqSetDeviceMode(start, flags | ELM_DevFlagProtocolElmue)`, check last error.

### Bit timing

`kBitTiming.prop + kBitTiming.seg1` is the effective segment 1. Mapping from python-can:

```python
prop_seg = 1
phase_seg1 = bit_timing.tseg1 - 1
phase_seg2 = bit_timing.tseg2
sjw = bit_timing.sjw
brp = bit_timing.brp
```

Use `can.BitTiming.from_sample_point()` and `can.BitTimingFd.from_sample_point()` first,
then validate against `kTimeMinMax` limits. If invalid, search valid `brp/tseg1/tseg2`
combinations where `total_tq = fclk / (brp * bitrate)` is an integer and
`1 <= sjw <= min(sjw_max, tseg1, tseg2)`. Choose the candidate closest to the requested
sample point with deterministic tie-breaking. `bit_timing.py` is unit-testable without hardware.

### Acceptance filters

- Clear filters while the channel is closed (`HostClear`).
- Convert each python-can filter dict to `HostPass_11` or `HostPass_29`.
- Re-apply timing and restart the channel after filter changes.
- Bridge filters remain low-level only; multi-channel support is out of scope.

### Shutdown and kernel driver

1. Send `GS_ReqSetDeviceMode(reset, ELM_DevFlagProtocolElmue)`.
2. Release interface 0.
3. Reattach the kernel driver if it was detached.
4. Mark the bus closed; `shutdown()` is idempotent.

## USB permissions

Current host USB nodes for `1d50:606f` are `root:root 0664`; user `ssn` cannot open them.

Recommended rule in `udev/99-canable.rules`:

```udev
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="606f", GROUP="plugdev", MODE="0660", TAG+="uaccess"
```

Install, replug, and verify. Fallback: run test scripts with `sudo` (or as a user with raw USB
access). Opening the plugin detaches `gs_usb`, so SocketCAN `can0` for this board disappears
while the plugin owns it. The old FYSETC on `can1` is unaffected unless the candle test owns it.

## Testing strategy

Unit and live-hardware tests are separate suites; see [Testing.md](Testing.md).

- Unit tests: frame packing/unpacking, struct parsing, feedback codes, bit timing for
  48 MHz and 160 MHz, device selection, init sequence, detection, and buffer/blob handling.
- Live tests: identity, loopback, adapter↔adapter through a reference-adapter registry, a
  per-adapter popular classic-bitrate matrix, runtime bitrate changes, filters, and clean
  driver reattachment. FYSETC UCAN is the registered classic reference; FD transfer tests
  are capability-gated and skip until an FD-capable reference is registered.

## Acceptance criteria

- `python-can` discovers the interface via `interface="canable"`.
- Only the Elmü 2.5 protocol is used; no legacy 80-byte frame path.
- Product/board identity and 160 MHz clock are read from firmware.
- Bitrate and FD data bitrate can be changed programmatically.
- Frame delivery passes adapter↔adapter against the `python-can-candle` reference at all
  popular classic bitrates from 10 kbit/s to 1 Mbit/s.
- Unit tests pass without hardware.
- Docs explain permissions, kernel-driver detachment, and the `lsusb` generic-name caveat.
- Changes are committed locally; nothing is pushed.

## Out of scope

- Legacy Candlelight/gs_usb 80-byte frame protocol.
- Supporting the old FYSETC adapter inside this plugin.
- DFU flashing or firmware rebuilds.
- Option-byte programming except the explicit, unsafe-by-design `disable_boot0()` API.
- Changing the host USB ID database.
- Pushing commits.

## Implementation order

1. Update documentation (this file, `Protocol.md`, `Testing.md`, `NOTICE`).
2. Create `pyproject.toml`, package skeleton, and plugin entry point.
3. Implement `protocol.py` and `bit_timing.py` with unit tests.
4. Implement `device.py` and `CanableBus`.
5. Add the separated unit and hardware suites.
6. Validate on live hardware using the checklist in `Testing.md`.
7. Commit locally; do not push.
