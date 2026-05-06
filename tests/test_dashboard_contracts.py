from datetime import datetime, timedelta

from dashboard_contracts import utc_now


def test_utc_now_returns_parseable_utc_iso_timestamp():
    parsed = datetime.fromisoformat(utc_now())

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
