from __future__ import annotations
import asyncio, argparse
from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync
from BinaryOptionsToolsV2.tracing import start_logs
from bot.config import BotConfig
from bot.price_tracker import PriceTracker
from bot.strategy import MarketAnalyzer, Signal
from bot.trader import Trader
from bot.stats import TradeLogger
from bot.telegram_control import TelegramControl, RuntimeState

async def run_bot(ssid: str, cfg: BotConfig):
    if ssid:
        cfg.ssid = ssid
    if not cfg.ssid:
        cfg.ssid = input("Enter your SSID: ").strip()
    cfg.normalize_assets()

    if cfg.log_level:
        start_logs(cfg.log_dir, cfg.log_level, terminal=cfg.log_to_terminal)

    api = PocketOptionAsync(cfg.ssid)
    await asyncio.sleep(5)

    state = RuntimeState(running=True, paused=not cfg.auto_trade)
    try:
        state.balance = await api.balance()
    except Exception:
        state.balance = None

    telegram = TelegramControl(cfg.telegram_token, cfg.telegram_chat_id, cfg, state)
    logger = TradeLogger(cfg.results_file)
    tracker = PriceTracker(maxlen=cfg.tick_buffer)
    analyzer = MarketAnalyzer(min_confidence=cfg.min_confidence, min_ticks=cfg.min_ticks_for_signal)
    trader = Trader(api, cfg.amount, cfg.duration, cfg.cooldown, logger, telegram, state, cfg.max_open_trades)

    watcher_tasks: dict[str, asyncio.Task] = {}
    pending_confirmations: dict[str, dict] = {}
    last_signal_alert: dict[str, float] = {}

    def _now() -> float:
        return asyncio.get_event_loop().time()

    def confirmed(signal) -> bool:
        key = signal.asset
        prev = pending_confirmations.get(key)
        if not prev or prev.get("direction") != signal.signal.value:
            pending_confirmations[key] = {"direction": signal.signal.value, "count": 1, "confidence": signal.confidence}
            return cfg.confirmations_required <= 1
        prev["count"] += 1
        prev["confidence"] = signal.confidence
        return prev["count"] >= cfg.confirmations_required

    def should_alert_signal(signal) -> bool:
        if not cfg.signal_alerts:
            return False
        key = f"{signal.asset}:{signal.signal.value}"
        last = last_signal_alert.get(key, 0.0)
        if _now() - last < cfg.signal_alert_cooldown:
            return False
        last_signal_alert[key] = _now()
        return True

    print("\n" + "="*60)
    print("MULTI-PAIR POCKET OPTION BOT")
    print(cfg.summary())
    print("="*60)
    print("Telegram:", "ON" if telegram.enabled else "OFF")
    print("Trading starts OFF unless you pass --autostart or send /run")
    print("="*60 + "\n")

    async def refresh_balance_loop():
        while True:
            try:
                state.balance = await api.balance()
            except Exception:
                pass
            await asyncio.sleep(15)

    async def watch_asset(asset: str):
        try:
            print(f"[*] subscribing: {asset}")
            await tracker.watch(api, asset)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            state.subscription_errors[asset] = str(e)
            if telegram.enabled:
                await telegram.send(f"Subscription failed: {asset}\n{e}")
            print(f"[ERR] subscription failed {asset}: {e}")

    async def subscription_manager():
        while True:
            cfg.normalize_assets()

            # Stop watchers removed from selected assets.
            for asset in list(watcher_tasks.keys()):
                if asset not in cfg.assets:
                    watcher_tasks[asset].cancel()
                    watcher_tasks.pop(asset, None)
                    tracker.remove(asset)

            # Start missing watchers, respecting subscription cap.
            for asset in list(cfg.assets):
                if asset not in watcher_tasks and len(watcher_tasks) < cfg.max_subscriptions:
                    watcher_tasks[asset] = asyncio.create_task(watch_asset(asset))

            state.active_watchers = list(watcher_tasks.keys())
            await asyncio.sleep(2)

    async def scan_loop():
        print("[*] Scanner waiting for prices...")
        if telegram.enabled:
            await telegram.send("Scanner active. Use /menu, then /run to start trading.", telegram.main_keyboard())
        while True:
            analyzer.min_confidence = cfg.min_confidence
            analyzer.min_ticks = cfg.min_ticks_for_signal
            trader.update_runtime(cfg.amount, cfg.duration, cfg.cooldown, cfg.max_open_trades)

            active = list(watcher_tasks.keys())
            ready_count = 0
            for asset in active:
                ticks = tracker.ticks(asset)
                if len(ticks) >= cfg.min_ticks_for_signal:
                    ready_count += 1
                    signal = analyzer.evaluate(asset, ticks)
                    if signal.signal is not Signal.NONE:
                        line = f"{asset} {signal.signal.value} {signal.confidence:.1f}% {signal.reasons}"
                        state.last_signal = line

                        is_confirmed = confirmed(signal)
                        mark = "CONFIRMED" if is_confirmed else "candidate"
                        print("\n[SIGNAL]", mark, line)

                        if telegram.enabled and should_alert_signal(signal):
                            await telegram.send(
                                f"SIGNAL CANDIDATE\nAsset: {asset}\nDirection: {signal.signal.value}\nConfidence: {signal.confidence:.1f}%\n"
                                f"Confirm: {pending_confirmations.get(asset, {}).get('count', 0)}/{cfg.confirmations_required}\n"
                                f"Entry: {signal.entry_price:.5f}\nReasons: {', '.join(signal.reasons)}"
                            )

                        if cfg.auto_trade and not state.paused and is_confirmed:
                            await trader.execute(signal)
                            pending_confirmations.pop(asset, None)

            if active and ready_count == 0:
                print(f"\r[*] Collecting ticks... active={len(active)} selected={len(cfg.assets)}", end="")
            await asyncio.sleep(cfg.scan_interval)

    tasks = [
        asyncio.create_task(telegram.poll_loop()),
        asyncio.create_task(refresh_balance_loop()),
        asyncio.create_task(subscription_manager()),
        asyncio.create_task(scan_loop()),
    ]
    await asyncio.gather(*tasks)

def parse_args():
    p = argparse.ArgumentParser(description="Multi-pair Pocket Option Telegram Bot")
    p.add_argument("--ssid", type=str, default="")
    p.add_argument("--amount", type=float, default=None)
    p.add_argument("--duration", type=int, default=None)
    p.add_argument("--timeframe", type=int, default=None)
    p.add_argument("--confidence", type=float, default=None)
    p.add_argument("--cooldown", type=float, default=None)
    p.add_argument("--maxopen", type=int, default=None)
    p.add_argument("--maxsubs", type=int, default=None)
    p.add_argument("--assets", type=str, default="")
    p.add_argument("--autostart", action="store_true")
    p.add_argument("--token", type=str, default="")
    p.add_argument("--chatid", type=str, default="")
    return p.parse_args()

def main():
    args = parse_args()
    cfg = BotConfig()
    if args.amount is not None: cfg.amount = args.amount
    if args.duration is not None: cfg.duration = args.duration
    if args.timeframe is not None: cfg.timeframe = args.timeframe
    if args.confidence is not None: cfg.min_confidence = args.confidence
    if args.cooldown is not None: cfg.cooldown = args.cooldown
    if args.maxopen is not None: cfg.max_open_trades = args.maxopen
    if args.maxsubs is not None: cfg.max_subscriptions = args.maxsubs
    if args.assets: cfg.assets = [x.strip() for x in args.assets.split(",") if x.strip()]
    if args.autostart: cfg.auto_trade = True
    if args.token: cfg.telegram_token = args.token
    if args.chatid: cfg.telegram_chat_id = args.chatid
    asyncio.run(run_bot(args.ssid, cfg))

if __name__ == "__main__":
    main()
