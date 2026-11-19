# Elmü CANable 2.5 Protocol Reference

## Attribution

The constants, enums, and struct layouts below are derived from the **ElmüSoft Candlelight
2.5 firmware**, which is MIT licensed. Primary references:

- <https://github.com/Elmue/CANable-2.5-firmware-Slcan-and-Candlelight>
- `SampleApplication Python/Source/Candlelight_def.py` in that repository
- `Firmware/Candlelight/candlelight_def.h`, `control.c`, `buffer.c`, `usb_class.c`
- <https://netcult.ch/elmue/CANable%20Firmware%20Update/>

See `NOTICE` at the repository root. This driver implements the **new Elmü 2.5 protocol
only**; the legacy 80-byte `kHostFrameLegacy` format is deliberately not implemented.

## USB transport

- VID/PID: `1d50:606f`.
- Channel 0 uses USB interface 0 with IN endpoint `0x81` and OUT endpoint `0x02`.
- Interface 1 is DFU and must never be claimed by this driver.
- Reserved multi-channel mapping: channel 1 → interface 2 (`0x83`/`0x04`), channel 2 →
  interface 3 (`0x85`/`0x06`). Multi-channel operation is out of scope.
- Control transfers use `bmRequestType` `0x41` for OUT (host-to-device, vendor, interface
  recipient) and `0xC1` for IN. `wValue` is the channel index; `wIndex` is `0`.

Every OUT control request must be followed by `ELM_ReqGetLastError`. A value other than
`eFeedback.Success` (2) means the request failed; the firmware cannot stall EP0 during the
OUT-data stage.

## Control requests

```text
GS_ReqSetHostFormat        = 0   # OUT uint32 (little-endian magic 0xBEEF)
GS_ReqSetBitTiming         = 1   # OUT kBitTiming
GS_ReqSetDeviceMode        = 2   # OUT kDeviceMode
GS_ReqBerrReport           = 3   # not implemented
GS_ReqGetCapabilities      = 4   # IN  kCapabilityClassic
GS_ReqGetDeviceVersion     = 5   # IN  kDeviceVersion
GS_ReqGetTimestamp         = 6   # IN  uint32, microseconds, rolls over after 1 hour
GS_ReqIdentify             = 7   # OUT uint32 (ignored)
GS_ReqSetBitTimingFD       = 10  # OUT kBitTiming (data phase only)
GS_ReqGetCapabilitiesFD    = 11  # IN  kCapabilityFD
GS_ReqSetTermination       = 12  # optional board feature
GS_ReqGetTermination       = 13  # optional board feature
GS_ReqGetErrorState        = 14  # IN  kErrorState (deprecated; prefer MSG_Error)

ELM_ReqGetBoardInfo        = 20  # IN  kBoardInfo
ELM_ReqSetFilter           = 21  # OUT kFilter
ELM_ReqGetLastError        = 22  # IN  uint8 eFeedback
ELM_ReqSetBusLoadReport    = 23  # OUT uint8 interval
ELM_ReqSetPinStatus        = 24  # OUT kPinStatus
ELM_ReqGetPinStatus        = 25  # IN  uint16; pin ID is passed in wValue
ELM_ReqReadFlash           = 26  # not used by this driver
ELM_ReqWriteFlash          = 27  # not used by this driver
```

## Packed structures

Use `struct` with little-endian, standard sizes (`<`). All sizes below are in bytes.

```text
kBitTiming          <IIIII                 20  prop, seg1, seg2, sjw, brp
kDeviceMode         <II                     8   mode, flags
kTimeMinMax         <IIIIIIII              32  seg1_min, seg1_max, seg2_min, seg2_max,
                                               sjw_max, brp_min, brp_max, brp_inc
kCapabilityClassic  <IIIIIIIIII            40  feature, fclk_can, kTimeMinMax
kCapabilityFD       <IIIIIIIIIIIIIIIIII    72  feature, fclk_can, two kTimeMinMax
kDeviceVersion      <BBBBII                12  hal hi/mid/lo, icount, sw_bcd, hw_bcd
kErrorState         <III                   12  state, rx_err, tx_err
kBoardInfo          <H25s25sI              56  McuDeviceID, McuName, BoardName, BoardFlags
kFilter             <BIIBB6s               17  Operation, Filter, Mask, Index,
                                               DestChannel, Reserved
kPinStatus          <HHII                  12  Operation, PinID, Reserved1, Reserved2
kHeader             <BB                    2   size, msg_type
```

`kCapabilityFD` is **72 bytes**, not 80: `4 + 4 + 32 + 32`. The `fclk_can` field for the
target board is `160_000_000`.

## Modes, flags, and feedback

```text
eDeviceMode.ModeReset = 0
eDeviceMode.ModeStart = 1

GS_DevFlagListenOnly       = 0x00001
GS_DevFlagLoopback         = 0x00002
GS_DevFlagOneShot          = 0x00008
GS_DevFlagTimestamp        = 0x00010
GS_DevFlagIdentify         = 0x00020
GS_DevFlagCAN_FD           = 0x00100
GS_DevFlagBitTimingFD      = 0x00400
GS_DevFlagTermination      = 0x00800
GS_DevFlagGetErrorState    = 0x02000
ELM_DevFlagProtocolElmue  = 0x04000
ELM_DevFlagSendUsbBlobs   = 0x08000
```

`ELM_DevFlagProtocolElmue` enables the Elmü frame format for all channels and remains set
until every channel is closed. It must be present in both the initial reset and the final
start command, and must be verified in the capability `feature` word.

Feedback codes returned by `ELM_ReqGetLastError`:

```text
Success             = 2
InvalidCommand      = ord('1')   # 0x31
InvalidParameter    = ord('2')   # 0x32
AdapterMustBeOpen   = ord('3')   # 0x33
AdapterMustBeClosed = ord('4')   # 0x34
ErrorFromHAL        = ord('5')   # 0x35
UnsupportedFeature  = ord('6')   # 0x36
TxBufferFull        = ord('7')   # 0x37
BusIsOff            = ord('8')   # 0x38
NoTxInSilentMode    = ord('9')   # 0x39
BaudrateNotSet      = 0x3A
OptBytesProgrFailed = 0x3B
ResetRequired       = 0x3C
ParamOutOfRange     = 0x3D
```

CAN ID and frame flags:

```text
CAN ID: Extended = 0x80000000, RTR = 0x40000000, Error = 0x20000000
Frame:  FDF      = 0x02, BRS = 0x04, ESI = 0x08
```

## Frame formats

`kHeader.size` is the total length of the message, including the 2-byte header.

### TX frame (host → device)

```text
offset 0:  header.size     uint8   = 8 + len(data)
offset 1:  header.msg_type uint8   = 10 (MSG_TxFrame)
offset 2:  flags           uint8   = FDF/BRS/ESI bits
offset 3:  can_id          uint32  = ID + CAN ID flags
offset 7:  marker          uint8   = 0 (no echo) or 1..255 (request TxEcho)
offset 8:  data            variable
```

DLC is derived from the byte count. For remote frames, the first data byte carries the DLC.
For CAN FD, set `FDF` and optionally `BRS`/`ESI`.

### RX frame (device → host)

```text
offset 0:  header (2 bytes)
offset 2:  flags   (1 byte)
offset 3:  can_id  (4 bytes)
offset 7:  timestamp (4 bytes, only when GS_DevFlagTimestamp is set)
offset 7/11: data
```

Data length is `header.size - 7` without timestamps, or `header.size - 11` with timestamps.
Timestamps are microseconds and roll over after one hour.

### Other message types

```text
11 MSG_TxEcho   marker [+ timestamp]                  # correlates a sent frame
12 MSG_RxFrame  CAN receive frame                      # decoded into can.Message
13 MSG_Error    err_id uint32 + 8 bytes [+ timestamp]  # decoded/logged, never CAN data
14 MSG_String   ASCII debug text                       # log only
15 MSG_Busload  one byte percent                       # optional report
16 MSG_TxBlob   frame_count, msg_type, subframes       # host → device
17 MSG_RxBlob   frame_count, msg_type, subframes       # device → host
```

`MSG_Error` layout: `err_id` uses `eErrFlagsCanID`; `data[5]` carries controller flags
(`eErrorAppFlags`), `data[6]` is the TX error count, and `data[7]` is the RX error count.
Common `err_id` values seen during bring-up: `0x08` (bit-stuffing/protocol error) and `0x20`
(no ACK).

For blobs, iterate `frame_count` subframes, advancing by each subframe's `header.size`.
Validate `2 <= size <= remaining bytes` before advancing to avoid infinite loops. Reads must
feed a persistent buffer in `CanableBus` so partial USB packets are handled correctly.

## Acceptance filters

`eFilterOperation`: `HostClear=0`, `HostPass_11=1`, `HostPass_29=2`, plus bridge operations
10–14 which are out of scope. Filters can be programmed only while the channel is closed.
Python-can filter dictionaries map to `HostPass_11` or `HostPass_29` depending on the ID
width; the channel is restarted after filters change.

## Pin status and BOOT0

```text
ePinOperation.Disable = 5
ePinID.BOOT0          = 1
ePinStatus.High       = 0x01
ePinStatus.Enabled    = 0x02
```

`ELM_ReqGetPinStatus` receives the pin ID in `wValue` and returns a 2-byte status.
`disable_boot0()` sends `kPinStatus(Operation=Disable, PinID=BOOT0)` via
`ELM_ReqSetPinStatus`. This changes STM32 option bytes and is intentionally unsafe; the
driver never calls it automatically and no test exercises it.

## Initialization sequence

1. Select the device by serial/product/manufacturer/bus/address.
2. Set configuration if needed; detach `gs_usb` from interface 0; claim interface 0.
3. Send `GS_ReqSetHostFormat(0xBEEF)` and verify feedback.
4. Send `GS_ReqSetDeviceMode(ModeReset, ELM_DevFlagProtocolElmue)` and verify feedback.
5. Read version, classic capabilities, FD capabilities, and board info.
6. Validate board name starts with `CanableDevBoard` and `ELM_DevFlagProtocolElmue` is set.
7. Compute and send nominal bit timing; optionally send FD data bit timing.
8. Send `GS_ReqSetDeviceMode(ModeStart, flags | ELM_DevFlagProtocolElmue)` and verify feedback.

On shutdown, reverse step 4, release the interface, reattach the kernel driver, and mark the
bus closed.
