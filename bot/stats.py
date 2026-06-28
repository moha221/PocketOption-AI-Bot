from __future__ import annotations
import csv, os
from datetime import datetime, timezone

class TradeLogger:
    FIELDS = ["timestamp","trade_id","asset","direction","confidence","entry_price","outcome","amount","duration","reasons"]
    def __init__(self, path: str = "trade_results.csv"):
        self._path = path
        self._ensure_header()
    def _ensure_header(self):
        if not os.path.exists(self._path):
            with open(self._path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(self.FIELDS)
    def record(self, trade_id: str, asset: str, direction: str, confidence: float, entry_price: float, outcome: str, amount: float, duration: int, reasons: list[str]):
        row = [datetime.now(timezone.utc).isoformat(), trade_id, asset, direction, f"{confidence:.2f}", f"{entry_price:.5f}", outcome, f"{amount:.2f}", duration, "|".join(reasons)]
        with open(self._path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

def load_results(path: str = "trade_results.csv") -> list[dict]:
    if not os.path.exists(path): return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def stats_snapshot(path: str = "trade_results.csv") -> dict:
    rows = load_results(path)
    total = len(rows)
    wins = sum(1 for r in rows if r.get("outcome","").lower() == "win")
    losses = sum(1 for r in rows if r.get("outcome","").lower() == "loss")
    draws = sum(1 for r in rows if r.get("outcome","").lower() == "draw")
    return {"total":total,"wins":wins,"losses":losses,"draws":draws,"win_rate":round((wins/total*100) if total else 0,2)}

def print_stats(path: str = "trade_results.csv"):
    s = stats_snapshot(path)
    print(f"Total: {s['total']} | Wins: {s['wins']} | Losses: {s['losses']} | Draws: {s['draws']} | Rate: {s['win_rate']}%")
