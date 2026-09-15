from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Invoice:
    payment_hash: str
    bolt11: str
    expires_at: float = 0.0


@dataclass
class PaidEvent:
    payment_hash: str
    preimage: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    amount_sats: int = 0


class InvoiceBackend(Protocol):
    def create_invoice(self, amount_sats: int, memo: str, extra: dict[str, Any], webhook: str | None) -> Invoice: ...

    def check_payment(self, payment_hash: str) -> PaidEvent | None: ...

    def parse_webhook(self, body: dict[str, Any]) -> PaidEvent: ...
