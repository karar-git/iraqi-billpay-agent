"""Small client for the wallet API, the same methods in-process or over HTTP.

    WalletClient(TestClient(create_app(":memory:", seed=...)))   # in-process (tests, eval, demo)
    WalletClient.over_http("http://127.0.0.1:8000")               # a running `uvicorn wallet.api:app`

Errors:
- WalletError: the wallet answered with a 4xx (`code` is detail.error, e.g. "wrong_pin").
- WalletUnavailable: no usable answer (timeout, connection error, 5xx). For an execute call this
  means the outcome is UNKNOWN: the money may or may not have moved.
"""

from __future__ import annotations

from typing import Any

import httpx


class WalletError(Exception):
    """The wallet refused a request (HTTP 4xx)."""

    def __init__(self, status: int, code: str, detail: dict | None = None):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.detail = detail or {}


class WalletUnavailable(Exception):
    """The wallet gave no usable answer (timeout, network error or HTTP 5xx)."""


class WalletClient:
    """Thin wrapper over the wallet endpoints. `http` is an httpx.Client or a FastAPI TestClient."""

    def __init__(self, http: Any):
        self.http = http

    @classmethod
    def over_http(cls, base_url: str, timeout: float = 10.0) -> "WalletClient":
        return cls(httpx.Client(base_url=base_url, timeout=timeout))

    @classmethod
    def in_process(cls, app: Any) -> "WalletClient":
        from fastapi.testclient import TestClient

        return cls(TestClient(app))

    # ------------------------------------------------------------ reads

    def user(self, uid: str) -> dict:
        return self._call("GET", f"/users/{uid}")

    def contacts(self, uid: str) -> list[dict]:
        return self._call("GET", f"/users/{uid}/contacts")

    def bill_accounts(self, uid: str) -> list[dict]:
        return self._call("GET", f"/users/{uid}/bill-accounts")

    def bills(self, account_id: str) -> list[dict]:
        return self._call("GET", "/bills", params={"account_id": account_id})

    def billers(self) -> list[dict]:
        return self._call("GET", "/billers")

    def transactions(self, uid: str, since_minutes: int | None = None) -> list[dict]:
        params = {} if since_minutes is None else {"since_minutes": since_minutes}
        return self._call("GET", f"/users/{uid}/transactions", params=params)

    def quote(self, qid: str) -> dict:
        return self._call("GET", f"/quotes/{qid}")

    def audit(self) -> dict:
        return self._call("GET", "/admin/audit")

    # ------------------------------------------------------------ writes

    def create_quote(self, user_id: str, kind: str, target: dict, amount: int | None = None) -> dict:
        body = {"user_id": user_id, "kind": kind, "target": target, "amount": amount}
        return self._call("POST", "/quotes", json=body)

    def execute(self, qid: str, pin: str | int) -> dict:
        """Run a quote. The quote id is the idempotency key, so a retry can never pay twice."""
        try:
            return self._call("POST", f"/quotes/{qid}/execute", json={"pin": str(pin)},
                              headers={"Idempotency-Key": qid})
        except (WalletError, WalletUnavailable):
            raise
        except Exception as exc:  # anything we cannot read means: outcome unknown
            raise WalletUnavailable(repr(exc)) from exc

    def cancel(self, qid: str) -> dict:
        return self._call("POST", f"/quotes/{qid}/cancel")

    def set_fault(self, mode: str, times: int | None = None) -> dict:
        return self._call("POST", "/admin/faults", json={"mode": mode, "times": times})

    # ------------------------------------------------------------ plumbing

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.http.request(method, path, **kwargs)
        except (httpx.TransportError, TimeoutError, OSError) as exc:
            raise WalletUnavailable(repr(exc)) from exc
        if response.status_code >= 500:
            raise WalletUnavailable(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise WalletUnavailable(f"HTTP {response.status_code}: not JSON") from exc
        if response.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else None
            detail = detail if isinstance(detail, dict) else {"error": str(detail)}
            raise WalletError(response.status_code, str(detail.get("error", "error")), detail)
        return body
