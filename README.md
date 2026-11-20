# python-can-canable

`python-can` plugin for **ElmüSoft CANable 2.5 / Candlelight 2.5** USB-CAN adapters using
the new Elmü 2.5 protocol only.

## Status

Implemented. The design, protocol reference, and test strategy are documented in
[docs/](docs/README.md):

- [Implementation plan](docs/Implement.md)
- [Protocol reference](docs/Protocol.md)
- [Testing](docs/Testing.md)

## Target hardware

- CanableDevBoard FDCAN (STM32G431, 8 MHz HSE, 160 MHz FDCAN clock), serial
  `209D3889594550052`.
- VID/PID `1d50:606f`. Both the target board and the reference adapter share this ID, so
  select devices by serial or sysfs product name, not by VID/PID.

## Usage

```python
import can

with can.Bus(
    interface="canable",
    channel="209D3889594550052:0",
    bitrate=500_000,
    sample_point=87.5,
    ignore_config=True,
) as bus:
    bus.send(can.Message(arbitration_id=0x123, data=[1, 2, 3, 4]))

configs = can.detect_available_configs("canable")
```

Select devices by serial because the target and the reference adapter share VID/PID
`1d50:606f`. See [examples/](examples/) for runnable snippets.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[test,hardware]"
pytest                                # unit tests only
CANABLE_HW=1 pytest tests/hardware -q # live hardware tests
```

USB access requires `udev/99-canable.rules` (recommended) or root privileges. Opening the
plugin detaches the `gs_usb` kernel driver from interface 0.

## License

MIT. Protocol definitions are derived from MIT-licensed Elmü firmware; see [NOTICE](NOTICE).
