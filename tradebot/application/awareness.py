"""AwarenessService: hourly external-data + LLM brief -> committee evidence.

Replaces the synthetic macro/fundamental/onchain placeholders with REAL
domain reports whenever fresh awareness data exists, while technical and
liquidity stay derived from live candles. Degradation ladder, honest at every
rung (the committee already leans cautious on stale/missing domains):

1. fresh brief (<= max_age)            -> DomainStatus.OK, real sources cited
2. aging brief (<= stale_ceiling)      -> DomainStatus.STALE, age in the note
3. nothing usable                      -> candle-drift placeholder (labelled)

The service is called from the live loop's poll hook; it rate-limits itself to
one refresh per ``cadence`` and never raises into the trading path.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from ..domain.dark_horse import DomainReport, DomainStatus, EvidenceItem
from ..domain.market import MarketSnapshot
from .dark_horse import DomainSignal
from .tick_engine import EvidenceFn, committee_evidence

#: Stance smaller than this is "no directional call" (mirrors _FLAT_EPSILON's
#: role for candle drift).
_FLAT_STANCE = 0.1

HOUR = dt.timedelta(hours=1)


@dataclass
class AwarenessState:
    """Last-good brief plus bookkeeping the dashboard can render."""

    brief: object | None = None  # MarketBrief
    brief_at: dt.datetime | None = None
    last_attempt_at: dt.datetime | None = None
    last_status: str = "never_run"  # ok | degraded:<why> | never_run
    source_errors: tuple[str, ...] = ()
    llm_model_id: str = ""


@dataclass
class AwarenessService:
    """Hourly refresh + evidence provider for the permanent-wallet committee.

    ``gather`` and ``synthesize`` are injected (real: sources.gather_inputs +
    brief.synthesize_brief over the broker/LLM; tests: fakes).
    """

    gather: Callable[[], object]  # -> AwarenessInputs
    synthesize: Callable[[object], tuple[object | None, object]]
    cadence: dt.timedelta = HOUR
    max_age: dt.timedelta = dt.timedelta(hours=2)
    stale_ceiling: dt.timedelta = dt.timedelta(hours=6)
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(
        dt.timezone.utc).replace(tzinfo=None)
    state: AwarenessState = field(default_factory=AwarenessState)

    # -- hourly refresh (live loop poll hook) --------------------------------

    def refresh(self, force: bool = False) -> bool:
        """Fetch + brief at most once per cadence. True when a refresh ran."""

        now = self.clock()
        if (not force and self.state.last_attempt_at is not None
                and now - self.state.last_attempt_at < self.cadence):
            return False
        self.state.last_attempt_at = now
        try:
            inputs = self.gather()
        except Exception as exc:  # a broker bug must not stop trading
            self.state.last_status = f"degraded:gather_{type(exc).__name__}"
            return True
        self.state.source_errors = tuple(getattr(inputs, "errors", ()))
        try:
            brief, run = self.synthesize(inputs)
        except Exception as exc:
            self.state.last_status = f"degraded:llm_{type(exc).__name__}"
            return True
        if brief is None:
            self.state.last_status = f"degraded:llm_{getattr(run, 'status', 'error')}"
            return True
        self.state.brief = brief
        self.state.brief_at = now
        self.state.llm_model_id = getattr(run, "model_id", "")
        self.state.last_status = "ok"
        return True

    # -- committee evidence --------------------------------------------------

    def evidence_fn(self) -> EvidenceFn:
        """An EvidenceFn for the TickEngine: awareness-aware committee inputs."""

        def _evidence(window: tuple[MarketSnapshot, ...]):
            reports, signals, now = committee_evidence(window)
            age = self._age()
            if self.state.brief is None or age is None or age > self.stale_ceiling:
                return reports, signals, now  # honest placeholder fallback
            status = (DomainStatus.OK if age <= self.max_age
                      else DomainStatus.STALE)
            hours = age.total_seconds() / 3600
            for read in self.state.brief.domains:
                confidence = Decimal(f"{read.confidence:.2f}")
                if status is DomainStatus.STALE:
                    confidence = min(confidence, Decimal("0.40"))
                reports[read.domain] = DomainReport(
                    read.domain, status, (EvidenceItem(
                        source_id=",".join(read.cited_sources) or "awareness_llm",
                        metric=f"{read.domain}_stance",
                        value=f"{read.stance:.2f}",
                        interpretation=(read.rationale if status is DomainStatus.OK
                                        else f"stale ({hours:.1f}h old): "
                                             f"{read.rationale}"),
                        confidence=confidence,
                        source_time=self.state.brief_at,
                        retrieved_at=self.state.brief_at,
                        data_snapshot_id=f"awareness-{self.state.brief_at:%Y%m%dT%H%M}",
                    ),),
                    note="" if status is DomainStatus.OK else f"{hours:.1f}h old",
                )
                bullish = (None if abs(read.stance) < _FLAT_STANCE
                           else read.stance > 0)
                signals[read.domain] = DomainSignal(read.domain, bullish,
                                                    confidence)
            return reports, signals, now

        return _evidence

    # -- dashboard read model ------------------------------------------------

    def status_block(self) -> dict:
        """JSON-safe snapshot for the API/dashboard awareness panel."""

        age = self._age()
        brief = self.state.brief
        return {
            "status": self.state.last_status,
            "brief_at": (self.state.brief_at.isoformat() + "Z"
                         if self.state.brief_at else None),
            "age_hours": round(age.total_seconds() / 3600, 2) if age else None,
            "model_id": self.state.llm_model_id,
            "summary": getattr(brief, "summary", None),
            "domains": [{
                "domain": d.domain,
                "stance": round(d.stance, 2),
                "confidence": round(d.confidence, 2),
                "rationale": d.rationale,
                "sources": list(d.cited_sources),
            } for d in getattr(brief, "domains", [])],
            "source_errors": list(self.state.source_errors),
        }

    def _age(self) -> dt.timedelta | None:
        if self.state.brief_at is None:
            return None
        return self.clock() - self.state.brief_at
