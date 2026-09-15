#!/usr/bin/env python3
"""Configure DM GPIO triggers over USB/BLE (Heltec V3 / V4).

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
            "meshtastic package required. Install into the repo venv:\n"
            "  .venv/bin/pip install meshtastic pypubsub\n"
            "Then run with: .venv/bin/python bin/configure-dm-triggers.py ..."
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

    price = sub.add_parser("price", help="Set Lightning price in sats (0 = free)")
    price.add_argument("index", type=int)
    price.add_argument("sats", type=int)

    ticket = sub.add_parser("ticket", help="Load a payment_hash ticket (64 hex)")
    ticket.add_argument("index", type=int)
    ticket.add_argument("payment_hash")

    unticket = sub.add_parser("unticket", help="Remove an unused payment_hash ticket")
    unticket.add_argument("index", type=int)
    unticket.add_argument("payment_hash")

    tickets = sub.add_parser("tickets", help="List unused tickets for a trigger")
    tickets.add_argument("index", type=int)

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
        elif args.cmd == "price":
            _send_command(iface, f"!dmtrigger:price|{args.index}|{args.sats}", args.wait)
        elif args.cmd == "ticket":
            _send_command(iface, f"!dmtrigger:ticket|{args.index}|{args.payment_hash}", args.wait)
        elif args.cmd == "unticket":
            _send_command(iface, f"!dmtrigger:unticket|{args.index}|{args.payment_hash}", args.wait)
        elif args.cmd == "tickets":
            _send_command(iface, f"!dmtrigger:tickets|{args.index}", args.wait)
        else:  # pragma: no cover
            parser.error(f"unknown command {args.cmd}")
    finally:
        iface.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
