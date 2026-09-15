from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin

from ln.backend import Invoice, PaidEvent


class LnBitsBackend:
    def __init__(self, url: str, invoice_key: str) -> None:
        self.url = url.rstrip("/")
        self.invoice_key = invoice_key

    def create_invoice(self, amount_sats: int, memo: str, extra: dict[str, Any], webhook: str | None) -> Invoice:
        if amount_sats <= 0:
            raise ValueError("amount_sats must be > 0")
        payload: dict[str, Any] = {
            "out": False,
            "amount": int(amount_sats),
            "unit": "sat",
            "memo": memo,
            "extra": extra,
        }
        if webhook:
            payload["webhook"] = webhook
        data = self._request("POST", "/api/v1/payments", payload)
        payment_hash = str(data.get("payment_hash") or data.get("checking_id") or "")
        bolt11 = str(data.get("payment_request") or data.get("bolt11") or "")
        if not payment_hash or not bolt11:
            raise RuntimeError(f"LNBits invoice response missing hash/bolt11: {data!r}")
        expiry = data.get("expiry") or data.get("expires_at") or 0
        try:
            expires_at = float(expiry)
        except (TypeError, ValueError):
            expires_at = 0.0
        return Invoice(payment_hash=payment_hash.lower(), bolt11=bolt11, expires_at=expires_at)

    def check_payment(self, payment_hash: str) -> PaidEvent | None:
        path = f"/api/v1/payments/{payment_hash}"
        try:
            data = self._request("GET", path)
        except RuntimeError:
            return None
        paid = bool(data.get("paid") or data.get("preimage"))
        if data.get("pending") is True:
            paid = False
        if not paid:
            return None
        return self.parse_webhook(data)

    def parse_webhook(self, body: dict[str, Any]) -> PaidEvent:
        extra = body.get("extra") if isinstance(body.get("extra"), dict) else {}
        payment_hash = str(body.get("payment_hash") or body.get("checking_id") or extra.get("payment_hash") or "")
        preimage = str(body.get("preimage") or body.get("payment_preimage") or "")
        amount = body.get("amount") or extra.get("amount") or 0
        try:
            amount_sats = int(amount)
            # LNBits sometimes reports millisats.
            if amount_sats >= 1000 and amount_sats % 1000 == 0 and "msat" in str(body).lower():
                amount_sats //= 1000
        except (TypeError, ValueError):
            amount_sats = 0
        if not payment_hash:
            raise ValueError("webhook missing payment_hash")
        return PaidEvent(
            payment_hash=payment_hash.lower(),
            preimage=preimage.lower(),
            extra=extra,
            amount_sats=amount_sats,
        )

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = urljoin(self.url + "/", path.lstrip("/"))
        headers = {"X-Api-Key": self.invoice_key, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LNBits HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LNBits request failed: {exc}") from exc
        if not raw:
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError(f"LNBits returned non-object JSON: {parsed!r}")
        return parsed
