# Host USB setup

This plugin uses PyUSB/libusb. A working host USB driver and a libusb backend are both
required; one does not replace the other.

Applies to genuine **ElmüSoft CANable 2.5 / Candlelight 2.5** firmware only. The plugin claims
USB interface 0 (Candlelight CAN) and leaves interface 1 (DFU) untouched. Both CAN and DFU
interfaces use VID/PID `1d50:606f`, so always select a board by USB serial, for example
`209D3889594550052:0`.

## All platforms

1. Install the package and its Python dependencies:
   ```sh
   python -m pip install -e ".[test]"
   ```
2. Make sure PyUSB can find a compatible **libusb 1.0** runtime.
3. Verify discovery:
   ```sh
   python examples/list_devices.py
   ```
   A working adapter appears as `SERIAL:0`.

The plugin itself does not use the Windows Candlelight device GUID, but host USB drivers and
other CAN applications may.

## Linux

Install [`udev/99-canable.rules`](../udev/99-canable.rules), reload the rules, and replug the
adapter:

```sh
sudo cp udev/99-canable.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

The rule grants `plugdev`/`uaccess` access to both VID/PID interfaces. Running Python as
`root` is a diagnostic fallback, not the recommended setup. Opening the plugin detaches the
`gs_usb` kernel driver from interface 0 and reattaches it on shutdown.

## Windows

Correct CANable 2.5 firmware reports Microsoft OS descriptors that make Windows install
WinUSB automatically for both interfaces:

- String ID `0xEE`, signature `MSFT100`, vendor code `0x20`.
- Compatible ID `USB\MS_COMP_WINUSB` for interfaces 0 and 1.
- Interface 0 GUID: `{c15b4308-04d3-11e6-b3ea-6057189e6443}`.
- Interface 1 GUID: `{c25b4308-04d3-11e6-b3ea-6057189e6443}`.

**Do not use Zadig on a correctly flashed CANable 2.5 adapter.** Zadig can replace WinUSB with
a driver registered under an arbitrary GUID. GUI tools that search for the Candlelight GUID
then report that no adapter is connected. If Windows does not bind WinUSB automatically, the
firmware's MS OS descriptor response is the first thing to check; reflash the correct Elmü
2.5 firmware rather than treating this as a Python problem.

After WinUSB is installed, PyUSB still needs a libusb 1.0 runtime. Install a `libusb-1.0.dll`
build matching the Python/Windows architecture and place it where the dynamic loader can find
it (for example on `PATH`). The firmware's built-in WinUSB support and PyUSB's libusb backend
are separate requirements.

Legacy/buggy firmware may fail to respond to the Microsoft OS descriptor request. In that
case Windows may install no usable driver, and this plugin cannot open the device. Reflash
correct 2.5 firmware.

No Windows hardware run of this plugin has been recorded yet; the Windows path is supported
by the cross-platform PyUSB design and must still be validated on a real host.

## macOS

Install a libusb 1.0 runtime supported by the host and Python architecture. macOS does not use
the Linux udev rule or the Windows MS OS descriptor/GUID behavior described above.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `LIBUSB_ERROR_ACCESS` | Linux: udev rule missing or the adapter was not replugged. Windows: WinUSB/descriptor problem or restrictive host policy. |
| PyUSB reports "No backend available" | libusb 1.0 runtime is missing, or its architecture does not match Python. |
| Discovery works with another tool but not Python | Python is using a different libusb installation or backend. |
| A GUI CAN tool cannot find a WinUSB-bound adapter | The Candlelight interface GUID was overwritten; avoid Zadig and restore the correct firmware/driver binding. |
| Device appears with an unexpected serial | Confirm the board identity; VID/PID alone cannot distinguish CAN and DFU interfaces or two adapters. |
