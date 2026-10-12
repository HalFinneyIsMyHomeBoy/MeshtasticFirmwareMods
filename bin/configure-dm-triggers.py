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
  ./bin/configure-dm-triggers.py --port /dev/ttyUSB0 add \
      --name "Greeting" --type reply --message "Hello" --reply "Hi there"
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
            "  .venv/bin/pip install -r bin/dm-trigger-requirements.txt\n"
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


TEXT_BYTES = 233
PACKET_BYTES = 200


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _chunks(index: int, field: str, text: str) -> list[tuple[int, str]]:
    chunks: list[tuple[int, str]] = []
    offset = 0
    buf = ""
    buf_bytes = 0
    for ch in text:
        n = _utf8_len(ch)
        header = f"!dmtrigger:put|{index}|{field}|{offset}|"
        if _utf8_len(header) + buf_bytes + n > PACKET_BYTES:
            if not buf:
                raise SystemExit("A single character does not fit in one Meshtastic message")
            chunks.append((offset, buf))
            offset += buf_bytes
            buf = ch
            buf_bytes = n
        else:
            buf += ch
            buf_bytes += n
    if buf:
        chunks.append((offset, buf))
    return chunks


def _save_commands(args, index: int) -> list[str]:
    duration_ms = args.duration_ms if args.type == "output" else 0
    multiplier = args.adc_multiplier if args.type == "analog" else 1.0
    gpio = 0 if args.type == "reply" else args.gpio
    reply = args.reply if args.type == "reply" else ""
    commands = [f"!dmtrigger:open|{index}|{args.name}|{args.type}|{gpio}|{duration_ms}|{multiplier}"]
    commands.extend(
        f"!dmtrigger:put|{index}|m|{offset}|{text}" for offset, text in _chunks(index, "m", args.message)
    )
    commands.extend(
        f"!dmtrigger:put|{index}|r|{offset}|{text}" for offset, text in _chunks(index, "r", reply)
    )
    commands.append(f"!dmtrigger:commit|{index}")
    return commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Serial port (default: auto-detect)")
    parser.add_argument("--wait", type=float, default=2.0, help="Seconds to wait after send")

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List configured triggers")

    add = sub.add_parser("add", help="Add or replace a trigger")
    add.add_argument("--index", type=int, default=-1, help="Trigger slot (default: append)")
    add.add_argument("--name", required=True)
    add.add_argument("--type", choices=["output", "analog", "reply"], required=True)
    add.add_argument("--message", required=True, help="Incoming DM text that must match")
    add.add_argument("--reply", default="", help="Text to send back when type is reply")
    add.add_argument("--gpio", type=int, default=0)
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
            if args.type == "reply" and not args.reply.strip():
                parser.error("--reply is required when --type reply")
            if args.type != "reply" and args.gpio <= 0:
                parser.error("--gpio is required unless --type reply")
            if "|" in args.name:
                parser.error("name cannot contain |")
            if _utf8_len(args.message) > TEXT_BYTES or _utf8_len(args.reply) > TEXT_BYTES:
                parser.error("message and reply must each be at most 233 bytes")
            for command in _save_commands(args, args.index):
                # The board drops a second USB text inside 2 seconds until firmware exempts local texts.
                _send_command(iface, command, max(args.wait, 2.05))
        else:  # pragma: no cover
            parser.error(f"unknown command {args.cmd}")
    finally:
        iface.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
