"""Statistika matnlari, grafik (PNG), Excel va Mini App uchun JSON."""
import io
import json
from datetime import datetime, timedelta

from .util import TZ, day_start_ts, esc, fmt_ts, now_local, user_label


def numbers(db, bot_id):
    t0, t1, t7, t30 = day_start_ts(0), day_start_ts(1), day_start_ts(6), day_start_ts(29)
    today = now_local().strftime("%Y-%m-%d")
    d7 = (now_local() - timedelta(days=6)).strftime("%Y-%m-%d")
    d30 = (now_local() - timedelta(days=29)).strftime("%Y-%m-%d")
    q1 = db.q1
    total = q1("SELECT COUNT(*) FROM users WHERE bot_id=?", bot_id)
    blocked = q1("SELECT COUNT(*) FROM users WHERE bot_id=? AND status='blocked'", bot_id)
    deleted = q1("SELECT COUNT(*) FROM users WHERE bot_id=? AND status='deleted'", bot_id)
    ev = lambda typ, since, until=None: q1(
        "SELECT COUNT(*) FROM events WHERE bot_id=? AND type=? AND ts>=? AND ts<?", bot_id, typ, since, until or 2**40)
    act = lambda since_day: q1("SELECT COUNT(DISTINCT user_id) FROM daily WHERE bot_id=? AND day>=?", bot_id, since_day)
    return {
        "total": total, "blocked": blocked, "deleted": deleted, "alive": total - blocked - deleted,
        "new_today": ev("new", t0), "new_yday": ev("new", t1, t0), "new_7": ev("new", t7), "new_30": ev("new", t30),
        "blk_today": ev("block", t0), "blk_7": ev("block", t7), "blk_30": ev("block", t30),
        "unblk_7": ev("unblock", t7),
        "starts_today": ev("start", t0),
        "dau": act(today), "wau": act(d7), "mau": act(d30),
        "block_rate": round(100 * blocked / total, 1) if total else 0.0,
    }


def _arrow(a, b):
    if a > b:
        return f"📈 +{a - b}"
    if a < b:
        return f"📉 −{b - a}"
    return "➖ 0"


def stats_text(db, bot):
    n = numbers(db, bot["id"])
    return (
        f"📊 <b>@{esc(bot['username'])} statistikasi</b>\n"
        f"<i>{now_local().strftime('%d.%m.%Y %H:%M')} holatiga</i>\n\n"
        f"👥 Jami foydalanuvchi: <b>{n['total']}</b>\n"
        f"✅ Tirik (bloklamagan): <b>{n['alive']}</b>\n"
        f"⛔ Bloklagan: <b>{n['blocked']}</b> ({n['block_rate']}%)\n\n"
        f"<b>🆕 Yangi kirganlar</b>\n"
        f"• Bugun: <b>{n['new_today']}</b>  (kecha {n['new_yday']}, {_arrow(n['new_today'], n['new_yday'])})\n"
        f"• 7 kun: <b>{n['new_7']}</b>   • 30 kun: <b>{n['new_30']}</b>\n\n"
        f"<b>🔥 Faollik</b>\n"
        f"• Bugun ishlatgan: <b>{n['dau']}</b>\n"
        f"• 7 kunda: <b>{n['wau']}</b>   • 30 kunda: <b>{n['mau']}</b>\n"
        f"• Bugun /start bosishlar: <b>{n['starts_today']}</b>\n\n"
        f"<b>🚪 Chiqib ketganlar (bloklash)</b>\n"
        f"• Bugun: <b>{n['blk_today']}</b>   • 7 kun: <b>{n['blk_7']}</b>   • 30 kun: <b>{n['blk_30']}</b>\n"
        f"• 7 kunda qaytganlar: <b>{n['unblk_7']}</b>"
    )


def list_new(db, bot, limit=20):
    rows = db.q("""SELECT u.*, e.ts AS ets, e.data AS edata FROM events e JOIN users u
                   ON u.bot_id=e.bot_id AND u.user_id=e.user_id
                   WHERE e.bot_id=? AND e.type='new' ORDER BY e.ts DESC LIMIT ?""", bot["id"], limit)
    if not rows:
        return "🆕 Hali yangi foydalanuvchi yo'q."
    lines = [f"🆕 <b>Oxirgi {len(rows)} ta yangi foydalanuvchi</b>\n"]
    for i, r in enumerate(rows, 1):
        p = json.loads(r["edata"]).get("param") if r["edata"] else None
        st = " ⛔" if r["status"] == "blocked" else ""
        lines.append(f"{i}. {user_label(r)}{st}\n    🕒 {fmt_ts(r['ets'])}" + (f"  📣 <code>{esc(p)}</code>" if p else ""))
    return "\n".join(lines)


def list_blocked(db, bot, limit=20):
    rows = db.q("""SELECT * FROM users WHERE bot_id=? AND status='blocked'
                   ORDER BY blocked_at DESC LIMIT ?""", bot["id"], limit)
    if not rows:
        return "⛔ Hozircha hech kim bloklamagan. 🎉"
    total = db.q1("SELECT COUNT(*) FROM users WHERE bot_id=? AND status='blocked'", bot["id"])
    lines = [f"⛔ <b>Bloklaganlar: {total} ta</b> (oxirgi {len(rows)} tasi)\n"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {user_label(r)}\n    🚪 {fmt_ts(r['blocked_at'])}  ·  kirgan: {fmt_ts(r['first_seen'])}  ·  {r['actions']} ta harakat")
    return "\n".join(lines)


def list_active(db, bot, limit=20):
    since = (now_local() - timedelta(days=6)).strftime("%Y-%m-%d")
    rows = db.q("""SELECT u.*, COUNT(d.day) AS days, SUM(d.actions) AS acts FROM daily d JOIN users u
                   ON u.bot_id=d.bot_id AND u.user_id=d.user_id
                   WHERE d.bot_id=? AND d.day>=? GROUP BY d.user_id ORDER BY days DESC, acts DESC LIMIT ?""",
                bot["id"], since, limit)
    if not rows:
        return "🔥 Oxirgi 7 kunda faol foydalanuvchi yo'q."
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🔥 <b>Eng faollar (7 kun)</b>\n"]
    for i, r in enumerate(rows):
        m = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{m} {user_label(r)}\n    📅 {r['days']}/7 kun  ·  ⚡ {r['acts']} ta harakat  ·  oxirgi: {fmt_ts(r['last_seen'])}")
    return "\n".join(lines)


def list_sources(db, bot):
    since = day_start_ts(29)
    rows = db.q("""SELECT COALESCE(json_extract(data,'$.param'),'') AS p, COUNT(*) AS n FROM events
                   WHERE bot_id=? AND type='new' AND ts>=? GROUP BY p ORDER BY n DESC LIMIT 15""", bot["id"], since)
    if not rows:
        return "📣 30 kunda yangi foydalanuvchi bo'lmadi."
    total = sum(r["n"] for r in rows)
    lines = ["📣 <b>Qayerdan kelishdi (30 kun)</b>",
             f"<i>Havola: t.me/{esc(bot['username'])}?start=<b>reklama1</b> ko'rinishida bersangiz, shu yerda ko'rinadi</i>\n"]
    for r in rows:
        name = r["p"] or "to'g'ridan-to'g'ri"
        pct = round(100 * r["n"] / total)
        bar = "▰" * max(1, pct // 10) + "▱" * (10 - max(1, pct // 10))
        lines.append(f"<code>{esc(name)[:24]}</code>\n{bar} <b>{r['n']}</b> ({pct}%)")
    return "\n".join(lines)


def series(db, bot_id, days=30):
    out = []
    for i in range(days - 1, -1, -1):
        d = now_local() - timedelta(days=i)
        day = d.strftime("%Y-%m-%d")
        a, b = day_start_ts(i), day_start_ts(i - 1) if i else 2**40
        new = db.q1("SELECT COUNT(*) FROM events WHERE bot_id=? AND type='new' AND ts>=? AND ts<?", bot_id, a, b)
        blk = db.q1("SELECT COUNT(*) FROM events WHERE bot_id=? AND type='block' AND ts>=? AND ts<?", bot_id, a, b)
        act = db.q1("SELECT COUNT(*) FROM daily WHERE bot_id=? AND day=?", bot_id, day)
        out.append({"day": day, "label": d.strftime("%d.%m"), "new": new, "block": blk, "active": act})
    return out


def chart_png(db, bot, days=30):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    s = series(db, bot["id"], days)
    x = list(range(len(s)))
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=130)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#FFFFFF")
    ax.bar([i - 0.2 for i in x], [p["new"] for p in s], width=0.4, color="#7C3AED", label="Yangi kirganlar", zorder=3)
    ax.bar([i + 0.2 for i in x], [p["block"] for p in s], width=0.4, color="#FF4D6D", label="Bloklaganlar", zorder=3)
    ax.plot(x, [p["active"] for p in s], color="#16C784", lw=3, marker="o", ms=4, label="Faol foydalanuvchilar", zorder=4)
    ax.set_xticks(x[::3])
    ax.set_xticklabels([p["label"] for p in s][::3], color="#6B6290", fontsize=9)
    ax.tick_params(axis="y", colors="#6B6290", labelsize=9)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.grid(axis="y", color="#EEE9F7", zorder=0)
    ax.set_title(f"@{bot['username']} · oxirgi {days} kun", loc="left", fontsize=15, fontweight="bold", color="#1B1240", pad=34)
    ax.legend(loc="lower left", frameon=False, fontsize=10, ncol=3, bbox_to_anchor=(0, 1.0))
    ax.set_ylim(bottom=0, top=max(3, max(max(p["new"], p["block"], p["active"]) for p in s) * 1.15))
    from matplotlib.ticker import MaxNLocator
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def excel(db, bot):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Foydalanuvchilar"
    head = ["ID", "Ism", "Familiya", "Username", "Til", "Premium", "Holat", "Kirgan vaqti", "Oxirgi faollik",
            "Bloklagan vaqti", "Harakatlar", "Manba (start)"]
    ws.append(head)
    st = {"active": "Faol", "blocked": "Bloklagan", "deleted": "O'chirilgan akkaunt"}
    dt = lambda ts: datetime.fromtimestamp(ts, TZ).replace(tzinfo=None) if ts else None
    for r in db.q("SELECT * FROM users WHERE bot_id=? ORDER BY first_seen DESC", bot["id"]):
        ws.append([r["user_id"], r["first_name"], r["last_name"], ("@" + r["username"]) if r["username"] else None,
                   r["lang"], "Ha" if r["is_premium"] else "", st.get(r["status"], r["status"]), dt(r["first_seen"]),
                   dt(r["last_seen"]), dt(r["blocked_at"]), r["actions"], r["start_param"]])
    ws2 = wb.create_sheet("Kunlik")
    ws2.append(["Sana", "Yangi kirganlar", "Bloklaganlar", "Faol foydalanuvchilar"])
    for p in series(db, bot["id"], 90):
        ws2.append([p["day"], p["new"], p["block"], p["active"]])
    for w in (ws, ws2):
        for c in w[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="7C3AED")
            c.alignment = Alignment(horizontal="center")
        w.freeze_panes = "A2"
        for col in w.columns:
            width = max(len(str(c.value or "")) for c in col[:200])
            w.column_dimensions[col[0].column_letter].width = min(40, max(10, width + 2))
        for row in w.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, datetime):
                    c.number_format = "dd.mm.yyyy hh:mm"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def panel_json(db, bot):
    """Mini App paneli uchun hamma ma'lumot."""
    def users(sql, *a):
        return [{"id": r["user_id"], "name": " ".join(x for x in (r["first_name"], r["last_name"]) if x) or "—",
                 "username": r["username"], "status": r["status"], "first_seen": r["first_seen"],
                 "last_seen": r["last_seen"], "blocked_at": r["blocked_at"], "actions": r["actions"],
                 "param": r["start_param"]} for r in db.q(sql, *a)]
    since = (now_local() - timedelta(days=6)).strftime("%Y-%m-%d")
    return {
        "bot": {"id": bot["id"], "username": bot["username"], "name": bot["name"], "mode": bot["mode"]},
        "n": numbers(db, bot["id"]),
        "series": series(db, bot["id"], 30),
        "new": users("SELECT * FROM users WHERE bot_id=? AND imported=0 ORDER BY first_seen DESC LIMIT 50", bot["id"]),
        "blocked": users("SELECT * FROM users WHERE bot_id=? AND status='blocked' ORDER BY blocked_at DESC LIMIT 50", bot["id"]),
        "active": users("""SELECT u.* FROM users u JOIN (SELECT user_id, COUNT(*) dd, SUM(actions) aa FROM daily
                           WHERE bot_id=? AND day>=? GROUP BY user_id) d ON d.user_id=u.user_id
                           WHERE u.bot_id=? ORDER BY d.dd DESC, d.aa DESC LIMIT 50""", bot["id"], since, bot["id"]),
        "sources": [{"param": r["p"] or None, "n": r["n"]} for r in db.q(
            """SELECT COALESCE(json_extract(data,'$.param'),'') AS p, COUNT(*) AS n FROM events
               WHERE bot_id=? AND type='new' AND ts>=? GROUP BY p ORDER BY n DESC LIMIT 10""", bot["id"], day_start_ts(29))],
    }
