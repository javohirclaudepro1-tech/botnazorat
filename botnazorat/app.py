"""Bot Nazorat: botlaringizga kim kirdi, kim bloklab chiqib ketdi, kim faol — hammasini ko'rsatadi.

Ishlash tartibi:
  * Boshqaruv boti (CONTROL_TOKEN) — siz bilan gaplashadigan bot (long polling).
  * Kuzatilayotgan bot tokeni ulanganda, uning webhook'i shu server orqali o'tkaziladi:
    Telegram -> /hook/<bot_id> -> statistikaga yoziladi -> asl serveringizga o'zgarishsiz uzatiladi.
  * Bot polling rejimida ishlasa, "tekshiruv rejimi" ulanadi (ID ro'yxati bo'yicha bloklaganlarni aniqlash).
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qsl

import aiohttp
from aiohttp import web

from . import reports, tracker
from .db import DB
from .tg import Bot, TgError
from .util import dec, enc, esc, now_local, user_label

log = logging.getLogger("nazorat")
HERE = Path(__file__).parent
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,}$")
EXTRA_TYPES = ["my_chat_member", "message", "callback_query"]


def kb(*rows):
    return {"inline_keyboard": [[b for b in row if b] for row in rows if row]}


def btn(text, data=None, url=None, web_app=None):
    b = {"text": text}
    if web_app:
        b["web_app"] = {"url": web_app}
    elif url:
        b["url"] = url
    else:
        b["callback_data"] = data
    return b


class App:
    def __init__(self, cfg=None):
        e = cfg or os.environ
        if not e.get("CONTROL_TOKEN"):
            raise SystemExit("❌ CONTROL_TOKEN berilmagan. .env faylini to'ldiring (README.md ga qarang).")
        self.control_token = e["CONTROL_TOKEN"]
        self.public_url = e.get("PUBLIC_URL", "").rstrip("/")
        self.api = e.get("TG_API", "https://api.telegram.org").rstrip("/")
        self.allowed = {int(x) for x in re.split(r"[,\s]+", e.get("ALLOWED_USERS", "")) if x.strip()}
        self.report_hour = int(e.get("REPORT_HOUR", "21"))
        self.port = int(e.get("PORT", "8080"))
        self.db = DB(e.get("DB_PATH", "data/nazorat.db"))
        self.state = {}      # owner_id -> (holat, ma'lumot)
        self.pending = {}    # owner_id -> {token, me, info}
        self.scans = set()
        self.session = None
        self.cb = None
        self.nq = asyncio.Queue()
        self.tasks = []

    # ------------------------------------------------------------------ yordamchi
    def hook_url(self, bot_id):
        return f"{self.public_url}/hook/{bot_id}"

    def target(self, row_or_token):
        token = row_or_token if isinstance(row_or_token, str) else dec(row_or_token["token_enc"])
        return Bot(token, self.session, self.api)

    def ok_user(self, uid):
        return not self.allowed or uid in self.allowed

    def notify(self, chat_id, text, kb_=None):
        self.nq.put_nowait((chat_id, text, kb_))

    async def notifier(self):
        while True:
            chat_id, text, k = await self.nq.get()
            if self.nq.qsize() > 30:  # juda ko'p hodisa bo'lsa, qisqa xulosa
                extra = 0
                while not self.nq.empty():
                    self.nq.get_nowait()
                    extra += 1
                text += f"\n\n… va yana <b>{extra}</b> ta hodisa. To'liq ro'yxat: 📊 Statistika bo'limida."
            try:
                await self.cb.send(chat_id, text, k)
            except TgError as e:
                if e.code == 429:
                    await asyncio.sleep(int(re.findall(r"\d+", e.desc)[-1]) if re.findall(r"\d+", e.desc) else 5)
            except Exception:
                log.exception("notify")
            await asyncio.sleep(0.35)

    # ------------------------------------------------------------------ webhook proxy
    async def hook(self, request):
        try:
            bot_id = int(request.match_info["bot_id"])
        except ValueError:
            return web.Response(status=404)
        b = self.db.bot(bot_id)
        if not b or not b["active"] or not hmac.compare_digest(
                request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), b["hook_secret"] or ""):
            return web.Response(status=403)
        raw = await request.read()
        try:
            upd = json.loads(raw)
        except ValueError:
            return web.Response(status=400)
        try:  # statistika xatosi hech qachon asl botga yetkazishga xalaqit bermasin
            for kind, u, extra in tracker.process(self.db, bot_id, upd):
                if b["notify"]:
                    self.notify(b["owner_id"], self.event_text(b, kind, u, extra))
        except Exception:
            log.exception("track")
        if not b["orig_url"] or not tracker.forward_allowed(upd, json.loads(b["orig_allowed"] or "null")):
            return web.json_response({})
        headers = {"Content-Type": "application/json"}
        osec = dec(b["orig_secret_enc"])
        if osec:
            headers["X-Telegram-Bot-Api-Secret-Token"] = osec
        try:
            async with self.session.post(b["orig_url"], data=raw, headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=55)) as r:
                body = await r.read()
                if r.status in (401, 403):
                    self.forward_failed(b)
                elif b["fwd_fail"]:
                    self.db.update_bot(bot_id, fwd_fail=0)
                ct = r.headers.get("Content-Type", "application/json")
                return web.Response(status=r.status, body=body, headers={"Content-Type": ct})
        except Exception as ex:
            log.warning("forward %s: %s", bot_id, ex)
            return web.Response(status=502)

    def forward_failed(self, b):
        n = b["fwd_fail"] + 1
        self.db.update_bot(b["id"], fwd_fail=n)
        if n == 3:
            self.notify(b["owner_id"],
                        f"⚠️ <b>@{esc(b['username'])}</b>: asl serveringiz xabarlarni qabul qilmayapti (401/403).\n"
                        "Ehtimol, webhook'ingizda <b>maxfiy kalit (secret_token)</b> bor. Kalitni kiriting "
                        "yoki botni asl holatiga qaytaring.",
                        kb([btn("🔑 Kalitni kiritish", f"secy:{b['id']}")], [btn("↩️ Asl holatga qaytarish", f"dcy:{b['id']}")]))

    def event_text(self, b, kind, u, extra):
        name = esc(" ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or "—")
        un = f" (@{esc(u['username'])})" if u.get("username") else ""
        who = f'<a href="tg://user?id={u["id"]}">{name}</a>{un}'
        t = now_local().strftime("%H:%M")
        if kind == "new":
            total = self.db.q1("SELECT COUNT(*) FROM users WHERE bot_id=?", b["id"])
            src = f"\n📣 Manba: <code>{esc(extra)}</code>" if extra else ""
            return f"🆕 <b>Yangi foydalanuvchi</b> · @{esc(b['username'])}\n{who}\n🕒 {t} · jami: <b>{total}</b>{src}"
        if kind == "block":
            return f"⛔ <b>Botni blokladi</b> · @{esc(b['username'])}\n{who}\n🕒 {t}"
        return f"🔄 <b>Qaytib keldi</b> · @{esc(b['username'])}\n{who}\n🕒 {t}"

    # ------------------------------------------------------------------ ulash / uzish
    async def start_connect(self, owner, token):
        tb = self.target(token)
        try:
            me = await tb.call("getMe")
        except TgError:
            return "❌ Token noto'g'ri yoki bekor qilingan. @BotFather'dan yangisini oling."
        if token == self.control_token:
            return "❌ Bu nazorat botining o'zi. Kuzatmoqchi bo'lgan botingiz tokenini yuboring."
        old = self.db.bot(me["id"])
        if old and old["active"] and old["owner_id"] != owner:
            return "❌ Bu bot boshqa akkauntga ulangan."
        info = await tb.call("getWebhookInfo")
        url = info.get("url") or ""
        if url == self.hook_url(me["id"]) and old:
            # allaqachon biz orqali — faqat tokenni yangilaymiz
            self.db.update_bot(me["id"], token_enc=enc(token), active=1, owner_id=owner,
                               username=me.get("username"), name=me.get("first_name"))
            return ("ok", me["id"])
        if not url or not self.public_url.startswith("https://"):
            self.db.save_bot(id=me["id"], owner_id=owner, username=me.get("username"), name=me.get("first_name"),
                             token_enc=enc(token), mode="scan", hook_secret=None, orig_url=None)
            return ("scan", me["id"])
        self.pending[owner] = {"token": token, "me": me, "info": info}
        return ("ask_secret", me)

    async def finish_proxy(self, owner, secret=None):
        p = self.pending.pop(owner, None)
        if not p:
            return None
        me, info, token = p["me"], p["info"], p["token"]
        allowed = info.get("allowed_updates") or None
        hook_secret = secrets.token_urlsafe(32)
        our_allowed = sorted(set(allowed) | set(EXTRA_TYPES)) if allowed else []
        old = self.db.bot(me["id"])
        self.db.save_bot(id=me["id"], owner_id=owner, username=me.get("username"), name=me.get("first_name"),
                         token_enc=enc(token), mode="proxy", orig_url=info["url"], orig_secret_enc=enc(secret),
                         orig_allowed=json.dumps(allowed) if allowed else None,
                         orig_max_conn=info.get("max_connections") or 40, hook_secret=hook_secret, fwd_fail=0,
                         notify=old["notify"] if old else 1, active=1)
        await self.target(token).call("setWebhook", {
            "url": self.hook_url(me["id"]), "secret_token": hook_secret, "allowed_updates": our_allowed,
            "max_connections": info.get("max_connections") or 40})
        return me["id"]

    async def disconnect(self, b):
        tb = self.target(b)
        if b["mode"] == "proxy" and b["orig_url"]:
            await tb.call("setWebhook", {
                "url": b["orig_url"], "secret_token": dec(b["orig_secret_enc"]),
                "allowed_updates": json.loads(b["orig_allowed"]) if b["orig_allowed"] else [],
                "max_connections": b["orig_max_conn"] or 40})
        self.db.update_bot(b["id"], active=0, token_enc="", hook_secret=None)

    async def watchdog(self):
        """Har 5 daqiqada webhook'larni tekshiradi."""
        while True:
            await asyncio.sleep(300)
            await self.check_webhooks()

    async def check_webhooks(self):
        """Asl kod webhook'ni qayta o'rnatib qo'ysa, kuzatuvni tiklaydi."""
        for b in self.db.active_bots():
            if b["mode"] != "proxy":
                continue
            try:
                info = await self.target(b).call("getWebhookInfo")
            except TgError as e:
                if e.code == 401:
                    self.db.update_bot(b["id"], active=0)
                    self.notify(b["owner_id"], f"⚠️ @{esc(b['username'])} tokeni bekor qilingan. Kuzatuv to'xtadi — qayta ulang.")
                continue
            except Exception:
                continue
            url = info.get("url") or ""
            if url == self.hook_url(b["id"]):
                continue
            if not url:
                self.db.update_bot(b["id"], mode="scan", orig_url=None)
                self.notify(b["owner_id"], f"ℹ️ @{esc(b['username'])} polling rejimiga o'tdi. Real vaqt kuzatuvi to'xtadi, "
                                           "faqat tekshiruv rejimi ishlaydi.")
                continue
            allowed = info.get("allowed_updates") or None
            self.db.update_bot(b["id"], orig_url=url, orig_allowed=json.dumps(allowed) if allowed else None,
                               orig_max_conn=info.get("max_connections") or 40)
            await self.target(b).call("setWebhook", {
                "url": self.hook_url(b["id"]), "secret_token": b["hook_secret"],
                "allowed_updates": sorted(set(allowed) | set(EXTRA_TYPES)) if allowed else [],
                "max_connections": info.get("max_connections") or 40})
            self.notify(b["owner_id"], f"🔁 @{esc(b['username'])} webhook'i qayta o'rnatilgan ekan — kuzatuv tiklandi.")

    # ------------------------------------------------------------------ bloklarni tekshirish
    async def scan(self, b, chat_id, msg_id):
        if b["id"] in self.scans:
            return
        self.scans.add(b["id"])
        try:
            tb = self.target(b)
            ids = [r[0] for r in self.db.q("SELECT user_id FROM users WHERE bot_id=? AND status!='deleted'", b["id"])]
            res = {"ok": 0, "blocked": 0, "new_blocked": 0, "back": 0, "deleted": 0, "missing": 0}
            sem = asyncio.Semaphore(20)
            last = [0.0]

            async def one(uid):
                async with sem:
                    for _ in range(3):
                        try:
                            await tb.call("sendChatAction", {"chat_id": uid, "action": "typing"}, timeout=20)
                            row = self.db.get_user(b["id"], uid)
                            if row["status"] == "blocked":
                                self.db.set_status(b["id"], uid, "active")
                                self.db.add_event(b["id"], uid, "unblock", int(time.time()))
                                res["back"] += 1
                            res["ok"] += 1
                            break
                        except TgError as e:
                            d = e.desc.lower()
                            if e.code == 429:
                                n = re.findall(r"\d+", e.desc)
                                await asyncio.sleep(int(n[-1]) if n else 3)
                                continue
                            if "blocked" in d:
                                row = self.db.get_user(b["id"], uid)
                                if row["status"] != "blocked":
                                    self.db.set_status(b["id"], uid, "blocked", int(time.time()))
                                    self.db.add_event(b["id"], uid, "block", int(time.time()), {"scan": True})
                                    res["new_blocked"] += 1
                                res["blocked"] += 1
                            elif "deactivated" in d:
                                self.db.set_status(b["id"], uid, "deleted")
                                res["deleted"] += 1
                            else:
                                res["missing"] += 1
                            break
                        except Exception:
                            await asyncio.sleep(1)
                    await asyncio.sleep(0.05)
                    done = sum(res.values()) - res["new_blocked"] - res["back"]
                    if time.time() - last[0] > 2.5:
                        last[0] = time.time()
                        try:
                            await self.cb.edit(chat_id, msg_id, f"🔍 Tekshirilmoqda… <b>{done}</b> / {len(ids)}")
                        except Exception:
                            pass

            await asyncio.gather(*(one(u) for u in ids))
            text = (f"✅ <b>Tekshiruv tugadi</b> · @{esc(b['username'])}\n\n"
                    f"👥 Tekshirildi: <b>{len(ids)}</b>\n"
                    f"✅ Botda bor: <b>{res['ok']}</b>\n"
                    f"⛔ Bloklagan: <b>{res['blocked']}</b>  (shundan yangi aniqlangan: {res['new_blocked']})\n"
                    f"🔄 Qaytganlar: <b>{res['back']}</b>\n"
                    f"🗑 O'chirilgan akkaunt: <b>{res['deleted']}</b>\n"
                    f"❔ Botga hech yozmagan: <b>{res['missing']}</b>")
            await self.cb.edit(chat_id, msg_id, text, kb([btn("⛔ Bloklaganlar", f"bl:{b['id']}"), btn("⬅️ Bot", f"b:{b['id']}")]))
        finally:
            self.scans.discard(b["id"])

    # ------------------------------------------------------------------ boshqaruv boti
    def main_menu(self, uid):
        bots = self.db.bots_of(uid)
        text = ("👋 <b>Bot Nazorat</b>\n\n"
                "Botingizga <b>kim kirdi</b>, <b>kim bloklab chiqib ketdi</b>, <b>kim faol</b> ishlatyapti — "
                "hammasini shu yerda ko'rasiz.\n\n")
        text += f"🤖 Ulangan botlar: <b>{len(bots)}</b>" if bots else "Boshlash uchun botingizni ulang 👇"
        rows = [[btn(f"🤖 @{b['username']}", f"b:{b['id']}")] for b in bots]
        rows.append([btn("➕ Bot ulash", "add")])
        if bots and self.public_url.startswith("https://"):
            rows.append([btn("📱 Panel (Mini App)", web_app=f"{self.public_url}/panel")])
        rows.append([btn("❓ Qanday ishlaydi", "help")])
        return text, {"inline_keyboard": rows}

    def bot_card(self, b):
        n = reports.numbers(self.db, b["id"])
        mode = ("🟢 To'liq kuzatuv (real vaqt)" if b["mode"] == "proxy"
                else "🟡 Tekshiruv rejimi (bot polling'da ishlaydi)")
        text = (f"🤖 <b>{esc(b['name'])}</b> · @{esc(b['username'])}\n{mode}\n\n"
                f"👥 Jami: <b>{n['total']}</b>   ✅ Tirik: <b>{n['alive']}</b>   ⛔ Blok: <b>{n['blocked']}</b>\n"
                f"🆕 Bugun kirdi: <b>{n['new_today']}</b>   🔥 Bugun faol: <b>{n['dau']}</b>   🚪 Bugun chiqdi: <b>{n['blk_today']}</b>")
        i = b["id"]
        rows = [
            [btn("📊 Statistika", f"st:{i}"), btn("📈 Grafik", f"ch:{i}")],
            [btn("🆕 Kirganlar", f"nw:{i}"), btn("⛔ Bloklaganlar", f"bl:{i}")],
            [btn("🔥 Faollar", f"ac:{i}"), btn("📣 Manbalar", f"src:{i}")],
            [btn("🔍 Bloklarni tekshirish", f"scan:{i}"), btn("📥 ID import", f"imp:{i}")],
            [btn("📁 Excel", f"xl:{i}"), btn("🔔 Xabarlar: " + ("yoqiq" if b["notify"] else "o'chiq"), f"nt:{i}")],
        ]
        if self.public_url.startswith("https://"):
            rows.append([btn("📱 Panelda ochish", web_app=f"{self.public_url}/panel?bot={i}")])
        rows.append([btn("❌ Uzish", f"dc:{i}"), btn("⬅️ Orqaga", "m")])
        return text, {"inline_keyboard": rows}

    HELP = ("❓ <b>Qanday ishlaydi</b>\n\n"
            "1️⃣ <b>➕ Bot ulash</b> ni bosing va botingiz tokenini yuboring "
            "(@BotFather → /mybots → bot → API Token).\n\n"
            "2️⃣ Agar botingiz <b>webhook</b> orqali ishlasa — <b>to'liq kuzatuv</b> yoqiladi: har bir /start, "
            "har bir bloklash va faollik real vaqtda yoziladi. Xabarlar asl serveringizga o'zgarishsiz yetib boradi, "
            "botingiz avvalgidek ishlayveradi.\n\n"
            "3️⃣ Agar bot <b>polling</b> rejimida bo'lsa — <b>tekshiruv rejimi</b>: foydalanuvchilar ID ro'yxatini yuborasiz, "
            "men kim bloklaganini aniqlab beraman.\n\n"
            "📣 <b>Manbalar:</b> reklamada t.me/botingiz?start=<b>insta1</b> kabi havola bersangiz, "
            "qaysi reklamadan nechta odam kelganini ko'rasiz.\n\n"
            "🔒 Tokenlar shifrlangan holda saqlanadi. Uzganingizda bot asl holatiga qaytariladi.")

    async def handle(self, upd):
        try:
            if "callback_query" in upd:
                await self.on_cb(upd["callback_query"])
            elif "message" in upd:
                await self.on_msg(upd["message"])
        except Exception:
            log.exception("handle")

    async def on_msg(self, m):
        if m.get("chat", {}).get("type") != "private":
            return
        uid = m["from"]["id"]
        chat = m["chat"]["id"]
        if not self.ok_user(uid):
            await self.cb.send(chat, "⛔ Bu bot shaxsiy. Sizga ruxsat berilmagan.")
            return
        text = (m.get("text") or "").strip()
        st, data = self.state.get(uid, (None, None))

        if text.startswith("/start") or text == "/menu":
            self.state.pop(uid, None)
            t, k = self.main_menu(uid)
            await self.cb.send(chat, t, k)
            return
        if text == "/help":
            await self.cb.send(chat, self.HELP, kb([btn("⬅️ Menyu", "m")]))
            return

        if st == "await_token" or (TOKEN_RE.match(text) and not st):
            if not TOKEN_RE.match(text):
                await self.cb.send(chat, "🤔 Bu token emasga o'xshaydi. Token shunday ko'rinadi:\n<code>7123456789:AAH...</code>\n\nQayta yuboring yoki /start")
                return
            try:
                await self.cb.call("deleteMessage", {"chat_id": chat, "message_id": m["message_id"]})
            except TgError:
                pass
            self.state.pop(uid, None)
            wait = await self.cb.send(chat, "⏳ Tekshirilmoqda…")
            r = await self.start_connect(uid, text)
            if isinstance(r, str):
                await self.cb.edit(chat, wait["message_id"], r, kb([btn("🔁 Qayta urinish", "add"), btn("⬅️ Menyu", "m")]))
            elif r[0] == "ok":
                t, k = self.bot_card(self.db.bot(r[1]))
                await self.cb.edit(chat, wait["message_id"], "✅ Token yangilandi.\n\n" + t, k)
            elif r[0] == "scan":
                b = self.db.bot(r[1])
                await self.cb.edit(chat, wait["message_id"],
                                   f"✅ <b>@{esc(b['username'])}</b> ulandi — 🟡 <b>tekshiruv rejimi</b>.\n\n"
                                   "Botingiz hozir <b>polling</b> rejimida ishlayapti (yoki o'chiq). Bu holatda Telegram "
                                   "xabarlarni ikki joyga bera olmaydi, shuning uchun real vaqt kuzatuvi o'rniga:\n"
                                   "📥 foydalanuvchilar ID ro'yxatini yuborasiz → 🔍 kim bloklaganini aniqlab beraman.\n\n"
                                   "💡 To'liq kuzatuv uchun botingizni webhook rejimiga o'tkazib, qayta ulang.",
                                   kb([btn("📥 ID ro'yxat yuborish", f"imp:{b['id']}")], [btn("🤖 Bot sahifasi", f"b:{b['id']}")]))
            else:
                me = r[1]
                await self.cb.edit(chat, wait["message_id"],
                                   f"🤖 <b>@{esc(me.get('username'))}</b> topildi — webhook rejimida ishlayapti ✅\n\n"
                                   "Oxirgi savol: webhook'ingizda <b>maxfiy kalit</b> (secret_token) bormi?\n"
                                   "<i>Kodingizda WEBHOOK_SECRET yoki secret_token deb yozilgan qiymat. "
                                   "Bilmasangiz — dasturchingizdan so'rang. Noto'g'ri tanlansa, bot javob bermay qolishi mumkin.</i>",
                                   kb([btn("✅ Kalit yo'q", "secn")], [btn("🔑 Kalit bor — yuboraman", "secy:0")], [btn("✖️ Bekor qilish", "m")]))
            return

        if st == "await_secret":
            try:
                await self.cb.call("deleteMessage", {"chat_id": chat, "message_id": m["message_id"]})
            except TgError:
                pass
            self.state.pop(uid, None)
            if data:  # mavjud botga kalit qo'shish (forward 401/403 holati)
                b = self.db.bot(data)
                if b and b["owner_id"] == uid:
                    self.db.update_bot(data, orig_secret_enc=enc(text), fwd_fail=0)
                    await self.cb.send(chat, "✅ Kalit saqlandi. Endi xabarlar asl serveringizga kalit bilan uzatiladi.",
                                       kb([btn("🤖 Bot sahifasi", f"b:{data}")]))
                return
            bid = await self.finish_proxy(uid, text)
            await self.after_proxy(chat, bid)
            return

        if st == "await_import":
            ids = []
            if m.get("document"):
                f = await self.cb.call("getFile", {"file_id": m["document"]["file_id"]})
                async with self.session.get(f"{self.api}/file/bot{self.control_token}/{f['file_path']}") as r:
                    raw = (await r.read()).decode("utf-8", "ignore")
            else:
                raw = text
            ids = sorted({int(x) for x in re.findall(r"(?<![\d.:\-])\d{4,15}(?![\d.:\-])", raw)})
            if not ids:
                await self.cb.send(chat, "🤔 ID topilmadi. Har qatorda bitta raqamli ID bo'lsin (txt yoki csv fayl).")
                return
            self.state.pop(uid, None)
            n = self.db.import_ids(data, ids)
            await self.cb.send(chat, f"📥 <b>{len(ids)}</b> ta ID qabul qilindi, shundan <b>{n}</b> tasi yangi.\n\n"
                                     "Endi kim bloklaganini tekshiraymi?",
                               kb([btn("🔍 Tekshirish", f"scan:{data}")], [btn("🤖 Bot sahifasi", f"b:{data}")]))
            return

        t, k = self.main_menu(uid)
        await self.cb.send(chat, t, k)

    async def after_proxy(self, chat, bid):
        if not bid:
            await self.cb.send(chat, "⏱ Vaqt o'tib ketdi, tokenni qayta yuboring.", kb([btn("➕ Bot ulash", "add")]))
            return
        b = self.db.bot(bid)
        t, k = self.bot_card(b)
        await self.cb.send(chat, f"🎉 <b>@{esc(b['username'])}</b> ulandi — 🟢 <b>to'liq kuzatuv</b> yoqildi!\n"
                                 "Endi kimdir /start bossa yoki bloklasa, darhol xabar beraman.\n\n"
                                 "💡 Avvalgi foydalanuvchilaringiz ro'yxati bo'lsa, 📥 ID import orqali yuboring — "
                                 "ulardan kim bloklaganini ham aniqlab beraman.\n\n" + t, k)

    async def on_cb(self, q):
        uid = q["from"]["id"]
        msg = q.get("message") or {}
        chat = msg.get("chat", {}).get("id", uid)
        mid = msg.get("message_id")
        data = q.get("data") or ""
        ans = {"callback_query_id": q["id"]}
        if not self.ok_user(uid):
            await self.cb.call("answerCallbackQuery", {**ans, "text": "Ruxsat yo'q", "show_alert": True})
            return
        act, _, arg = data.partition(":")
        b = None
        if arg.isdigit() and int(arg):
            b = self.db.bot(int(arg))
            if not b or b["owner_id"] != uid or not b["active"]:
                await self.cb.call("answerCallbackQuery", {**ans, "text": "Bot topilmadi", "show_alert": True})
                return
        toast = None
        back = kb([btn("⬅️ Bot", f"b:{arg}")])
        if act in ("ch", "xl"):  # og'ir amallardan oldin tugma "soat"ini to'xtatamiz
            await self.cb.call("answerCallbackQuery", {**ans, "text": "⏳ Tayyorlanmoqda…"})
            ans = None

        if act == "m":
            self.state.pop(uid, None)
            self.pending.pop(uid, None)
            await self.cb.edit(chat, mid, *self.main_menu(uid))
        elif act == "help":
            await self.cb.edit(chat, mid, self.HELP, kb([btn("⬅️ Menyu", "m")]))
        elif act == "add":
            self.state[uid] = ("await_token", None)
            await self.cb.edit(chat, mid, "🔑 <b>Botingiz tokenini yuboring</b>\n\n"
                                          "Qayerdan olinadi: @BotFather → /mybots → botingiz → <b>API Token</b>\n"
                                          "Ko'rinishi: <code>7123456789:AAHk3x9…</code>\n\n"
                                          "🔒 Xabaringizni darhol o'chiraman, token shifrlangan holda saqlanadi.",
                               kb([btn("✖️ Bekor qilish", "m")]))
        elif act == "secn":
            bid = await self.finish_proxy(uid, None)
            await self.cb.call("deleteMessage", {"chat_id": chat, "message_id": mid})
            await self.after_proxy(chat, bid)
        elif act == "secy":
            self.state[uid] = ("await_secret", int(arg) if arg.isdigit() and int(arg) else None)
            await self.cb.send(chat, "🔑 Maxfiy kalitni (secret_token) yuboring. Xabaringizni darhol o'chiraman.")
        elif act == "b":
            await self.cb.edit(chat, mid, *self.bot_card(b))
        elif act == "st":
            await self.cb.edit(chat, mid, reports.stats_text(self.db, b), kb([btn("🔄 Yangilash", f"st:{arg}"), btn("⬅️ Bot", f"b:{arg}")]))
        elif act == "nw":
            await self.cb.edit(chat, mid, reports.list_new(self.db, b), back)
        elif act == "bl":
            await self.cb.edit(chat, mid, reports.list_blocked(self.db, b), back)
        elif act == "ac":
            await self.cb.edit(chat, mid, reports.list_active(self.db, b), back)
        elif act == "src":
            await self.cb.edit(chat, mid, reports.list_sources(self.db, b), back)
        elif act == "ch":
            png = await asyncio.to_thread(reports.chart_png, self.db, b)
            n = reports.numbers(self.db, b["id"])
            await self.cb.call("sendPhoto", {"chat_id": chat, "caption":
                                             f"📈 @{esc(b['username'])} · 30 kun\n🟣 yangi: {n['new_30']}  🔴 bloklagan: {n['blk_30']}  🟢 faol: {n['mau']}",
                                             "parse_mode": "HTML", "reply_markup": back}, files={"photo": ("grafik.png", png)})
        elif act == "xl":
            data_ = await asyncio.to_thread(reports.excel, self.db, b)
            fname = f"{b['username']}_{now_local().strftime('%Y-%m-%d')}.xlsx"
            await self.cb.call("sendDocument", {"chat_id": chat, "caption": f"📁 @{esc(b['username'])} — barcha foydalanuvchilar",
                                                "parse_mode": "HTML"}, files={"document": (fname, data_)})
        elif act == "nt":
            self.db.update_bot(b["id"], notify=0 if b["notify"] else 1)
            toast = "🔔 Xabarlar yoqildi" if not b["notify"] else "🔕 Xabarlar o'chirildi"
            await self.cb.edit(chat, mid, *self.bot_card(self.db.bot(b["id"])))
        elif act == "imp":
            self.state[uid] = ("await_import", b["id"])
            await self.cb.send(chat, "📥 <b>Foydalanuvchilar ID ro'yxatini yuboring</b>\n\n"
                                     "• .txt yoki .csv fayl (har qatorda bitta ID), yoki\n"
                                     "• oddiy xabar qilib, ID'larni vergul/qator bilan.\n\n"
                                     "<i>Bu ro'yxat odatda botingiz bazasida bo'ladi (users jadvali).</i>",
                               kb([btn("✖️ Bekor qilish", f"b:{arg}")]))
        elif act == "scan":
            cnt = self.db.q1("SELECT COUNT(*) FROM users WHERE bot_id=? AND status!='deleted'", b["id"])
            if not cnt:
                toast = "Avval 📥 ID import qiling"
            elif b["id"] in self.scans:
                toast = "Tekshiruv allaqachon ketyapti"
            else:
                await self.cb.edit(chat, mid, f"🔍 Tekshiruv boshlandi: <b>{cnt}</b> ta foydalanuvchi…\n"
                                              "<i>Har biriga bir lahzalik “yozmoqda…” holati yuboriladi — xabar yuborilmaydi.</i>")
                asyncio.create_task(self.scan(b, chat, mid))
        elif act == "dc":
            await self.cb.edit(chat, mid, f"❌ <b>@{esc(b['username'])}</b> ni uzaymi?\n\n"
                                          "Webhook asl manziliga qaytariladi, yig'ilgan statistika saqlanib qoladi.",
                               kb([btn("✅ Ha, uzish", f"dcy:{arg}"), btn("✖️ Yo'q", f"b:{arg}")]))
        elif act == "dcy":
            try:
                await self.disconnect(b)
                await self.cb.edit(chat, mid, f"✅ @{esc(b['username'])} uzildi va asl holatiga qaytarildi.", kb([btn("⬅️ Menyu", "m")]))
            except TgError as e:
                await self.cb.edit(chat, mid, f"⚠️ Xatolik: {esc(e.desc)}", back)
        if ans:
            await self.cb.call("answerCallbackQuery", {**ans, "text": toast} if toast else ans)

    async def poll(self):
        await self.cb.call("deleteWebhook", {"drop_pending_updates": False})
        me = await self.cb.call("getMe")
        log.info("Boshqaruv boti: @%s", me["username"])
        await self.cb.call("setMyCommands", {"commands": [{"command": "start", "description": "🏠 Menyu"},
                                                          {"command": "help", "description": "❓ Qanday ishlaydi"}]})
        offset = 0
        while True:
            try:
                ups = await self.cb.call("getUpdates", {"offset": offset, "timeout": 50,
                                                        "allowed_updates": ["message", "callback_query"]}, timeout=70)
                for u in ups:
                    offset = u["update_id"] + 1
                    asyncio.create_task(self.handle(u))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("poll: %s", e)
                await asyncio.sleep(3)

    # ------------------------------------------------------------------ kunlik hisobot
    async def daily_report(self):
        while True:
            await asyncio.sleep(60)
            now = now_local()
            day = now.strftime("%Y-%m-%d")
            if now.hour != self.report_hour or self.db.kv_get("report_day") == day:
                continue
            self.db.kv_set("report_day", day)
            for b in self.db.active_bots():
                if not b["notify"]:
                    continue
                n = reports.numbers(self.db, b["id"])
                txt = (f"🌙 <b>Kunlik hisobot</b> · @{esc(b['username'])}\n\n"
                       f"🆕 Bugun kirdi: <b>{n['new_today']}</b>\n"
                       f"🔥 Bugun faol: <b>{n['dau']}</b>\n"
                       f"🚪 Bugun bloklab chiqdi: <b>{n['blk_today']}</b>\n"
                       f"👥 Jami: <b>{n['total']}</b> · tirik: <b>{n['alive']}</b>")
                self.notify(b["owner_id"], txt, kb([btn("📊 To'liq statistika", f"st:{b['id']}")]))

    # ------------------------------------------------------------------ Mini App paneli
    def check_init_data(self, init_data):
        """Telegram Mini App initData imzosini tekshiradi, foydalanuvchi ID'sini qaytaradi."""
        try:
            pairs = dict(parse_qsl(init_data, keep_blank_values=True))
            h = pairs.pop("hash")
            check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
            secret = hmac.new(b"WebAppData", self.control_token.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), h):
                return None
            if time.time() - int(pairs.get("auth_date", 0)) > 86400:
                return None
            return json.loads(pairs["user"])["id"]
        except Exception:
            return None

    async def panel_page(self, request):
        return web.FileResponse(HERE / "panel.html", headers={"Cache-Control": "no-cache"})

    async def api_panel(self, request):
        uid = self.check_init_data(request.headers.get("X-Init-Data", ""))
        if not uid or not self.ok_user(uid):
            return web.json_response({"error": "auth"}, status=401)
        bots = self.db.bots_of(uid)
        if not bots:
            return web.json_response({"bots": []})
        want = request.query.get("bot")
        b = next((x for x in bots if str(x["id"]) == want), bots[0])
        data = await asyncio.to_thread(reports.panel_json, self.db, b)
        data["bots"] = [{"id": x["id"], "username": x["username"]} for x in bots]
        return web.json_response(data)

    async def health(self, request):
        return web.json_response({"ok": True, "bots": len(self.db.active_bots())})

    # ------------------------------------------------------------------ ishga tushirish
    def web_app(self):
        a = web.Application(client_max_size=4 * 1024 * 1024)
        a.router.add_post("/hook/{bot_id}", self.hook)
        a.router.add_get("/panel", self.panel_page)
        a.router.add_get("/api/panel", self.api_panel)
        a.router.add_get("/", self.health)
        return a

    async def start(self, run_polling=True, background=True):
        self.session = aiohttp.ClientSession()
        self.cb = Bot(self.control_token, self.session, self.api)
        self.tasks.append(asyncio.create_task(self.notifier()))
        if background:
            self.tasks += [asyncio.create_task(self.watchdog()), asyncio.create_task(self.daily_report())]
        if run_polling:
            self.tasks.append(asyncio.create_task(self.poll()))

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.session.close()


async def amain():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = App()
    if not app.public_url.startswith("https://"):
        log.warning("PUBLIC_URL https:// bilan boshlanmagan — to'liq kuzatuv va Panel ishlamaydi, faqat tekshiruv rejimi.")
    await app.start()
    runner = web.AppRunner(app.web_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", app.port).start()
    log.info("Server %s portda ishga tushdi", app.port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await app.stop()


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    asyncio.run(amain())
