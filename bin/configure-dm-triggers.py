#!/usr/bin/env python3
"""Configure Heltec V3 DM GPIO triggers over USB/BLE.

Sends !dmtrigger: admin commands to the local node. The sender must either be
the node itself (self-DM) or hold a configured admin key.

Example:
  ./bin/configure-dm-triggers.py --port /dev/ttyUSB0 list
  ./bin/configure-dm-triggers.py --port /dev/ttyUSB0 add \
      --name "Garage" --type output --message "Open_Seseme" --gpio 7 --duration-ms 10000
  ./bin/configure-dm-triggers.py --port /dev/ttyUSB0 add \
      --name "Voltage" --type analog --message "Get_Reading" --gpio 6
  ./bin/configure-dm-triggers.py --port /dev/ttyUSB0 del 0
  ./bin/configure-dm-triggers.py --port /dev/ttyUSB0 clear
"""

from __future__ import annotations

import argparse
import sys
import time


def _connect(port: str | None):
    try:
        import meshtastic.serial_interface
    except ImportError as exc:  # pragma: no cover - runtime helper
        raise SystemExit(
            "meshtastic package required: pip install meshtastic"
        ) from exc

    if port:
        return meshtastic.serial_interface.SerialInterface(devPath=port)
    return meshtastic.serial_interface.SerialInterface()


def _send_command(iface, command: str, wait_s: float = 2.0) -> None:
    node = iface.getNode()
    node_num = iface.localNode.nodeNum
    print(f"Sending: {command}")
    iface.sendText(command, destinationId=node_num, wantAck=True)
    time.sleep(wait_s)
    print("Done. Check the device serial log or any DM reply for confirmation.")


def _build_set_command(args, index: int) -> str:
    duration_ms = args.duration_ms if args.type == "output" else 0
    multiplier = args.adc_multiplier if args.type == "analog" else 1.0
    return (
        f"!dmtrigger:set|{index}|{args.name}|{args.type}|{args.message}|"
        f"{args.gpio}|{duration_ms}|{multiplier}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Serial port (default: auto-detect)")
    parser.add_argument("--wait", type=float, default=2.0, help="Seconds to wait after send")

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List configured triggers")

    add = sub.add_parser("add", help="Add or replace a trigger")
    add.add_argument("--index", type=int, default=-1, help="Trigger slot (default: append)")
    add.add_argument("--name", required=True)
    add.add_argument("--type", choices=["output", "analog"], required=True)
    add.add_argument("--message", required=True)
    add.add_argument("--gpio", type=int, required=True)
    add.add_argument("--duration-ms", type=int, default=1000)
    add.add_argument("--adc-multiplier", type=float, default=1.0)

    delete = sub.add_parser("del", help="Delete trigger by index")
    delete.add_argument("index", type=int)

    sub.add_parser("clear", help="Remove all triggers")

    args = parser.parse_args(argv)
    iface = _connect(args.port)
    try:
        if args.cmd == "list":
            _send_command(iface, "!dmtrigger:list", args.wait)
        elif args.cmd == "clear":
            _send_command(iface, "!dmtrigger:clear", args.wait)
        elif args.cmd == "del":
            _send_command(iface, f"!dmtrigger:del|{args.index}", args.wait)
        elif args.cmd == "add":
            _send_command(iface, _build_set_command(args, args.index), args.wait)
        else:  # pragma: no cover
            parser.error(f"unknown command {args.cmd}")
    finally:
        iface.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
