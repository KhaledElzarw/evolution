"""Quarantine tracking for misbehaving strategy workers.

A strategy version accumulates strikes on timeout, crash, malformed output, or
prohibited access. Reaching the strike limit quarantines it: the engine stops
scheduling it and the promotion pipeline treats it as technically invalid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_STRIKE_LIMIT = 3

QUARANTINE_CATEGORIES = frozenset(
    {"Timeout", "WorkerCrash", "MalformedOutput", "ProhibitedAccess"}
)


@dataclass(slots=True)
class QuarantineRegistry:
    strike_limit: int = DEFAULT_STRIKE_LIMIT
    _strikes: dict[str, int] = field(default_factory=dict)
    _quarantined: dict[str, str] = field(default_factory=dict)  # version -> reason

    def record_failure(self, strategy_version_id: str, category: str) -> bool:
        """Record a failure; returns True if the version is now quarantined."""

        if strategy_version_id in self._quarantined:
            return True
        if category not in QUARANTINE_CATEGORIES:
            return False
        strikes = self._strikes.get(strategy_version_id, 0) + 1
        self._strikes[strategy_version_id] = strikes
        if strikes >= self.strike_limit:
            self._quarantined[strategy_version_id] = category
            return True
        return False

    def record_success(self, strategy_version_id: str) -> None:
        """A clean run resets the strike counter (but never un-quarantines)."""

        if strategy_version_id not in self._quarantined:
            self._strikes.pop(strategy_version_id, None)

    def is_quarantined(self, strategy_version_id: str) -> bool:
        return strategy_version_id in self._quarantined

    def quarantine_reason(self, strategy_version_id: str) -> str | None:
        return self._quarantined.get(strategy_version_id)
