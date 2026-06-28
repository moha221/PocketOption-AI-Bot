from __future__ import annotations
from dataclasses import dataclass, field
import os

@dataclass
class BotConfig:
    ssid: str = ""

    # Active subscribed/traded assets. Keep <= max_subscriptions.
    assets: list[str] = field(default_factory=lambda: [
        "EURUSD_otc",
        "GBPUSD_otc",
        "USDJPY_otc",
    ])

    # Asset menu in Telegram. The platform/library may reject too many live subscriptions.
    all_assets: list[str] = field(default_factory=lambda: [
        "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "EURJPY_otc", "AUDUSD_otc",
        "AUDJPY_otc", "NZDUSD_otc", "USDCAD_otc", "USDCHF_otc", "GBPJPY_otc",
        "CADJPY_otc", "CHFJPY_otc", "EURGBP_otc", "EURCAD_otc", "EURAUD_otc",
    ])

    amount: float = 1.0
    duration: int = 60
    timeframe: int = 5
    cooldown: float = 90.0

    # To trade several pairs at the same time, set max_open_trades > 1.
    max_open_trades: int = 3

    # Live subscription cap. If you set this too high you get: Maximum subscriptions limit reached.
    max_subscriptions: int = 3

    min_confidence: float = 95.0
    min_payout: int = 70
    auto_trade: bool = False

    max_trades_per_session: int = 50
    stop_after_losses: int = 4
    stop_after_wins: int = 999

    tick_buffer: int = 220
    min_ticks_for_signal: int = 90
    scan_interval: float = 0.35

    # Execution filters
    confirmations_required: int = 3     # same asset + same direction must repeat before entry
    signal_alerts: bool = False         # False = Telegram sends only real trade opened/result, not every raw signal
    signal_alert_cooldown: float = 25.0

    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    telegram_poll_interval: float = 1.2

    results_file: str = "trade_results.csv"
    log_level: str = "INFO"
    log_to_terminal: bool = True
    log_dir: str = "."

    def normalize_assets(self):
        clean = []
        for a in self.assets:
            a = str(a).strip()
            if a and a not in clean:
                clean.append(a)
        self.assets = clean[: self.max_subscriptions]

    def summary(self) -> str:
        return (
            f"Assets: {len(self.assets)} -> {', '.join(self.assets)}\n"
            f"Amount: ${self.amount}\n"
            f"Duration: {self.duration}s\n"
            f"Timeframe: {self.timeframe}s\n"
            f"Min Confidence: {self.min_confidence}%\n"
            f"Confirmations: {self.confirmations_required}\n"
            f"Min Payout: {self.min_payout}%\n"
            f"Cooldown: {self.cooldown}s\n"
            f"Max Open Trades: {self.max_open_trades}\n"
            f"Max Subscriptions: {self.max_subscriptions}\n"
            f"Auto Trade: {'ON' if self.auto_trade else 'OFF'}"
        )
