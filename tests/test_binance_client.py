from urllib.parse import parse_qs, urlparse

import binance_client
from binance_client import BinanceSpotREST


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.raise_for_status_called = False

    def raise_for_status(self):
        self.raise_for_status_called = True

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload=None):
        self.headers = {}
        self.payload = payload if payload is not None else {"ok": True}
        self.get_calls = []
        self.post_calls = []
        self.responses = []

    def get(self, url, params=None, timeout=None):
        response = FakeResponse(self.payload)
        self.responses.append(response)
        self.get_calls.append({"url": url, "params": params, "timeout": timeout})
        return response

    def post(self, url, data=None, timeout=None, headers=None):
        response = FakeResponse(self.payload)
        self.responses.append(response)
        self.post_calls.append({"url": url, "data": data, "timeout": timeout, "headers": headers})
        return response


def _client_with_fake_session(monkeypatch, payload=None):
    session = FakeSession(payload=payload)
    monkeypatch.setattr(binance_client.requests, "Session", lambda: session)
    client = BinanceSpotREST(
        base_url="https://api.example.test/",
        api_key="key",
        api_secret="secret",
        timeout_s=7,
    )
    return client, session


def test_klines_uses_unsigned_get_path_and_params(monkeypatch):
    rows = [[1, "100.0", "110.0", "90.0", "105.0"]]
    client, session = _client_with_fake_session(monkeypatch, payload=rows)

    result = client.klines(symbol="BTCUSDT", interval="1m", limit=5)

    assert result == rows
    assert session.headers == {"X-MBX-APIKEY": "key"}
    assert session.get_calls == [{
        "url": "https://api.example.test/api/v3/klines",
        "params": {"symbol": "BTCUSDT", "interval": "1m", "limit": 5},
        "timeout": 7,
    }]
    assert session.responses[0].raise_for_status_called is True


def test_account_uses_signed_get_with_timestamp_and_signature(monkeypatch):
    monkeypatch.setattr(binance_client.time, "time", lambda: 1234567890.0)
    client, session = _client_with_fake_session(monkeypatch, payload={"balances": []})

    result = client.account()

    assert result == {"balances": []}
    assert session.get_calls[0]["params"] is None
    assert session.get_calls[0]["timeout"] == 7
    parsed = urlparse(session.get_calls[0]["url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.example.test"
    assert parsed.path == "/api/v3/account"
    query = parse_qs(parsed.query)
    assert query == {
        "timestamp": ["1234567890000"],
        "recvWindow": ["5000"],
        "signature": ["6fb0ee79942282008b237b023d83057b257ee8d769b69805becfaf41a9447283"],
    }


def test_sign_returns_deterministic_signature(monkeypatch):
    client, _session = _client_with_fake_session(monkeypatch)

    signed = client._sign({"symbol": "BTCUSDT", "timestamp": 1234567890000, "recvWindow": 5000})

    assert (
        signed
        == "symbol=BTCUSDT&timestamp=1234567890000&recvWindow=5000"
        "&signature=d6a04b40d078b4eef652710f565b55bad0134fc39de49e4ba1dab5a6600c1b24"
    )


def test_new_order_posts_signed_form_params(monkeypatch):
    monkeypatch.setattr(binance_client.time, "time", lambda: 1234567890.0)
    client, session = _client_with_fake_session(monkeypatch, payload={"orderId": 123})

    result = client.new_order(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity="0.01",
        price="100.00",
    )

    assert result == {"orderId": 123}
    assert session.post_calls[0]["url"] == "https://api.example.test/api/v3/order"
    assert session.post_calls[0]["timeout"] == 7
    assert session.post_calls[0]["headers"] == {"Content-Type": "application/x-www-form-urlencoded"}
    body = parse_qs(session.post_calls[0]["data"])
    assert body == {
        "symbol": ["BTCUSDT"],
        "side": ["BUY"],
        "type": ["LIMIT"],
        "quantity": ["0.01"],
        "price": ["100.00"],
        "timestamp": ["1234567890000"],
        "recvWindow": ["5000"],
        "signature": ["22de62d3387fd9f20b5ce1eb5368f62a304d7184a6e86531d296c2ea910d96cd"],
    }
