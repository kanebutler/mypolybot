"""
Client Module - API Clients for Polymarket CLOB and Builder Relayer

Features:
- HMAC authentication for Builder APIs
- Automatic retry with exponential backoff
- Gasless transactions via Relayer
"""

import time
import hmac
import hashlib
import base64
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import requests

from .config import BuilderConfig
from .http import ThreadLocalSessionMixin


class ApiError(Exception):
    pass

class AuthenticationError(ApiError):
    pass

class OrderError(ApiError):
    pass


@dataclass
class ApiCredentials:
    api_key: str
    secret: str
    passphrase: str

    @classmethod
    def load(cls, filepath: str) -> "ApiCredentials":
        with open(filepath) as f:
            data = json.load(f)
        return cls(
            api_key=data.get("apiKey", ""),
            secret=data.get("secret", ""),
            passphrase=data.get("passphrase", ""),
        )

    def is_valid(self) -> bool:
        return bool(self.api_key and self.secret and self.passphrase)


class ApiClient(ThreadLocalSessionMixin):
    """Base HTTP client with retries and JSON handling."""

    def __init__(self, base_url: str, timeout: int = 30, retry_count: int = 3):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry_count = retry_count

    def _request(
        self, method: str, endpoint: str,
        data: Optional[Any] = None, headers: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        last_err = None
        for attempt in range(self.retry_count):
            try:
                s = self.session
                if method.upper() == "GET":
                    r = s.get(url, headers=req_headers, params=params, timeout=self.timeout)
                elif method.upper() == "POST":
                    r = s.post(url, headers=req_headers, json=data, params=params, timeout=self.timeout)
                elif method.upper() == "DELETE":
                    r = s.delete(url, headers=req_headers, json=data, params=params, timeout=self.timeout)
                else:
                    raise ApiError(f"Unsupported method: {method}")
                r.raise_for_status()
                return r.json() if r.text else {}
            except requests.exceptions.RequestException as e:
                last_err = e
                if attempt < self.retry_count - 1:
                    time.sleep(2**attempt)
        raise ApiError(f"Request failed after {self.retry_count} attempts: {last_err}")


class ClobClient(ApiClient):
    """Client for Polymarket CLOB API."""

    def __init__(
        self, host: str = "https://clob.polymarket.com", chain_id: int = 137,
        signature_type: int = 2, funder: str = "",
        api_creds: Optional[ApiCredentials] = None,
        builder_creds: Optional[BuilderConfig] = None, timeout: int = 30,
    ):
        super().__init__(base_url=host, timeout=timeout)
        self.host = host
        self.chain_id = chain_id
        self.signature_type = signature_type
        self.funder = funder
        self.api_creds = api_creds
        self.builder_creds = builder_creds

    def set_api_creds(self, creds: ApiCredentials) -> None:
        self.api_creds = creds

    def _build_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        headers = {}
        if self.builder_creds and self.builder_creds.is_configured():
            ts = str(int(time.time()))
            msg = f"{ts}{method}{path}{body}"
            sig = hmac.new(
                self.builder_creds.api_secret.encode(), msg.encode(), hashlib.sha256
            ).hexdigest()
            headers.update({
                "POLY_BUILDER_API_KEY": self.builder_creds.api_key,
                "POLY_BUILDER_TIMESTAMP": ts,
                "POLY_BUILDER_PASSPHRASE": self.builder_creds.api_passphrase,
                "POLY_BUILDER_SIGNATURE": sig,
            })
        if self.api_creds and self.api_creds.is_valid():
            ts = str(int(time.time()))
            msg = f"{ts}{method}{path}"
            if body:
                msg += body
            try:
                b64_secret = base64.urlsafe_b64decode(self.api_creds.secret)
                h = hmac.new(b64_secret, msg.encode("utf-8"), hashlib.sha256)
                sig = base64.urlsafe_b64encode(h.digest()).decode("utf-8")
            except Exception:
                sig = hmac.new(
                    self.api_creds.secret.encode(), msg.encode(), hashlib.sha256
                ).hexdigest()
            headers.update({
                "POLY_ADDRESS": self.funder,
                "POLY_API_KEY": self.api_creds.api_key,
                "POLY_TIMESTAMP": ts,
                "POLY_PASSPHRASE": self.api_creds.passphrase,
                "POLY_SIGNATURE": sig,
            })
        return headers

    def create_or_derive_api_key(self, signer) -> ApiCredentials:
        ts = str(int(time.time()))
        nonce = 0
        sig = signer.sign_auth_message(timestamp=ts, nonce=nonce)
        headers = {
            "POLY_ADDRESS": signer.address,
            "POLY_SIGNATURE": sig,
            "POLY_TIMESTAMP": ts,
            "POLY_NONCE": str(nonce),
        }
        resp = self._request("GET", "/auth/derive-api-key", headers=headers)
        return ApiCredentials(
            api_key=resp.get("apiKey", ""),
            secret=resp.get("secret", ""),
            passphrase=resp.get("passphrase", ""),
        )

    def get_order_book(self, token_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/book?token_id={token_id}")

    def get_market_price(self, token_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/price?token_id={token_id}")

    def get_open_orders(self) -> List[Dict[str, Any]]:
        headers = self._build_headers("GET", "/orders")
        result = self._request("GET", "/orders", headers=headers)
        return result if isinstance(result, list) else []

    def get_trades(self, token_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        params = {"limit": limit}
        if token_id:
            params["asset_id"] = token_id
        headers = self._build_headers("GET", "/trades")
        result = self._request("GET", "/trades", headers=headers, params=params)
        return result if isinstance(result, list) else []

    def post_order(self, signed_order: Dict[str, Any], order_type: str = "GTC") -> Dict[str, Any]:
        body = {
            "order": signed_order.get("order", signed_order),
            "owner": self.funder,
            "orderType": order_type,
        }
        if "signature" in signed_order:
            body["signature"] = signed_order["signature"]
        body_json = json.dumps(body, separators=(",", ":"))
        headers = self._build_headers("POST", "/order", body_json)
        return self._request("POST", "/order", data=body, headers=headers)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        body = {"orderID": order_id}
        body_json = json.dumps(body, separators=(",", ":"))
        headers = self._build_headers("DELETE", "/order", body_json)
        return self._request("DELETE", "/order", data=body, headers=headers)

    def cancel_all_orders(self) -> Dict[str, Any]:
        headers = self._build_headers("DELETE", "/cancel-all")
        return self._request("DELETE", "/cancel-all", headers=headers)

    def cancel_market_orders(self, market: Optional[str] = None, asset_id: Optional[str] = None) -> Dict[str, Any]:
        body = {}
        if market:
            body["market"] = market
        if asset_id:
            body["asset_id"] = asset_id
        body_json = json.dumps(body, separators=(",", ":")) if body else ""
        headers = self._build_headers("DELETE", "/cancel-market-orders", body_json)
        return self._request("DELETE", "/cancel-market-orders", data=body if body else None, headers=headers)


class RelayerClient(ApiClient):
    """Client for Builder Relayer API (gasless transactions)."""

    def __init__(
        self, host: str = "https://relayer-v2.polymarket.com", chain_id: int = 137,
        builder_creds: Optional[BuilderConfig] = None, tx_type: str = "SAFE",
        timeout: int = 60,
    ):
        super().__init__(base_url=host, timeout=timeout)
        self.chain_id = chain_id
        self.builder_creds = builder_creds
        self.tx_type = tx_type

    def _build_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        if not self.builder_creds or not self.builder_creds.is_configured():
            raise AuthenticationError("Builder credentials required for relayer")
        ts = str(int(time.time()))
        msg = f"{ts}{method}{path}{body}"
        sig = hmac.new(
            self.builder_creds.api_secret.encode(), msg.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "POLY_BUILDER_API_KEY": self.builder_creds.api_key,
            "POLY_BUILDER_TIMESTAMP": ts,
            "POLY_BUILDER_PASSPHRASE": self.builder_creds.api_passphrase,
            "POLY_BUILDER_SIGNATURE": sig,
        }

    def deploy_safe(self, safe_address: str) -> Dict[str, Any]:
        body = {"safeAddress": safe_address}
        body_json = json.dumps(body, separators=(",", ":"))
        headers = self._build_headers("POST", "/deploy", body_json)
        return self._request("POST", "/deploy", data=body, headers=headers)
