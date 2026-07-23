"""Credential-free external inputs for the hourly awareness brief.

Every fetch goes through the DataBroker (deny-by-default allowlist, redirect
revalidation, size/MIME caps, markup sanitization) — external text is data,
never instructions. Each fetcher is independent: one failing source never takes
the others down; the caller records which inputs were available.

Sources (all already on the A09 allowlist):
* CoinGecko ``/api/v3/global``               -> macro (total mcap, BTC dominance)
* CoinGecko ``/api/v3/coins/bitcoin``        -> fundamental (price momentum, ath)
* mempool.space ``/api/v1/fees/recommended`` -> onchain (fee pressure)
* CoinDesk + Cointelegraph RSS               -> headlines (secondary evidence)
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

from ..data_broker.client import DataBroker, sanitize_markup


@dataclass(frozen=True, slots=True)
class SourceReading:
    """One source's contribution: labelled metrics plus provenance."""

    source_id: str
    domain_hint: str  # which committee domain this primarily informs
    metrics: dict[str, str]  # metric -> stringified value
    fetched_at: dt.datetime
    snapshot_id: str  # broker's normalized content hash (provenance)


@dataclass(frozen=True, slots=True)
class Headlines:
    source_id: str
    titles: tuple[str, ...]
    fetched_at: dt.datetime
    snapshot_id: str


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def fetch_coingecko_global(broker: DataBroker) -> SourceReading:
    """Total-market stats: the macro backdrop for BTC."""

    res = broker.fetch("coingecko_global",
                       "https://api.coingecko.com/api/v3/global")
    data = json.loads(res.payload)["data"]
    return SourceReading(
        source_id="coingecko_global", domain_hint="macro",
        metrics={
            "total_market_cap_usd": f"{data['total_market_cap']['usd']:.0f}",
            "market_cap_change_24h_pct":
                f"{data['market_cap_change_percentage_24h_usd']:.2f}",
            "btc_dominance_pct": f"{data['market_cap_percentage']['btc']:.2f}",
        },
        fetched_at=_now(), snapshot_id=res.normalized_hash,
    )


def fetch_btc_stats(broker: DataBroker) -> SourceReading:
    """Bitcoin-specific market data: momentum and distance from the high."""

    res = broker.fetch(
        "coingecko_btc",
        "https://api.coingecko.com/api/v3/coins/bitcoin"
        "?localization=false&tickers=false&market_data=true"
        "&community_data=false&developer_data=false&sparkline=false")
    md = json.loads(res.payload)["market_data"]
    return SourceReading(
        source_id="coingecko_btc", domain_hint="bitcoin_fundamental",
        metrics={
            "price_usd": f"{md['current_price']['usd']:.2f}",
            "change_24h_pct": f"{md['price_change_percentage_24h']:.2f}",
            "change_7d_pct": f"{md['price_change_percentage_7d']:.2f}",
            "change_30d_pct": f"{md['price_change_percentage_30d']:.2f}",
            "ath_change_pct": f"{md['ath_change_percentage']['usd']:.2f}",
        },
        fetched_at=_now(), snapshot_id=res.normalized_hash,
    )


def fetch_mempool_stats(broker: DataBroker) -> SourceReading:
    """On-chain demand proxy: recommended fee rates (sat/vB)."""

    res = broker.fetch("mempool_fees",
                       "https://mempool.space/api/v1/fees/recommended")
    fees = json.loads(res.payload)
    return SourceReading(
        source_id="mempool_fees", domain_hint="onchain",
        metrics={
            "fastest_fee_sat_vb": str(fees["fastestFee"]),
            "half_hour_fee_sat_vb": str(fees["halfHourFee"]),
            "economy_fee_sat_vb": str(fees["economyFee"]),
        },
        fetched_at=_now(), snapshot_id=res.normalized_hash,
    )


_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("coindesk_rss", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph_rss", "https://cointelegraph.com/rss"),
)

# The broker sanitizes XML to inert flat text, which loses the item structure,
# so headline extraction runs on the PRE-sanitized body: pull <title> elements
# with a regex, then sanitize each title individually. Titles remain data.
_TITLE_RE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                       re.IGNORECASE | re.DOTALL)


def extract_rss_titles(xml_text: str, limit: int = 8) -> tuple[str, ...]:
    """Item titles from raw RSS, each individually sanitized and length-capped.

    The first ``<title>`` is the channel name, not a headline — skipped.
    """

    titles = []
    for m in _TITLE_RE.finditer(xml_text):
        clean = sanitize_markup(m.group(1))[:200]
        if clean:
            titles.append(clean)
    return tuple(titles[1:limit + 1])


class _RawCapture:
    """Transport wrapper capturing the last raw body the broker fetched."""

    def __init__(self, transport):
        self._inner = transport
        self.last_body: bytes = b""

    def request(self, method, url, headers):
        resp = self._inner.request(method, url, headers)
        self.last_body = resp.body
        return resp


def fetch_news_headlines(broker: DataBroker, limit: int = 8) -> list[Headlines]:
    """Latest headlines from each allowlisted feed; failures are skipped.

    Runs each feed through a broker sharing the caller's transport/resolver so
    every policy check still applies, while retaining the raw XML needed to
    split individual titles.
    """

    out: list[Headlines] = []
    for source_id, url in _RSS_FEEDS:
        capture = _RawCapture(broker.transport)
        guarded = DataBroker(transport=capture, resolver=broker.resolver,
                             user_agent=broker.user_agent)
        try:
            res = guarded.fetch(source_id, url)
        except Exception:
            continue  # one dead feed never blocks the brief
        titles = extract_rss_titles(
            capture.last_body.decode("utf-8", errors="replace"), limit=limit)
        if titles:
            out.append(Headlines(source_id=source_id, titles=titles,
                                 fetched_at=_now(),
                                 snapshot_id=res.normalized_hash))
    return out


@dataclass(frozen=True, slots=True)
class AwarenessInputs:
    """Everything one refresh gathered (any field may be missing)."""

    readings: tuple[SourceReading, ...] = ()
    headlines: tuple[Headlines, ...] = ()
    errors: tuple[str, ...] = ()  # "source_id: ExcType" for the status panel
    fetched_at: dt.datetime = field(default_factory=_now)


def gather_inputs(broker: DataBroker) -> AwarenessInputs:
    """Fetch every source independently; report per-source failures honestly."""

    readings: list[SourceReading] = []
    errors: list[str] = []
    for fn in (fetch_coingecko_global, fetch_btc_stats, fetch_mempool_stats):
        try:
            readings.append(fn(broker))
        except Exception as exc:
            errors.append(f"{fn.__name__}: {type(exc).__name__}")
    headlines = fetch_news_headlines(broker)
    if not headlines:
        errors.append("news_feeds: no feed reachable")
    return AwarenessInputs(readings=tuple(readings), headlines=tuple(headlines),
                           errors=tuple(errors))
