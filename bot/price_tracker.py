from __future__ import annotations
import asyncio
from collections import deque
from dataclasses import dataclass
from time import time
from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync

@dataclass
class Tick:
    ts: float
    price: float

class PriceTracker:
    def __init__(self, maxlen: int = 220):
        self.maxlen = maxlen
        self._prices: dict[str, float | None] = {}
        self._ticks: dict[str, deque[Tick]] = {}
        self._callbacks: list = []

    @property
    def prices(self) -> dict[str, float | None]:
        return dict(self._prices)

    def get(self, symbol: str) -> float | None:
        return self._prices.get(symbol)

    def ticks(self, symbol: str) -> list[Tick]:
        return list(self._ticks.get(symbol, []))

    def on_update(self, callback):
        self._callbacks.append(callback)

    def remove(self, symbol: str):
        self._prices.pop(symbol, None)
        self._ticks.pop(symbol, None)

    async def watch(self, api: PocketOptionAsync, symbol: str):
        self._prices.setdefault(symbol, None)
        self._ticks.setdefault(symbol, deque(maxlen=self.maxlen))
        stream = await api.subscribe_symbol(symbol)
        async for candle in stream:
            try:
                price = float(candle["close"])
            except Exception:
                continue
            tick = Tick(ts=time(), price=price)
            self._prices[symbol] = price
            self._ticks[symbol].append(tick)
            for cb in self._callbacks:
                try:
                    cb(symbol, price, tick)
                except Exception:
                    pass

    async def wait_for_prices(self, symbols: list[str], poll: float = 0.25):
        while any(self._prices.get(s) is None for s in symbols):
            await asyncio.sleep(poll)
