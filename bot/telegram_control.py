from __future__ import annotations
import asyncio, urllib.parse, urllib.request, json
from dataclasses import dataclass
from typing import Any

@dataclass
class RuntimeState:
    running: bool = False
    paused: bool = True
    open_trades: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    balance: float | None = None
    last_signal: str = "None"
    active_watchers: list[str] = None
    subscription_errors: dict[str, str] = None

    def __post_init__(self):
        if self.active_watchers is None:
            self.active_watchers = []
        if self.subscription_errors is None:
            self.subscription_errors = {}

class TelegramControl:
    def __init__(self, token: str, chat_id: str, cfg, state: RuntimeState):
        self.token = token
        self.chat_id = str(chat_id) if chat_id else ""
        self.cfg = cfg
        self.state = state
        self.offset = 0
        self.enabled = bool(token and chat_id)

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    async def _post_json(self, method: str, payload: dict[str, Any]):
        if not self.enabled:
            return None
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._api_url(method), data=data, headers={"Content-Type":"application/json"})
        try:
            resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=8)
            return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    async def send(self, text: str, keyboard: dict | None = None):
        payload = {"chat_id": self.chat_id, "text": text}
        if keyboard:
            payload["reply_markup"] = keyboard
        return await self._post_json("sendMessage", payload)

    async def answer_callback(self, callback_id: str, text: str = ""):
        await self._post_json("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})

    async def _get_updates(self):
        if not self.enabled:
            return []
        url = self._api_url("getUpdates") + "?" + urllib.parse.urlencode({"timeout":5,"offset":self.offset})
        try:
            resp = await asyncio.to_thread(urllib.request.urlopen, url, timeout=10)
            return json.loads(resp.read().decode("utf-8")).get("result", [])
        except Exception:
            return []

    def main_keyboard(self) -> dict:
        return {"inline_keyboard": [
            [{"text":"تشغيل التداول", "callback_data":"run"}, {"text":"إيقاف التداول", "callback_data":"stop"}],
            [{"text":"الحالة", "callback_data":"status"}, {"text":"الأزواج", "callback_data":"assets"}],
            [{"text":"الإعدادات", "callback_data":"settings"}, {"text":"النتائج", "callback_data":"results"}],
        ]}

    def settings_keyboard(self) -> dict:
        return {"inline_keyboard": [
            [{"text":"Amount 1$", "callback_data":"amount:1"}, {"text":"Amount 2$", "callback_data":"amount:2"}, {"text":"Amount 5$", "callback_data":"amount:5"}],
            [{"text":"30s", "callback_data":"duration:30"}, {"text":"60s", "callback_data":"duration:60"}, {"text":"120s", "callback_data":"duration:120"}],
            [{"text":"MaxOpen 1", "callback_data":"maxopen:1"}, {"text":"MaxOpen 3", "callback_data":"maxopen:3"}, {"text":"MaxOpen 5", "callback_data":"maxopen:5"}],
            [{"text":"Confidence 80", "callback_data":"confidence:80"}, {"text":"86", "callback_data":"confidence:86"}, {"text":"92", "callback_data":"confidence:92"}],
            [{"text":"رجوع", "callback_data":"menu"}],
        ]}

    def assets_keyboard(self) -> dict:
        rows = []
        current = set(self.cfg.assets)
        for i, asset in enumerate(self.cfg.all_assets):
            mark = "✅" if asset in current else "☐"
            rows.append([{"text": f"{mark} {asset}", "callback_data": f"asset:{asset}"}])
        rows.append([
            {"text":"تطبيق/تحديث الاشتراكات", "callback_data":"apply_assets"},
            {"text":"رجوع", "callback_data":"menu"},
        ])
        return {"inline_keyboard": rows}

    async def poll_loop(self):
        if not self.enabled:
            print("[Telegram] disabled: token/chat_id missing")
            return
        await self.send("BOT ONLINE. Trading is OFF. استخدم الأزرار للتحكم.", self.main_keyboard())
        while True:
            for update in await self._get_updates():
                self.offset = max(self.offset, update.get("update_id", 0) + 1)
                if "callback_query" in update:
                    cb = update["callback_query"]
                    msg = cb.get("message") or {}
                    if str((msg.get("chat") or {}).get("id")) != self.chat_id:
                        continue
                    await self.handle_callback(cb.get("data", ""), cb.get("id", ""))
                    continue
                msg = update.get("message") or update.get("edited_message") or {}
                if str((msg.get("chat") or {}).get("id")) != self.chat_id:
                    continue
                text = (msg.get("text") or "").strip()
                if text:
                    await self.handle_text(text)
            await asyncio.sleep(self.cfg.telegram_poll_interval)

    async def handle_callback(self, data: str, callback_id: str):
        try:
            if data == "menu":
                await self.send("لوحة التحكم", self.main_keyboard())
            elif data == "run":
                self.cfg.auto_trade = True; self.state.paused = False
                await self.send("AUTO TRADE: ON", self.main_keyboard())
            elif data == "stop":
                self.cfg.auto_trade = False; self.state.paused = True
                await self.send("AUTO TRADE: OFF", self.main_keyboard())
            elif data == "status":
                await self.send(self.status_text(), self.main_keyboard())
            elif data == "results":
                await self.send(self.results_text(), self.main_keyboard())
            elif data == "settings":
                await self.send("الإعدادات", self.settings_keyboard())
            elif data == "assets":
                await self.send(self.assets_text(), self.assets_keyboard())
            elif data.startswith("asset:"):
                asset = data.split(":", 1)[1]
                await self.toggle_asset(asset)
                await self.send(self.assets_text(), self.assets_keyboard())
            elif data == "apply_assets":
                self.cfg.normalize_assets()
                await self.send("تم تطبيق الأزواج. البوت سيشترك بالأزواج الجديدة تدريجيًا. إذا ظهر Maximum subscriptions limit، قلل العدد.", self.assets_keyboard())
            elif ":" in data:
                key, value = data.split(":", 1)
                await self.set_value(key, value)
            await self.answer_callback(callback_id, "OK")
        except Exception as e:
            await self.answer_callback(callback_id, f"ERR: {e}")
            await self.send(f"Command error: {e}")

    async def toggle_asset(self, asset: str):
        if asset in self.cfg.assets:
            self.cfg.assets = [a for a in self.cfg.assets if a != asset]
            return
        if len(self.cfg.assets) >= self.cfg.max_subscriptions:
            await self.send(f"لا يمكن إضافة أكثر من {self.cfg.max_subscriptions} أزواج مباشرة. احذف زوجًا أولًا أو ارفع Max Subscriptions بحذر.")
            return
        self.cfg.assets.append(asset)

    async def set_value(self, key: str, value: str):
        if key == "amount": self.cfg.amount = float(value)
        elif key == "duration": self.cfg.duration = int(value)
        elif key == "maxopen": self.cfg.max_open_trades = int(value)
        elif key == "confidence": self.cfg.min_confidence = float(value)
        elif key == "cooldown": self.cfg.cooldown = float(value)
        elif key == "maxsubs": self.cfg.max_subscriptions = int(value)
        await self.send(f"تم التعديل: {key} = {value}", self.settings_keyboard())

    async def handle_text(self, text: str):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        try:
            if cmd in ["/start", "/help", "/menu"]: await self.send(self.help_text(), self.main_keyboard())
            elif cmd == "/status": await self.send(self.status_text(), self.main_keyboard())
            elif cmd == "/run": self.cfg.auto_trade=True; self.state.paused=False; await self.send("AUTO TRADE: ON", self.main_keyboard())
            elif cmd == "/stop": self.cfg.auto_trade=False; self.state.paused=True; await self.send("AUTO TRADE: OFF", self.main_keyboard())
            elif cmd == "/amount": self.cfg.amount=float(arg); await self.send(f"Amount set: ${self.cfg.amount}")
            elif cmd == "/duration": self.cfg.duration=int(arg); await self.send(f"Duration set: {self.cfg.duration}s")
            elif cmd == "/timeframe": self.cfg.timeframe=int(arg); await self.send(f"Timeframe set: {self.cfg.timeframe}s")
            elif cmd == "/confidence": self.cfg.min_confidence=float(arg); await self.send(f"Min confidence set: {self.cfg.min_confidence}%")
            elif cmd == "/cooldown": self.cfg.cooldown=float(arg); await self.send(f"Cooldown set: {self.cfg.cooldown}s")
            elif cmd == "/maxopen": self.cfg.max_open_trades=int(arg); await self.send(f"Max open trades set: {self.cfg.max_open_trades}")
            elif cmd == "/maxsubs": self.cfg.max_subscriptions=int(arg); self.cfg.normalize_assets(); await self.send(f"Max subscriptions set: {self.cfg.max_subscriptions}")
            elif cmd == "/assets": await self.send(self.assets_text(), self.assets_keyboard())
            elif cmd == "/setassets":
                assets=[x.strip() for x in arg.split(",") if x.strip()]
                self.cfg.assets=assets[: self.cfg.max_subscriptions]
                await self.send(self.assets_text(), self.assets_keyboard())
            elif cmd == "/ssid": self.cfg.ssid=arg; await self.send("SSID saved in runtime memory.")
            else: await self.send("Unknown command. Use /menu", self.main_keyboard())
        except Exception as e:
            await self.send(f"Command error: {e}")

    def help_text(self) -> str:
        return (
            "لوحة أوامر البوت\n"
            "/menu - أزرار التحكم\n/status - الحالة\n/run - تشغيل التداول\n/stop - إيقاف التداول\n"
            "/amount 1\n/duration 60\n/confidence 86\n/maxopen 3\n/maxsubs 3\n/assets\n"
            "/setassets EURUSD_otc,GBPUSD_otc,USDJPY_otc"
        )

    def assets_text(self) -> str:
        return (
            f"الأزواج المختارة: {len(self.cfg.assets)}/{self.cfg.max_subscriptions}\n"
            f"{', '.join(self.cfg.assets) if self.cfg.assets else 'None'}\n\n"
            f"المشتركة فعليًا الآن: {', '.join(self.state.active_watchers) if self.state.active_watchers else 'None'}\n"
            "اضغط على الزوج لإضافته/حذفه."
        )

    def status_text(self) -> str:
        rate=(self.state.wins/self.state.total_trades*100) if self.state.total_trades else 0
        return (
            f"STATUS\n"
            f"Auto Trade: {'ON' if self.cfg.auto_trade else 'OFF'}\n"
            f"Balance: {self.state.balance}\n"
            f"Amount: ${self.cfg.amount}\n"
            f"Duration: {self.cfg.duration}s\n"
            f"Timeframe: {self.cfg.timeframe}s\n"
            f"Confidence: {self.cfg.min_confidence}%\n"
            f"Open Trades: {self.state.open_trades}/{self.cfg.max_open_trades}\n"
            f"Trades: {self.state.total_trades}\n"
            f"W/L/D: {self.state.wins}/{self.state.losses}/{self.state.draws}\n"
            f"Win Rate: {rate:.1f}%\n"
            f"Last Signal: {self.state.last_signal}\n"
            f"Selected Assets: {', '.join(self.cfg.assets)}\n"
            f"Active Watchers: {', '.join(self.state.active_watchers)}"
        )

    def results_text(self) -> str:
        rate=(self.state.wins/self.state.total_trades*100) if self.state.total_trades else 0
        return f"RESULTS\nTotal: {self.state.total_trades}\nWins: {self.state.wins}\nLosses: {self.state.losses}\nDraws: {self.state.draws}\nWin Rate: {rate:.1f}%"
