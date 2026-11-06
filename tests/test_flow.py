"""To'liq sinov: soxta Telegram API + soxta "asl bot serveri" bilan butun oqim.
Ishga tushirish:  python -m tests.test_flow
"""
import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
from urllib.parse import urlencode

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

CONTROL = "111111:" + "A" * 35
TARGET = "222222:" + "B" * 35
POLLBOT = "333333:" + "C" * 35
OWNER = 900


class FakeTG:
    def __init__(self):
        self.calls = []
        self.bots = {
            CONTROL: {"id": 111111, "username": "nazorat_bot", "first_name": "Nazorat", "wh": {}},
            TARGET: {"id": 222222, "username": "speakupcefrbot", "first_name": "SpeakUp", "wh": {}},
            POLLBOT: {"id": 333333, "username": "pollbot", "first_name": "Poll", "wh": {}},
        }
        self.blocked = {1003}
        self.deactivated = {1004}
        self.files = {"f1": b"1001\n1003\n1004, 1005\n"}

    def calls_of(self, method, token=None):
        return [d for t, m, d in self.calls if m == method and (token is None or t == token)]

    async def handle(self, req):
        token, method = req.match_info["token"], req.match_info["method"]
        if req.content_type.startswith("multipart"):
            data = {}
            async for part in await req.multipart():
                data[part.name] = bytes(await part.read()) if part.filename else (await part.text())
        else:
            data = await req.json() if req.can_read_body else {}
        self.calls.append((token, method, data))
        bot = self.bots.get(token)
        if not bot:
            return web.json_response({"ok": False, "error_code": 401, "description": "Unauthorized"})
        ok = lambda r: web.json_response({"ok": True, "result": r})
        if method == "getMe":
            return ok({"id": bot["id"], "is_bot": True, "username": bot["username"], "first_name": bot["first_name"]})
        if method == "getWebhookInfo":
            w = bot["wh"]
            return ok({"url": w.get("url", ""), "pending_update_count": 0,
                       **({"allowed_updates": w["allowed_updates"]} if w.get("allowed_updates") else {}),
                       **({"max_connections": w["max_connections"]} if w.get("max_connections") else {})})
        if method == "setWebhook":
            bot["wh"] = {"url": data.get("url", ""), "secret": data.get("secret_token"),
                         "allowed_updates": data.get("allowed_updates"), "max_connections": data.get("max_connections")}
            return ok(True)
        if method == "deleteWebhook":
            bot["wh"] = {}
            return ok(True)
        if method == "sendChatAction":
            cid = int(data["chat_id"])
            if cid in self.blocked:
                return web.json_response({"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"})
            if cid in self.deactivated:
                return web.json_response({"ok": False, "error_code": 403, "description": "Forbidden: user is deactivated"})
            if cid == 1005:
                return web.json_response({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
            return ok(True)
        if method in ("sendMessage", "sendPhoto", "sendDocument", "editMessageText"):
            return ok({"message_id": len(self.calls), "chat": {"id": data.get("chat_id")}})
        if method == "getFile":
            return ok({"file_id": data["file_id"], "file_path": "docs/" + data["file_id"]})
        return ok(True)

    async def file(self, req):
        return web.Response(body=self.files[req.match_info["path"].split("/")[-1]])


class Orig:
    """Botingizning asl serveri (webhook qabul qiluvchi)."""
    def __init__(self):
        self.got = []
        self.status = 200

    async def handle(self, req):
        self.got.append((dict(req.headers), await req.json()))
        if self.status != 200:
            return web.Response(status=self.status)
        return web.json_response({"method": "sendMessage", "chat_id": 1, "text": "salom"})


def msg(uid, text, name="Ali", username=None, chat_type="private", mid=1):
    return {"update_id": int(time.time() * 1000) % 10**9, "message": {
        "message_id": mid, "from": {"id": uid, "is_bot": False, "first_name": name, "username": username, "language_code": "uz"},
        "chat": {"id": uid, "type": chat_type}, "date": int(time.time()), "text": text}}


def cbq(uid, data, mid=50):
    return {"update_id": 1, "callback_query": {"id": "q1", "from": {"id": uid, "first_name": "Owner"}, "data": data,
                                               "message": {"message_id": mid, "chat": {"id": uid, "type": "private"}}}}


def init_data(uid, token=CONTROL):
    p = {"auth_date": str(int(time.time())), "query_id": "AA1", "user": json.dumps({"id": uid, "first_name": "Owner"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(p.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    p["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(p)


async def run():
    fake, orig = FakeTG(), Orig()
    tg_app = web.Application()
    tg_app.router.add_post("/bot{token}/{method}", fake.handle)
    tg_app.router.add_get("/file/bot{token}/{path:.*}", fake.file)
    tg_srv = TestServer(tg_app)
    await tg_srv.start_server()
    o_app = web.Application()
    o_app.router.add_post("/orig", orig.handle)
    o_srv = TestServer(o_app)
    await o_srv.start_server()
    orig_url = str(o_srv.make_url("/orig"))
    fake.bots[TARGET]["wh"] = {"url": orig_url, "allowed_updates": None, "max_connections": 40}

    os.environ["SECRET_KEY"] = "test-secret-key-1234567890"
    from botnazorat.app import App
    tmp = tempfile.mkdtemp()
    app = App({"CONTROL_TOKEN": CONTROL, "PUBLIC_URL": "https://nazorat.example.com", "TG_API": str(tg_srv.make_url("")),
               "DB_PATH": f"{tmp}/t.db", "ALLOWED_USERS": ""})
    await app.start(run_polling=False, background=False)
    client = TestClient(TestServer(app.web_app()))
    await client.start_server()
    ok = 0

    def check(cond, label):
        nonlocal ok
        assert cond, label
        ok += 1
        print("  ✅", label)

    async def settle():
        await asyncio.sleep(0.15)
        while not app.nq.empty():
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.4)

    print("1) Menyu va ulash")
    await app.handle(msg(OWNER, "/start", "Javohir"))
    check("Bot Nazorat" in fake.calls_of("sendMessage")[-1]["text"], "/start menyu chiqdi")
    await app.handle(cbq(OWNER, "add"))
    await app.handle(msg(OWNER, TARGET, "Javohir", mid=77))
    check(any(d["message_id"] == 77 for d in fake.calls_of("deleteMessage")), "token xabari o'chirildi")
    check("maxfiy kalit" in fake.calls_of("editMessageText")[-1]["text"], "webhook topildi, kalit so'raldi")
    await app.handle(cbq(OWNER, "secn"))
    wh = fake.bots[TARGET]["wh"]
    check(wh["url"] == "https://nazorat.example.com/hook/222222" and wh["secret"], "webhook proxy'ga o'rnatildi")
    b = app.db.bot(222222)
    check(b["mode"] == "proxy" and b["orig_url"] == orig_url, "asl manzil saqlandi")
    check(TARGET not in json.dumps(dict(b)), "token bazada shifrlangan")
    hs = wh["secret"]

    print("2) Webhook orqali kuzatuv + asl serverga uzatish")
    r = await client.post("/hook/222222", json=msg(1001, "/start insta1", "Aziz", "aziz_uz"),
                          headers={"X-Telegram-Bot-Api-Secret-Token": hs})
    body = await r.json()
    check(r.status == 200 and body.get("method") == "sendMessage", "asl server javobi Telegram'ga qaytdi")
    check(len(orig.got) == 1 and orig.got[0][1]["message"]["text"] == "/start insta1", "update asl serverga yetdi")
    await client.post("/hook/222222", json=msg(1002, "/start", "Malika"), headers={"X-Telegram-Bot-Api-Secret-Token": hs})
    await client.post("/hook/222222", json=msg(1001, "salom"), headers={"X-Telegram-Bot-Api-Secret-Token": hs})
    await client.post("/hook/222222", json={"update_id": 5, "callback_query": {"id": "x", "from": {"id": 1001, "first_name": "Aziz"}, "data": "go"}},
                      headers={"X-Telegram-Bot-Api-Secret-Token": hs})
    await client.post("/hook/222222", json={"update_id": 6, "my_chat_member": {
        "chat": {"id": 1002, "type": "private"}, "from": {"id": 1002, "first_name": "Malika"}, "date": 1,
        "old_chat_member": {"status": "member", "user": {"id": 222222}}, "new_chat_member": {"status": "kicked", "user": {"id": 222222}}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": hs})
    r = await client.post("/hook/222222", json=msg(1, "x"), headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    check(r.status == 403, "noto'g'ri secret rad etildi")
    u1, u2 = app.db.get_user(222222, 1001), app.db.get_user(222222, 1002)
    check(u1["actions"] == 3 and u1["start_param"] == "insta1", "faollik va manba (insta1) yozildi")
    check(u2["status"] == "blocked", "bloklash aniqlandi")
    await settle()
    texts = [d["text"] for d in fake.calls_of("sendMessage", CONTROL)]
    check(any("Yangi foydalanuvchi" in t and "Aziz" in t and "insta1" in t for t in texts), "egaga 'yangi foydalanuvchi' xabari")
    check(any("Botni blokladi" in t and "Malika" in t for t in texts), "egaga 'bloklandi' xabari")
    await client.post("/hook/222222", json={"update_id": 7, "my_chat_member": {
        "chat": {"id": 1002, "type": "private"}, "from": {"id": 1002, "first_name": "Malika"}, "date": 1,
        "old_chat_member": {"status": "kicked", "user": {"id": 222222}}, "new_chat_member": {"status": "member", "user": {"id": 222222}}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": hs})
    check(app.db.get_user(222222, 1002)["status"] == "active", "qaytgan foydalanuvchi aniqlandi")
    check(len(orig.got) == 6, "barcha 6 update asl serverga uzatildi")

    print("3) Statistika tugmalari")
    for a in ("b", "st", "nw", "bl", "ac", "src"):
        await app.handle(cbq(OWNER, f"{a}:222222"))
    ed = [d["text"] for d in fake.calls_of("editMessageText", CONTROL)][-6:]
    check("Tirik" in ed[0] and "Jami: <b>2</b>" in ed[0], "bot kartochkasi")
    check("Jami foydalanuvchi: <b>2</b>" in ed[1] and "Bugun: <b>2</b>" in ed[1], "statistika matni")
    check("Aziz" in ed[2] and "insta1" in ed[2], "yangi kirganlar ro'yxati")
    check("Faollar" in ed[4] or "faollar" in ed[4].lower(), "faollar ro'yxati")
    check("insta1" in ed[5], "manbalar")
    await app.handle(cbq(OWNER, "ch:222222"))
    ph = fake.calls_of("sendPhoto")[-1]
    check(isinstance(ph["photo"], bytes) and ph["photo"][:4] == b"\x89PNG", "grafik PNG yuborildi")
    open(f"{tmp}/grafik.png", "wb").write(ph["photo"])
    await app.handle(cbq(OWNER, "xl:222222"))
    xl = fake.calls_of("sendDocument")[-1]["document"]
    check(xl[:2] == b"PK", "Excel fayl yuborildi")
    open(f"{tmp}/hisobot.xlsx", "wb").write(xl)

    print("4) ID import + bloklarni tekshirish")
    await app.handle(cbq(OWNER, "imp:222222"))
    m = msg(OWNER, "")
    m["message"]["document"] = {"file_id": "f1", "file_name": "users.txt"}
    await app.handle(m)
    check(app.db.q1("SELECT COUNT(*) FROM users WHERE bot_id=222222") == 5, "ID'lar import qilindi (3 ta yangi)")
    await app.handle(cbq(OWNER, "scan:222222"))
    for _ in range(50):
        await asyncio.sleep(0.1)
        if not app.scans:
            break
    check(app.db.get_user(222222, 1003)["status"] == "blocked", "skan: bloklagan topildi")
    check(app.db.get_user(222222, 1004)["status"] == "deleted", "skan: o'chirilgan akkaunt")
    check("Tekshiruv tugadi" in fake.calls_of("editMessageText")[-1]["text"], "skan natijasi ko'rsatildi")

    print("5) Mini App panel")
    r = await client.get("/api/panel", headers={"X-Init-Data": init_data(OWNER)})
    j = await r.json()
    check(r.status == 200 and j["n"]["total"] == 5 and len(j["series"]) == 30, "panel API ma'lumot berdi")
    json.dump(j, open(f"{tmp}/panel.json", "w"))
    r = await client.get("/api/panel", headers={"X-Init-Data": init_data(OWNER, POLLBOT)})
    check(r.status == 401, "soxta initData rad etildi")
    r = await client.get("/api/panel", headers={"X-Init-Data": init_data(12345)})
    check((await r.json()).get("bots") == [], "begona odam boshqaning botini ko'rmaydi")
    r = await client.get("/panel")
    check(r.status == 200 and "Bot Nazorat" in await r.text(), "panel sahifasi ochiladi")

    print("6) Begona egalik, watchdog, forward xatosi")
    await app.handle(cbq(777, "st:222222"))
    check(fake.calls_of("answerCallbackQuery")[-1].get("text") == "Bot topilmadi", "begona odam boshqaning botiga kira olmaydi")
    fake.bots[TARGET]["wh"] = {"url": orig_url + "?v=2"}   # asl kod restartda webhook'ni qayta qo'ydi
    await app.check_webhooks()
    check(fake.bots[TARGET]["wh"]["url"].endswith("/hook/222222") and app.db.bot(222222)["orig_url"] == orig_url + "?v=2",
          "watchdog kuzatuvni tikladi, yangi asl manzilni oldi")
    orig.status = 401
    for i in range(3):
        await client.post("/hook/222222", json=msg(1001, f"m{i}"), headers={"X-Telegram-Bot-Api-Secret-Token": fake.bots[TARGET]["wh"]["secret"]})
    await settle()
    check(any("maxfiy kalit" in d["text"] for d in fake.calls_of("sendMessage", CONTROL)), "401 bo'lsa egaga ogohlantirish")
    orig.status = 200

    print("7) Uzish va polling bot")
    await app.handle(cbq(OWNER, "dcy:222222"))
    w = fake.bots[TARGET]["wh"]
    check(w["url"] == orig_url + "?v=2" and not w["secret"], "uzganda webhook asl holatiga qaytdi")
    await app.handle(msg(OWNER, POLLBOT, mid=88))
    check(app.db.bot(333333)["mode"] == "scan" and not fake.bots[POLLBOT]["wh"].get("url"), "polling bot: tekshiruv rejimi, webhook'ga tegilmadi")

    await client.close()
    await app.stop()
    await tg_srv.close()
    await o_srv.close()
    print(f"\n🎉 Hammasi o'tdi: {ok} ta tekshiruv. Fayllar: {tmp}")
    return tmp


if __name__ == "__main__":
    asyncio.run(run())
