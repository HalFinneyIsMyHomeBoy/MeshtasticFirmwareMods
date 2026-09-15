"""Lightning ticket helpers for DM GPIO phrases."""

from __future__ import annotations

import hashlib
import re


_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def normalize_hex32(value: str) -> str:
    text = (value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not _HEX64.match(text):
        raise ValueError("expected 64 hex characters")
    if text == "0" * 64:
        raise ValueError("all-zero hash/preimage is not allowed")
    return text


def sha256_hex(preimage_hex: str) -> str:
    raw = bytes.fromhex(normalize_hex32(preimage_hex))
    return hashlib.sha256(raw).hexdigest()


def format_ticket_dm(phrase: str, preimage_hex: str) -> str:
    phrase = (phrase or "").strip()
    if not phrase:
        raise ValueError("phrase is required")
    if len(phrase) > 39:
        raise ValueError("phrase too long (max 39 characters)")
    if ":" in phrase:
        raise ValueError("phrase cannot contain ':'")
    return f"{phrase}:{normalize_hex32(preimage_hex)}"


def parse_ticket_dm(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if ":" not in raw:
        raise ValueError("ticket DM must be phrase:preimage")
    phrase, preimage = raw.rsplit(":", 1)
    return phrase, normalize_hex32(preimage)
