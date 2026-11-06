"""Kuzatilayotgan botga kelgan update'ni tahlil qilib, bazaga yozadi."""
import time

from .util import day_of

# Har bir update turida foydalanuvchi qaysi maydonda turishi
USER_PATHS = {
    "message": ("from",), "edited_message": ("from",),
    "callback_query": ("from",), "inline_query": ("from",), "chosen_inline_result": ("from",),
    "shipping_query": ("from",), "pre_checkout_query": ("from",), "poll_answer": ("user",),
    "message_reaction": ("user",), "business_message": ("from",),
}


def process(db, bot_id, upd, ts=None):
    """Update'ni yozadi. Egaga yuboriladigan bildirishnomalar ro'yxatini qaytaradi:
    [("new"|"block"|"unblock", user_dict, extra)]"""
    ts = ts or int(time.time())
    out = []

    mcm = upd.get("my_chat_member")
    if mcm:
        if mcm.get("chat", {}).get("type") != "private":
            return out
        u = mcm["from"]
        new = mcm.get("new_chat_member", {}).get("status")
        old = mcm.get("old_chat_member", {}).get("status")
        is_new = db.upsert_user(bot_id, u, ts, count_action=False)
        if is_new:
            db.add_event(bot_id, u["id"], "new", ts)
        if new == "kicked":
            db.set_status(bot_id, u["id"], "blocked", ts)
            db.add_event(bot_id, u["id"], "block", ts)
            out.append(("block", u, None))
        elif new == "member" and old == "kicked":
            db.set_status(bot_id, u["id"], "active")
            db.add_event(bot_id, u["id"], "unblock", ts)
            out.append(("unblock", u, None))
        return out

    for key, path in USER_PATHS.items():
        obj = upd.get(key)
        if not obj:
            continue
        u = obj
        for p in path:
            u = (u or {}).get(p)
        if not u or u.get("is_bot"):
            return out
        is_new = db.upsert_user(bot_id, u, ts)
        db.touch_daily(bot_id, day_of(ts), u["id"])
        param = None
        if key == "message" and obj.get("chat", {}).get("type") == "private":
            text = obj.get("text") or ""
            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                param = parts[1][:64] if len(parts) > 1 else None
                db.add_event(bot_id, u["id"], "start", ts, {"param": param})
        if is_new:
            db.add_event(bot_id, u["id"], "new", ts, {"param": param})
            if param:
                db.c.execute("UPDATE users SET start_param=? WHERE bot_id=? AND user_id=?", (param, bot_id, u["id"]))
            out.append(("new", u, param))
        else:
            row = db.get_user(bot_id, u["id"])
            # bloklagan odam yozsa, demak qaytgan (my_chat_member kelmay qolgan holatlar uchun)
            if row and row["status"] == "blocked":
                db.set_status(bot_id, u["id"], "active")
                db.add_event(bot_id, u["id"], "unblock", ts)
                out.append(("unblock", u, None))
        return out
    return out


def forward_allowed(upd, allowed):
    """Asl bot faqat o'zi so'ragan turdagi update'larni olsin."""
    kinds = [k for k in upd if k != "update_id"]
    if not kinds:
        return False
    if not allowed:  # Telegram standarti: chat_member, message_reaction* dan tashqari hammasi
        return kinds[0] not in ("chat_member", "message_reaction", "message_reaction_count")
    return kinds[0] in allowed
