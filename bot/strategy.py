from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from statistics import mean, pstdev
from math import isfinite
from bot.price_tracker import Tick


class Signal(Enum):
    CALL = "CALL"
    PUT = "PUT"
    NONE = "NONE"


@dataclass(frozen=True)
class TradeSignal:
    signal: Signal
    asset: str
    confidence: float
    entry_price: float
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


@dataclass
class EngineResult:
    call: float = 0.0
    put: float = 0.0
    reasons_call: list[str] = field(default_factory=list)
    reasons_put: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


@dataclass
class MarketContext:
    asset: str
    ticks: list[Tick]
    prices: list[float]
    times: list[float]
    entry: float
    diffs: list[float]
    recent_prices: list[float]
    older_prices: list[float]
    volatility: float
    avg_step: float
    high: float
    low: float
    mid: float
    spread_range: float


def _safe_mean(values: list[float], default: float = 0.0) -> float:
    return mean(values) if values else default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _norm(v: float, denom: float) -> float:
    if denom <= 0 or not isfinite(denom):
        return 0.0
    return v / denom


class BaseEngine:
    name = "BaseEngine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        return EngineResult()


# 1) Tick Flow: raw buy/sell tick dominance.
class TickFlowEngine(BaseEngine):
    name = "Tick Flow Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        diffs = ctx.diffs[-45:]
        ups = sum(1 for d in diffs if d > 0)
        downs = sum(1 for d in diffs if d < 0)
        total = max(1, ups + downs)
        flow = (ups - downs) / total
        strength = abs(flow)
        res = EngineResult(metrics={"tick_flow": round(flow, 4), "ups": ups, "downs": downs})
        if flow > 0.18:
            res.call += _clamp(strength * 22, 4, 22)
            res.reasons_call.append("tick_flow_buyers")
        elif flow < -0.18:
            res.put += _clamp(strength * 22, 4, 22)
            res.reasons_put.append("tick_flow_sellers")
        return res


# 2) Momentum: net move efficiency versus noise.
class MomentumEngine(BaseEngine):
    name = "Momentum Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        net = ctx.prices[-1] - ctx.prices[0]
        noise = sum(abs(d) for d in ctx.diffs) or 1e-9
        efficiency = abs(net) / noise
        power = abs(net) / max(ctx.volatility, ctx.avg_step, 1e-9)
        score = _clamp((efficiency * 18) + (power * 2.5), 0, 24)
        res = EngineResult(metrics={"momentum_net": round(net, 6), "efficiency": round(efficiency, 4), "momentum_power": round(power, 3)})
        if net > 0 and efficiency > 0.20:
            res.call += score
            res.reasons_call.append("clean_bull_momentum")
        elif net < 0 and efficiency > 0.20:
            res.put += score
            res.reasons_put.append("clean_bear_momentum")
        return res


# 3) Pressure: latest window pressure against previous window.
class PressureEngine(BaseEngine):
    name = "Pressure Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        recent_diffs = [ctx.recent_prices[i] - ctx.recent_prices[i-1] for i in range(1, len(ctx.recent_prices))]
        older_diffs = [ctx.older_prices[i] - ctx.older_prices[i-1] for i in range(1, len(ctx.older_prices))]
        recent_pressure = sum(1 if d > 0 else -1 if d < 0 else 0 for d in recent_diffs) / max(1, len(recent_diffs))
        older_pressure = sum(1 if d > 0 else -1 if d < 0 else 0 for d in older_diffs) / max(1, len(older_diffs))
        delta = recent_pressure - older_pressure
        res = EngineResult(metrics={"recent_pressure": round(recent_pressure, 4), "pressure_delta": round(delta, 4)})
        if recent_pressure > 0.20 and delta > 0:
            res.call += _clamp(10 + delta * 10, 4, 18)
            res.reasons_call.append("buyer_pressure_expansion")
        elif recent_pressure < -0.20 and delta < 0:
            res.put += _clamp(10 + abs(delta) * 10, 4, 18)
            res.reasons_put.append("seller_pressure_expansion")
        return res


# 4) Velocity: acceleration in last ticks.
class VelocityEngine(BaseEngine):
    name = "Velocity Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        if len(ctx.ticks) < 10:
            return EngineResult()
        recent = ctx.ticks[-10:]
        dt = max(0.25, recent[-1].ts - recent[0].ts)
        move = recent[-1].price - recent[0].price
        velocity = move / dt
        norm_velocity = velocity / max(ctx.avg_step, 1e-9)
        res = EngineResult(metrics={"velocity": round(velocity, 8), "norm_velocity": round(norm_velocity, 4)})
        if norm_velocity > 0.18:
            res.call += _clamp(abs(norm_velocity) * 8, 4, 16)
            res.reasons_call.append("bullish_velocity")
        elif norm_velocity < -0.18:
            res.put += _clamp(abs(norm_velocity) * 8, 4, 16)
            res.reasons_put.append("bearish_velocity")
        return res


# 5) Wick / Absorption proxy: sweep then rejection inside tick range.
class WickAbsorptionEngine(BaseEngine):
    name = "Wick Absorption Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        last = ctx.prices[-8:]
        if len(last) < 8:
            return EngineResult()
        local_high = max(ctx.prices[-35:-8] or ctx.prices[:-8])
        local_low = min(ctx.prices[-35:-8] or ctx.prices[:-8])
        made_high = max(last) > local_high
        made_low = min(last) < local_low
        close_from_high = (max(last) - ctx.entry) / max(ctx.spread_range, ctx.avg_step, 1e-9)
        close_from_low = (ctx.entry - min(last)) / max(ctx.spread_range, ctx.avg_step, 1e-9)
        res = EngineResult(metrics={"absorb_high": round(close_from_high, 4), "absorb_low": round(close_from_low, 4)})
        if made_low and close_from_low > 0.08:
            res.call += _clamp(close_from_low * 32, 5, 18)
            res.reasons_call.append("lower_wick_absorption")
        if made_high and close_from_high > 0.08:
            res.put += _clamp(close_from_high * 32, 5, 18)
            res.reasons_put.append("upper_wick_absorption")
        return res


# 6) Structure Break: break of recent tick range with follow-through.
class StructureBreakEngine(BaseEngine):
    name = "Break Structure Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        lookback = ctx.prices[-60:-1] if len(ctx.prices) > 60 else ctx.prices[:-1]
        if len(lookback) < 15:
            return EngineResult()
        prev_high = max(lookback)
        prev_low = min(lookback)
        res = EngineResult(metrics={"prev_high": round(prev_high, 6), "prev_low": round(prev_low, 6)})
        buffer = max(ctx.avg_step * 0.4, ctx.volatility * 0.03)
        if ctx.entry > prev_high + buffer:
            res.call += 18
            res.reasons_call.append("structure_break_up")
        elif ctx.entry < prev_low - buffer:
            res.put += 18
            res.reasons_put.append("structure_break_down")
        return res


# 7) Liquidity Sweep: false break then return.
class LiquiditySweepEngine(BaseEngine):
    name = "Liquidity Sweep Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        if len(ctx.prices) < 55:
            return EngineResult()
        base = ctx.prices[-55:-10]
        tail = ctx.prices[-10:]
        base_high = max(base)
        base_low = min(base)
        tail_high = max(tail)
        tail_low = min(tail)
        res = EngineResult(metrics={"sweep_high": tail_high > base_high, "sweep_low": tail_low < base_low})
        # Sweep down then close back up = CALL.
        if tail_low < base_low and ctx.entry > base_low and (ctx.entry - tail_low) > ctx.avg_step * 2:
            res.call += 20
            res.reasons_call.append("liquidity_sweep_low")
        # Sweep up then close back down = PUT.
        if tail_high > base_high and ctx.entry < base_high and (tail_high - ctx.entry) > ctx.avg_step * 2:
            res.put += 20
            res.reasons_put.append("liquidity_sweep_high")
        return res


# 8) Micro Trend: slope agreement across small windows.
class MicroTrendEngine(BaseEngine):
    name = "Micro Trend Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        windows = [8, 16, 32]
        slopes = []
        for w in windows:
            if len(ctx.prices) >= w:
                slopes.append(ctx.prices[-1] - ctx.prices[-w])
        if not slopes:
            return EngineResult()
        bull = sum(1 for s in slopes if s > 0)
        bear = sum(1 for s in slopes if s < 0)
        total_strength = sum(abs(s) for s in slopes) / max(ctx.avg_step, 1e-9)
        res = EngineResult(metrics={"micro_slopes": [round(s, 6) for s in slopes], "trend_strength": round(total_strength, 3)})
        if bull == len(slopes):
            res.call += _clamp(10 + total_strength, 10, 18)
            res.reasons_call.append("micro_trend_up")
        elif bear == len(slopes):
            res.put += _clamp(10 + total_strength, 10, 18)
            res.reasons_put.append("micro_trend_down")
        return res


# 9) Multi-Timeframe Filter proxy: same direction on 5s, 15s, 30s tick windows.
class MultiTimeframeFilter(BaseEngine):
    name = "Multi Timeframe Filter"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        frames = [12, 30, 60]
        dirs = []
        for f in frames:
            if len(ctx.prices) >= f:
                move = ctx.prices[-1] - ctx.prices[-f]
                dirs.append(1 if move > 0 else -1 if move < 0 else 0)
        res = EngineResult(metrics={"mtf_dirs": dirs})
        if len(dirs) >= 2 and all(d > 0 for d in dirs):
            res.call += 14
            res.reasons_call.append("mtf_aligned_call")
        elif len(dirs) >= 2 and all(d < 0 for d in dirs):
            res.put += 14
            res.reasons_put.append("mtf_aligned_put")
        elif len(dirs) >= 2 and 1 in dirs and -1 in dirs:
            res.call -= 10
            res.put -= 10
            res.metrics["mtf_conflict"] = True
        return res


# 10) Rejection: last ticks reject extremes.
class RejectionEngine(BaseEngine):
    name = "Rejection Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        last = ctx.prices[-6:]
        if len(last) < 6:
            return EngineResult()
        res = EngineResult()
        # Price printed low then final two ticks recover.
        if min(last) in last[-4:-1] and last[-1] > last[-2] > min(last):
            res.call += 12
            res.reasons_call.append("fast_lower_rejection")
        # Price printed high then final two ticks reject.
        if max(last) in last[-4:-1] and last[-1] < last[-2] < max(last):
            res.put += 12
            res.reasons_put.append("fast_upper_rejection")
        return res


# 11) Exhaustion: avoid buying/selling after overstretched one-way burst.
class ExhaustionEngine(BaseEngine):
    name = "Exhaustion Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        last_diffs = ctx.diffs[-10:]
        if len(last_diffs) < 10:
            return EngineResult()
        one_way_up = sum(1 for d in last_diffs if d > 0)
        one_way_down = sum(1 for d in last_diffs if d < 0)
        burst = abs(sum(last_diffs)) / max(ctx.avg_step, 1e-9)
        res = EngineResult(metrics={"exhaustion_burst": round(burst, 3)})
        if one_way_up >= 9 and burst > 12:
            res.call -= 16
            res.put += 6
            res.reasons_put.append("bullish_exhaustion_risk")
        elif one_way_down >= 9 and burst > 12:
            res.put -= 16
            res.call += 6
            res.reasons_call.append("bearish_exhaustion_risk")
        return res


# 12) Volume Proxy: tick density and movement quality.
class VolumeProxyEngine(BaseEngine):
    name = "Volume Proxy Engine"
    def evaluate(self, ctx: MarketContext) -> EngineResult:
        if len(ctx.ticks) < 20:
            return EngineResult()
        span = max(1.0, ctx.ticks[-1].ts - ctx.ticks[-20].ts)
        tick_rate = 20 / span
        net = ctx.ticks[-1].price - ctx.ticks[-20].price
        quality = abs(net) / max(sum(abs(ctx.ticks[i].price - ctx.ticks[i-1].price) for i in range(len(ctx.ticks)-19, len(ctx.ticks))), 1e-9)
        res = EngineResult(metrics={"tick_rate": round(tick_rate, 3), "volume_quality": round(quality, 4)})
        if tick_rate >= 1.2 and quality > 0.28:
            if net > 0:
                res.call += 10
                res.reasons_call.append("high_tick_activity_up")
            elif net < 0:
                res.put += 10
                res.reasons_put.append("high_tick_activity_down")
        return res


# 13) Confidence Engine: final aggregation, conflict handling.
class ConfidenceEngine:
    def decide(self, asset: str, entry: float, results: list[EngineResult], min_confidence: float) -> TradeSignal:
        call_score = sum(r.call for r in results)
        put_score = sum(r.put for r in results)
        reasons_call: list[str] = []
        reasons_put: list[str] = []
        metrics: dict = {"call_raw": round(call_score, 2), "put_raw": round(put_score, 2)}
        for r in results:
            reasons_call.extend(r.reasons_call)
            reasons_put.extend(r.reasons_put)
            metrics.update(r.metrics)

        # Contradiction penalty: avoid entries where both sides are strong.
        conflict = min(max(call_score, 0), max(put_score, 0))
        if conflict > 25:
            call_score -= conflict * 0.45
            put_score -= conflict * 0.45
            metrics["conflict_penalty"] = round(conflict * 0.45, 2)

        edge = abs(call_score - put_score)
        if edge < 18:
            return TradeSignal(Signal.NONE, asset, 0.0, entry, ["weak_edge"], metrics)

        def has_reversal_setup(reasons: list[str], side: str) -> bool:
            # Binary 60s entries fail badly when the bot chases pure velocity.
            # We require rejection/absorption/sweep before execution.
            if side == "CALL":
                required = {"liquidity_sweep_low", "lower_wick_absorption", "fast_lower_rejection"}
            else:
                required = {"liquidity_sweep_high", "upper_wick_absorption", "fast_upper_rejection"}
            return any(r in required for r in reasons)

        def enough_core_agreement(reasons: list[str], side: str) -> bool:
            joined = set(reasons)
            if side == "CALL":
                groups = [
                    {"tick_flow_buyers", "clean_bull_momentum", "buyer_pressure_expansion"},
                    {"bullish_velocity"},
                    {"micro_trend_up", "mtf_aligned_call"},
                    {"liquidity_sweep_low", "lower_wick_absorption", "fast_lower_rejection"},
                ]
            else:
                groups = [
                    {"tick_flow_sellers", "clean_bear_momentum", "seller_pressure_expansion"},
                    {"bearish_velocity"},
                    {"micro_trend_down", "mtf_aligned_put"},
                    {"liquidity_sweep_high", "upper_wick_absorption", "fast_upper_rejection"},
                ]
            return sum(1 for g in groups if joined & g) >= 4

        if call_score > put_score:
            confidence = _clamp(call_score - put_score * 0.45, 0, 97)
            reasons = reasons_call[:10]
            if not has_reversal_setup(reasons_call, "CALL"):
                metrics["reject"] = "chasing_call_without_reversal_setup"
                return TradeSignal(Signal.NONE, asset, confidence, entry, ["chase_filter_call"], metrics)
            if not enough_core_agreement(reasons_call, "CALL"):
                metrics["reject"] = "weak_call_agreement"
                return TradeSignal(Signal.NONE, asset, confidence, entry, ["weak_call_agreement"], metrics)
            return TradeSignal(Signal.CALL if confidence >= min_confidence else Signal.NONE, asset, confidence, entry, reasons or ["call_score"], metrics)

        confidence = _clamp(put_score - call_score * 0.45, 0, 97)
        reasons = reasons_put[:10]
        if not has_reversal_setup(reasons_put, "PUT"):
            metrics["reject"] = "chasing_put_without_reversal_setup"
            return TradeSignal(Signal.NONE, asset, confidence, entry, ["chase_filter_put"], metrics)
        if not enough_core_agreement(reasons_put, "PUT"):
            metrics["reject"] = "weak_put_agreement"
            return TradeSignal(Signal.NONE, asset, confidence, entry, ["weak_put_agreement"], metrics)
        return TradeSignal(Signal.PUT if confidence >= min_confidence else Signal.NONE, asset, confidence, entry, reasons or ["put_score"], metrics)


# 14) Decision Engine / MarketAnalyzer: runs engines in exact order.
class MarketAnalyzer:
    def __init__(self, min_confidence: float = 86.0, min_ticks: int = 45):
        self.min_confidence = min_confidence
        self.min_ticks = min_ticks
        self.engines: list[BaseEngine] = [
            TickFlowEngine(),
            MomentumEngine(),
            PressureEngine(),
            VelocityEngine(),
            WickAbsorptionEngine(),
            StructureBreakEngine(),
            LiquiditySweepEngine(),
            MicroTrendEngine(),
            MultiTimeframeFilter(),
            RejectionEngine(),
            ExhaustionEngine(),
            VolumeProxyEngine(),
        ]
        self.confidence_engine = ConfidenceEngine()

    def _context(self, asset: str, ticks: list[Tick]) -> MarketContext:
        clean = [t for t in ticks if t.price and isfinite(t.price)]
        prices = [t.price for t in clean[-max(self.min_ticks, 90):]]
        times = [t.ts for t in clean[-max(self.min_ticks, 90):]]
        diffs = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        entry = prices[-1]
        recent_prices = prices[-18:] if len(prices) >= 18 else prices
        older_prices = prices[-45:-18] if len(prices) >= 45 else prices[:-18]
        avg_step = _safe_mean([abs(d) for d in diffs], 1e-9) or 1e-9
        volatility = pstdev(prices) if len(prices) > 2 else avg_step
        high = max(prices)
        low = min(prices)
        mid = _safe_mean(prices, entry)
        spread_range = max(high - low, avg_step, 1e-9)
        return MarketContext(asset, clean[-max(self.min_ticks, 90):], prices, times, entry, diffs, recent_prices, older_prices, volatility, avg_step, high, low, mid, spread_range)

    def evaluate(self, asset: str, ticks: list[Tick]) -> TradeSignal:
        if len(ticks) < self.min_ticks:
            entry = ticks[-1].price if ticks else 0.0
            return TradeSignal(Signal.NONE, asset, 0.0, entry, ["not_enough_ticks"], {"ticks": len(ticks)})

        ctx = self._context(asset, ticks)
        if len(ctx.prices) < self.min_ticks or not ctx.diffs:
            return TradeSignal(Signal.NONE, asset, 0.0, ctx.entry, ["not_enough_clean_ticks"], {"ticks": len(ctx.prices)})

        results = [engine.evaluate(ctx) for engine in self.engines]
        signal = self.confidence_engine.decide(asset, ctx.entry, results, self.min_confidence)
        signal.metrics["engine_order"] = [engine.name for engine in self.engines] + ["Confidence Engine", "Decision Engine"]
        return signal
