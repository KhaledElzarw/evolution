import os

from binance_client import BinanceSpotREST
from dotenv import load_dotenv


def _required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def main():
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

    base_url = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
    api_key = _required("BINANCE_API_KEY")
    api_secret = _required("BINANCE_API_SECRET")
    symbol = os.getenv("BINANCE_SYMBOL", "BTCUSDT")

    client = BinanceSpotREST(base_url=base_url, api_key=api_key, api_secret=api_secret)

    # Connectivity checks
    client.ping()
    st = client.server_time()
    print(f"OK ping. Server time: {st}")

    # Auth check
    acct = client.account()
    balances = [b for b in acct.get("balances", []) if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0]
    print(f"Authenticated. Non-zero balances: {len(balances)}")

    # Market data check
    k = client.klines(symbol=symbol, interval="15m", limit=5)
    last = k[-1]
    print(f"Klines OK. {symbol} last close={last[4]} (interval=15m)")


if __name__ == "__main__":
    main()
