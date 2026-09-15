#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ln.tickets import format_ticket_dm, normalize_hex32, parse_ticket_dm, sha256_hex


class TicketHelpersTest(unittest.TestCase):
    def test_normalize_and_sha256(self) -> None:
        preimage = "11" * 32
        digest = sha256_hex(preimage)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, sha256_hex(preimage.upper()))
        self.assertEqual(normalize_hex32("0x" + preimage), preimage)

    def test_format_and_parse(self) -> None:
        preimage = "ab" * 32
        dm = format_ticket_dm("Open_Seseme", preimage)
        self.assertEqual(dm, f"Open_Seseme:{preimage}")
        phrase, got = parse_ticket_dm(dm)
        self.assertEqual(phrase, "Open_Seseme")
        self.assertEqual(got, preimage)

    def test_rejects_bad_inputs(self) -> None:
        with self.assertRaises(ValueError):
            normalize_hex32("00" * 32)
        with self.assertRaises(ValueError):
            format_ticket_dm("bad:phrase", "11" * 32)
        with self.assertRaises(ValueError):
            format_ticket_dm("", "11" * 32)


if __name__ == "__main__":
    unittest.main()
