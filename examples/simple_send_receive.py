"""Send one frame and wait for its echo through the ``canable`` plugin.

Usage::

    CANABLE_SERIAL=209D3889594550052 python examples/simple_send_receive.py
"""

from __future__ import annotations

import os

import can

DEFAULT_SERIAL = "209D3889594550052"


def main() -> None:
    """Open the board by serial, send a frame, and print the first reply."""
    serial = os.environ.get("CANABLE_SERIAL", DEFAULT_SERIAL)
    with can.Bus(
        interface="canable",
        channel=f"{serial}:0",
        bitrate=500_000,
        sample_point=87.5,
        loop_back=True,
        receive_own_messages=True,
        ignore_config=True,
    ) as bus:
        sent = can.Message(
            arbitration_id=0x123,
            data=b"\x01\x02\x03\x04",
            is_extended_id=False,
        )
        bus.send(sent)
        received = bus.recv(2.0)
        if received is None:
            print("No frame received; check bitrate, termination, and USB access.")
        else:
            print(f"received: ID=0x{received.arbitration_id:X} data={received.data.hex()}")


if __name__ == "__main__":
    main()
