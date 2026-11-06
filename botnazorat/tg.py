"""Telegram Bot API uchun yengil mijoz (aiohttp)."""
import json
import aiohttp


class TgError(Exception):
    def __init__(self, code, desc):
        super().__init__(f"{code}: {desc}")
        self.code = code
        self.desc = desc or ""


def _field(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return "true" if v else "false"
    return v if isinstance(v, str) else str(v)


class Bot:
    def __init__(self, token, session, api="https://api.telegram.org"):
        self.token = token
        self.session = session
        self.api = api.rstrip("/")

    async def call(self, method, data=None, files=None, timeout=60):
        url = f"{self.api}/bot{self.token}/{method}"
        data = {k: v for k, v in (data or {}).items() if v is not None}
        if files:
            fd = aiohttp.FormData()
            for k, v in data.items():
                fd.add_field(k, _field(v))
            for k, (fname, content) in files.items():
                fd.add_field(k, content, filename=fname)
            kw = {"data": fd}
        else:
            kw = {"json": data}
        async with self.session.post(url, timeout=aiohttp.ClientTimeout(total=timeout), **kw) as r:
            j = await r.json(content_type=None)
        if not j.get("ok"):
            raise TgError(j.get("error_code"), j.get("description"))
        return j["result"]

    # qulay qisqartmalar
    async def send(self, chat_id, text, kb=None, **kw):
        return await self.call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                               "disable_web_page_preview": True, "reply_markup": kb, **kw})

    async def edit(self, chat_id, msg_id, text, kb=None):
        try:
            return await self.call("editMessageText", {"chat_id": chat_id, "message_id": msg_id, "text": text,
                                                       "parse_mode": "HTML", "disable_web_page_preview": True,
                                                       "reply_markup": kb})
        except TgError as e:
            if "not modified" in e.desc:
                return None
            raise
