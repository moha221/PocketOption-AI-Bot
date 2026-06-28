from __future__ import annotations
import asyncio
from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync
from bot.strategy import TradeSignal, Signal
from bot.stats import TradeLogger
from bot.telegram_control import TelegramControl, RuntimeState

class Trader:
    def __init__(self, api: PocketOptionAsync, amount: float, duration: int, cooldown: float, logger: TradeLogger, telegram: TelegramControl | None = None, state: RuntimeState | None = None, max_open_trades: int = 1):
        self._api=api; self._amount=amount; self._duration=duration; self._cooldown=cooldown; self._logger=logger; self._telegram=telegram; self._state=state; self._max_open_trades=max_open_trades
        self._last_trade_time_by_asset: dict[str,float] = {}
        self._open_trade_ids: set[str] = set()
        self._open_assets: set[str] = set()

    def update_runtime(self, amount: float, duration: int, cooldown: float, max_open_trades: int):
        self._amount=amount; self._duration=duration; self._cooldown=cooldown; self._max_open_trades=max_open_trades

    def _now(self) -> float:
        return asyncio.get_event_loop().time()

    def on_cooldown(self, asset: str) -> bool:
        return (self._now() - self._last_trade_time_by_asset.get(asset, 0.0)) < self._cooldown

    async def execute(self, signal: TradeSignal):
        if signal.signal is Signal.NONE:
            return
        if self.on_cooldown(signal.asset):
            return
        if signal.asset in self._open_assets:
            return
        if len(self._open_trade_ids) >= self._max_open_trades:
            return

        self._last_trade_time_by_asset[signal.asset] = self._now()
        direction = signal.signal.value
        try:
            if signal.signal is Signal.CALL:
                trade_id, _ = await self._api.buy(asset=signal.asset, amount=self._amount, time=self._duration, check_win=False)
            else:
                trade_id, _ = await self._api.sell(asset=signal.asset, amount=self._amount, time=self._duration, check_win=False)

            trade_id = str(trade_id)
            self._open_trade_ids.add(trade_id)
            self._open_assets.add(signal.asset)
            if self._state:
                self._state.open_trades=len(self._open_trade_ids)
                self._state.last_signal=f"{signal.asset} {direction} {signal.confidence:.1f}%"

            msg=(f"TRADE OPENED\nAsset: {signal.asset}\nDirection: {direction}\nConfidence: {signal.confidence:.1f}%\n"
                 f"Amount: ${self._amount}\nDuration: {self._duration}s\nEntry: {signal.entry_price:.5f}\n"
                 f"Reasons: {', '.join(signal.reasons)}\nID: {trade_id}")
            print("\n"+msg)
            if self._telegram:
                await self._telegram.send(msg)
            asyncio.create_task(self._track_result(trade_id, signal))
        except Exception as e:
            err=f"FAILED {direction} {signal.asset}: {e}"
            print("\n[ERR] "+err)
            if self._telegram:
                await self._telegram.send(err)

    async def _track_result(self, trade_id: str, signal: TradeSignal):
        outcome = "timeout"
        result = {}
        try:
            # Give Pocket Option extra time after expiry, then retry check_win.
            await asyncio.sleep(max(2, min(30, self._duration // 3)))
            for attempt in range(1, 4):
                try:
                    result = await self._api.check_win(trade_id)
                    outcome = str(result.get("result", "unknown")).lower()
                    if outcome and outcome != "unknown":
                        break
                except Exception as e:
                    if attempt == 3:
                        outcome = "timeout"
                    else:
                        await asyncio.sleep(10 * attempt)

            self._logger.record(trade_id, signal.asset, signal.signal.value, signal.confidence, signal.entry_price, outcome, self._amount, self._duration, signal.reasons)

            if self._state:
                self._state.total_trades += 1
                if outcome == "win": self._state.wins += 1
                elif outcome == "loss": self._state.losses += 1
                elif outcome == "draw": self._state.draws += 1

            msg=(f"TRADE RESULT\nAsset: {signal.asset}\nDirection: {signal.signal.value}\nConfidence: {signal.confidence:.1f}%\n"
                 f"Outcome: {outcome.upper()}\nID: {trade_id}")
            print("\n"+msg)
            if self._telegram:
                await self._telegram.send(msg)
        finally:
            self._open_trade_ids.discard(trade_id)
            self._open_assets.discard(signal.asset)
            if self._state:
                self._state.open_trades=len(self._open_trade_ids)
