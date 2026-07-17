"""Registry of the twelve built-in competition strategies."""

from __future__ import annotations

from .s01_vol_adaptive_grid import VolAdaptiveGrid
from .s02_bollinger_zscore import BollingerZScore
from .s03_vwap_reversion import VwapReversion
from .s04_oscillator_exhaustion import OscillatorExhaustion
from .s05_donchian_breakout import DonchianBreakout
from .s06_ema_pullback import EmaPullback
from .s07_macd_momentum import MacdMomentum
from .s08_squeeze_expansion import SqueezeExpansion
from .s09_chandelier_trend import ChandelierTrend
from .s10_mtf_momentum import MtfMomentum
from .s11_obv_breakout import ObvBreakout
from .s12_regime_ensemble import RegimeEnsemble

BUILTIN_STRATEGIES = (
    VolAdaptiveGrid,
    BollingerZScore,
    VwapReversion,
    OscillatorExhaustion,
    DonchianBreakout,
    EmaPullback,
    MacdMomentum,
    SqueezeExpansion,
    ChandelierTrend,
    MtfMomentum,
    ObvBreakout,
    RegimeEnsemble,
)
