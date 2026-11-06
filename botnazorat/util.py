import base64
import hashlib
import html
import os
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

TZ = timezone(timedelta(hours=float(os.environ.get("TZ_OFFSET", "5"))))


def fernet():
    key = os.environ.get("SECRET_KEY", "")
    if len(key) < 16:
        raise RuntimeError("SECRET_KEY kamida 16 belgidan iborat bo'lishi kerak (.env faylini tekshiring)")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def enc(s):
    return fernet().encrypt(s.encode()).decode() if s else None


def dec(s):
    return fernet().decrypt(s.encode()).decode() if s else None


def esc(s):
    return html.escape(str(s or ""), quote=False)


def now_local():
    return datetime.now(TZ)


def day_of(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d")


def fmt_ts(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m %H:%M")


def day_start_ts(days_ago=0):
    d = now_local().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return int(d.timestamp())


def user_label(r):
    name = " ".join(x for x in (r["first_name"], r["last_name"]) if x) or "—"
    uname = f" @{r['username']}" if r["username"] else ""
    return f'<a href="tg://user?id={r["user_id"]}">{esc(name)}</a>{esc(uname)}'
