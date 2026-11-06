"""SQLite ombori: ulangan botlar, foydalanuvchilar, hodisalar, kunlik faollik."""
import json
import sqlite3
import time
import os

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots(
  id INTEGER PRIMARY KEY,            -- kuzatilayotgan botning Telegram ID'si
  owner_id INTEGER NOT NULL,         -- uni ulagan odam
  username TEXT, name TEXT,
  token_enc TEXT NOT NULL,
  mode TEXT NOT NULL,                -- 'proxy' (to'liq kuzatuv) yoki 'scan' (faqat tekshiruv)
  orig_url TEXT, orig_secret_enc TEXT, orig_allowed TEXT, orig_max_conn INTEGER,
  hook_secret TEXT,
  notify INTEGER NOT NULL DEFAULT 1,
  fwd_fail INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users(
  bot_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  first_name TEXT, last_name TEXT, username TEXT, lang TEXT, is_premium INTEGER,
  first_seen INTEGER, last_seen INTEGER, actions INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',   -- active | blocked | deleted
  blocked_at INTEGER, start_param TEXT, imported INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(bot_id, user_id)
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  type TEXT NOT NULL,                 -- new | start | block | unblock
  ts INTEGER NOT NULL, data TEXT
);
CREATE INDEX IF NOT EXISTS ev_bot_ts ON events(bot_id, type, ts);
CREATE TABLE IF NOT EXISTS daily(
  bot_id INTEGER NOT NULL, day TEXT NOT NULL, user_id INTEGER NOT NULL,
  actions INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(bot_id, day, user_id)
);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""


class DB:
    def __init__(self, path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA synchronous=NORMAL")
        self.c.executescript(SCHEMA)

    # ---------- kv ----------
    def kv_get(self, k, default=None):
        r = self.c.execute("SELECT value FROM kv WHERE key=?", (k,)).fetchone()
        return r[0] if r else default

    def kv_set(self, k, v):
        self.c.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))

    # ---------- bots ----------
    def bot(self, bot_id):
        return self.c.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()

    def bots_of(self, owner_id):
        return self.c.execute("SELECT * FROM bots WHERE owner_id=? AND active=1 ORDER BY created_at", (owner_id,)).fetchall()

    def active_bots(self):
        return self.c.execute("SELECT * FROM bots WHERE active=1").fetchall()

    def save_bot(self, **f):
        f.setdefault("created_at", int(time.time()))
        f.setdefault("active", 1)
        cols = ",".join(f)
        qs = ",".join("?" * len(f))
        upd = ",".join(f"{k}=excluded.{k}" for k in f if k not in ("id", "created_at"))
        self.c.execute(f"INSERT INTO bots({cols}) VALUES({qs}) ON CONFLICT(id) DO UPDATE SET {upd}", tuple(f.values()))

    def update_bot(self, bot_id, **f):
        sets = ",".join(f"{k}=?" for k in f)
        self.c.execute(f"UPDATE bots SET {sets} WHERE id=?", (*f.values(), bot_id))

    # ---------- users / events ----------
    def get_user(self, bot_id, uid):
        return self.c.execute("SELECT * FROM users WHERE bot_id=? AND user_id=?", (bot_id, uid)).fetchone()

    def add_event(self, bot_id, uid, typ, ts, data=None):
        self.c.execute("INSERT INTO events(bot_id,user_id,type,ts,data) VALUES(?,?,?,?,?)",
                       (bot_id, uid, typ, ts, json.dumps(data, ensure_ascii=False) if data is not None else None))

    def upsert_user(self, bot_id, u, ts, count_action=True):
        """Foydalanuvchini yozadi. Yangi bo'lsa True qaytaradi."""
        old = self.get_user(bot_id, u["id"])
        fields = (u.get("first_name"), u.get("last_name"), u.get("username"), u.get("language_code"),
                  1 if u.get("is_premium") else 0)
        if old is None:
            self.c.execute("""INSERT INTO users(bot_id,user_id,first_name,last_name,username,lang,is_premium,
                              first_seen,last_seen,actions,status) VALUES(?,?,?,?,?,?,?,?,?,?,'active')""",
                           (bot_id, u["id"], *fields, ts, ts, 1 if count_action else 0))
            return True
        self.c.execute("""UPDATE users SET first_name=?,last_name=?,username=?,lang=COALESCE(?,lang),is_premium=?,
                          last_seen=?,actions=actions+?, first_seen=COALESCE(first_seen,?) WHERE bot_id=? AND user_id=?""",
                       (*fields, ts, 1 if count_action else 0, ts, bot_id, u["id"]))
        return False

    def touch_daily(self, bot_id, day, uid):
        self.c.execute("""INSERT INTO daily(bot_id,day,user_id,actions) VALUES(?,?,?,1)
                          ON CONFLICT(bot_id,day,user_id) DO UPDATE SET actions=actions+1""", (bot_id, day, uid))

    def set_status(self, bot_id, uid, status, ts=None):
        if status == "blocked":
            self.c.execute("UPDATE users SET status='blocked', blocked_at=? WHERE bot_id=? AND user_id=?", (ts, bot_id, uid))
        else:
            self.c.execute("UPDATE users SET status=?, blocked_at=NULL WHERE bot_id=? AND user_id=?", (status, bot_id, uid))

    def import_ids(self, bot_id, ids):
        n = 0
        for uid in ids:
            cur = self.c.execute("INSERT OR IGNORE INTO users(bot_id,user_id,imported,status) VALUES(?,?,1,'active')", (bot_id, uid))
            n += cur.rowcount
        return n

    def q(self, sql, *a):
        return self.c.execute(sql, a).fetchall()

    def q1(self, sql, *a):
        r = self.c.execute(sql, a).fetchone()
        return r[0] if r else None
