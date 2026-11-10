#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KMJ TIPS Toast Control Bot
Remote control for the KMJ TIPS Toast/Dialog module, straight from Telegram.
HOST-ONLY DESIGN: there is no database, no GitHub repo and no config file.
The config JSON lives as a PINNED MESSAGE in the primary owner's chat with
this bot; the bot reads it back with getChat and updates it with
editMessageText. The app reads the config straight from THIS host's URL
(GET /api/index?config). Hosting = deploy this one file + 2 env vars. Done.

Environment variables (only these two are needed):
  BOT_TOKEN  - BotFather bot token
  OWNER_ID   - Your numeric Telegram ID (multiple IDs allowed, comma separated;
               the FIRST one is the storage chat)

Pure Python stdlib only. Runs both as a webhook (Vercel api/index.py)
and in polling mode (python3 kmj_toast_bot.py).
"""
import json
import os
import time
import urllib.request
import urllib.error

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# OWNER_ID may hold one or more IDs separated by commas; all of them count as owners
OWNER_IDS = set(x.strip() for x in str(os.environ.get("OWNER_ID", "")).split(",") if x.strip())
_OWNER_LIST = [x.strip() for x in str(os.environ.get("OWNER_ID", "")).split(",") if x.strip()]
PRIMARY_OWNER = _OWNER_LIST[0] if _OWNER_LIST else ""

DEFAULT_CONFIG = {
    "enabled": True,
    "show_dialog": True,
    "force_update": False,
    "force_update_below": 0,
    "expire_date": "",
    "expire_message": "This version has expired. Please update to continue.",
    "title": "KMJ TIPS",
    "app_name": "KMJ TIPS",
    "developer": "KMJ TIPS",
    "description": "",
    "telegram_link": "https://t.me/kmjTrick",
    "update_link": "https://t.me/kmjTrick",
    "announcement": "",
    "announcement_id": 0,
    "show_text": True,
}

TG = "https://api.telegram.org/bot" + BOT_TOKEN


# ---------------- Telegram helpers ----------------

def tg_call(method, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(TG + "/" + method, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("TG error", method, e)
        return None


def send(chat_id, text, kb=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
         "disable_web_page_preview": True}
    if kb:
        p["reply_markup"] = kb
    return tg_call("sendMessage", p)


def edit_msg(chat_id, msg_id, text, kb=None):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text,
         "parse_mode": "HTML", "disable_web_page_preview": True}
    if kb:
        p["reply_markup"] = kb
    return tg_call("editMessageText", p)


def answer_cb(cb_id, text=""):
    return tg_call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})


def panel_kb():
    return {"inline_keyboard": [
        [{"text": "🟢 Normal Mode", "callback_data": "m_normal"},
         {"text": "🔴 Force Update", "callback_data": "m_force"}],
        [{"text": "⚫ Dialog OFF", "callback_data": "m_off"},
         {"text": "🟢 Dialog ON", "callback_data": "m_on"}],
        [{"text": "📊 Status", "callback_data": "m_status"},
         {"text": "📄 Full Config", "callback_data": "m_config"}],
        [{"text": "✏️ Text Edit", "callback_data": "m_textedit"},
         {"text": "🗑 Text Delete", "callback_data": "m_textdel"}],
        [{"text": "👁 Text Show ON/OFF", "callback_data": "m_textshow"}],
        [{"text": "📢 Announcement OFF", "callback_data": "m_annoff"},
         {"text": "📢 How to Announce?", "callback_data": "m_anninfo"}],
    ]}


# ---------------- Config storage: Telegram pinned message ----------------
# The config JSON is stored as a PINNED MESSAGE in the primary owner's chat
# with this bot. No database, no GitHub, no config file anywhere.

STORE_HEADER = ("\u2699\ufe0f KMJ TIPS CONFIG STORAGE\n"
                "Do not delete or unpin this message - the bot saves the "
                "app config inside it.\n")
_store = {"msg_id": None}


def _parse_store_text(text):
    i = (text or "").find("{")
    if i < 0:
        return None
    try:
        cfg = json.loads(text[i:])
    except Exception:
        return None
    return cfg if isinstance(cfg, dict) else None


def _store_create(cfg):
    """Sends a fresh storage message to the primary owner and pins it."""
    if not PRIMARY_OWNER:
        return None
    text = STORE_HEADER + json.dumps(cfg, ensure_ascii=False, indent=1)
    r = tg_call("sendMessage", {"chat_id": int(PRIMARY_OWNER), "text": text})
    mid = ((r or {}).get("result") or {}).get("message_id")
    if mid:
        tg_call("pinChatMessage", {"chat_id": int(PRIMARY_OWNER),
                                   "message_id": mid,
                                   "disable_notification": True})
        _store["msg_id"] = mid
    return mid


def store_read():
    """Returns (config_dict, None) or (None, error_string).
    Creates the storage message with defaults on first use."""
    if not PRIMARY_OWNER:
        return None, "OWNER_ID is not set"
    r = tg_call("getChat", {"chat_id": int(PRIMARY_OWNER)})
    pin = ((r or {}).get("result") or {}).get("pinned_message") or {}
    cfg = _parse_store_text(pin.get("text", ""))
    if cfg is not None:
        _store["msg_id"] = pin.get("message_id")
        full = dict(DEFAULT_CONFIG)
        full.update(cfg)
        return full, None
    cfg = dict(DEFAULT_CONFIG)
    _store_create(cfg)
    return cfg, None


def store_write(cfg):
    """Saves the config by editing the pinned storage message."""
    if not PRIMARY_OWNER:
        return False
    text = STORE_HEADER + json.dumps(cfg, ensure_ascii=False, indent=1)
    mid = _store.get("msg_id")
    if mid:
        r = tg_call("editMessageText", {"chat_id": int(PRIMARY_OWNER),
                                        "message_id": mid, "text": text})
        if r and r.get("ok"):
            return True
    return _store_create(cfg) is not None


def apply_and_save(transform, message):
    """Read config -> transform(cfg) -> save. Rapid button taps can collide,
    so retry up to 3 times with a fresh read each time, and always report
    the REAL final state (re-read after the write)."""
    for _attempt in range(3):
        cfg, err = store_read()
        if cfg is None:
            return False, "\u274c Could not read the config: %s" % err
        transform(cfg)
        if store_write(cfg):
            final, _e = store_read()
            return True, mode_summary(final if final is not None else cfg)
    return False, "\u274c Could not save. Please try again shortly."


# ---------------- DIRECT config serve (for the app) ----------------

_cfg_cache = {"t": 0, "cfg": None}


def serve_config():
    """Config served to the app: a short 10-second in-memory cache, otherwise
    a fresh read from the pinned storage message."""
    now = time.time()
    if _cfg_cache["cfg"] is not None and now - _cfg_cache["t"] < 10:
        return _cfg_cache["cfg"]
    cfg, _err = store_read()
    if cfg is not None:
        _cfg_cache["t"] = now
        _cfg_cache["cfg"] = cfg
        return cfg
    return _cfg_cache["cfg"]


# ---------------- Mode logic (same rules as the dex) ----------------

def compute_mode(cfg):
    if not cfg.get("enabled", True):
        return "⚫ OFF (dialog disabled)"
    exp = str(cfg.get("expire_date", "")).strip()
    if exp:
        return "⏰ EXPIRY SET: %s (Expired screen after this time)" % exp
    if cfg.get("force_update", False) or not cfg.get("show_dialog", True):
        return "🔴 FORCE UPDATE (Update Now only, app locked)"
    fb = cfg.get("force_update_below", 0) or 0
    if fb > 0:
        return "🟠 NORMAL + force update for versions below %s" % fb
    return "🟢 NORMAL (Welcome + Open button)"


def mode_summary(cfg):
    return ("✅ <b>Done!</b>\n\nCurrent mode: <b>%s</b>\n"
            "Effect in apps: within ~20 seconds (the app keeps checking)." % compute_mode(cfg))


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def status_text(cfg):
    return ("📊 <b>KMJ TIPS Toast — Live Status</b>\n\n"
            "Mode: <b>%s</b>\n"
            "enabled: <code>%s</code> | force_update: <code>%s</code> | show_dialog: <code>%s</code>\n"
            "expire_date: <code>%s</code>\n"
            "Title: %s\nTelegram: %s\nUpdate link: %s"
            % (compute_mode(cfg), cfg.get("enabled"), cfg.get("force_update"),
               cfg.get("show_dialog"), cfg.get("expire_date") or "-",
               esc(cfg.get("title", "-")), esc(cfg.get("telegram_link", "-")),
               esc(cfg.get("update_link", "-")),
               ) + "\nDialog text: " + (
                   "<b>ON</b>" if cfg.get("show_text", True) else "<b>OFF</b>") + (
                   " — " + esc(str(cfg.get("description", ""))[:40]) if str(cfg.get("description", "")).strip() else " — (khali)") + "\nAnnouncement: " + (
                   ("<b>ON</b> — " + esc(str(cfg.get("announcement"))[:60]))
                   if str(cfg.get("announcement", "")).strip() else "OFF"))


HELP = ("😈 <b>KMJ TIPS Toast Control</b>\n\n"
        "Control everything from the buttons below or with commands:\n"
        "/normal — 🟢 Normal dialog\n"
        "/force — 🔴 Force update (app locked)\n"
        "/off — ⚫ Dialog fully OFF\n"
        "/on — Dialog back ON\n"
        "/text &lt;message&gt; — Change the description\n"
        "/title &lt;text&gt; — Change the title\n"
        "/expire 2026-12-31 — Set the expiry date (turn off: /expire off)\n"
        "/applink &lt;url&gt; — Link for the Update Now button\n"
        "/tglink &lt;url&gt; — Link for the Telegram button\n"
        "/announce &lt;text&gt; — 📢 Send an announcement (pops up in the app)\n"
        "/announce off — Turn the announcement off\n"
        "/status — Current mode\n"
        "/config — Full JSON\n"
        "/id — Your Telegram ID")


# ---------------- Transforms ----------------

def t_normal(c):
    c["enabled"] = True; c["force_update"] = False; c["show_dialog"] = True
    c["force_update_below"] = 0  # purana below jaal saaf, warna force chipka rehta hai

def t_force(c):
    c["enabled"] = True; c["force_update"] = True; c["show_dialog"] = False
    c["force_update_below"] = 0  # force sirf direct flag se, below ka jaal nahi

def t_off(c):
    c["enabled"] = False

def t_on(c):
    c["enabled"] = True


# ---------------- Update handling ----------------

def is_owner(uid):
    return str(uid) in OWNER_IDS


def do_action(chat_id, action, arg=""):
    """Runs one action and sends the result to the chat."""
    if action == "status":
        cfg, err = store_read()
        send(chat_id, status_text(cfg) if cfg is not None else "❌ " + str(err), panel_kb())
        return False
    if action == "config":
        cfg, err = store_read()
        if cfg is None:
            send(chat_id, "❌ " + str(err))
        else:
            send(chat_id, "<pre>" + json.dumps(cfg, ensure_ascii=False, indent=2)
                 .replace("&", "&amp;").replace("<", "&lt;") + "</pre>")
        return False

    if action == "textedit":
        cfg, err = store_read()
        if cfg is None:
            send(chat_id, "❌ " + str(err))
        else:
            cur = esc(cfg.get("description", "") or "(khali hai)")
            send(chat_id, "✏️ <b>Dialog Text</b>\n\nAbhi ka text:\n<pre>" + cur + "</pre>\n\n"
                          "Naya text add/edit karne ke liye bhejo:\n<code>/text APNA NAYA TEXT</code>\n\n"
                          "Delete karna ho to panel ka 🗑 Text Delete button dabao.")
        return False
    if action == "textdel":
        ok, txt = apply_and_save(lambda c: c.update(description="", show_text=False),
                                 "bot: text delete")
        send(chat_id, "🗑 <b>Dialog text delete ho gaya.</b>\nApp me ab text box nahi dikhega.\n"
                      "Wapas add karna ho to: <code>/text APNA TEXT</code>" if ok else txt, panel_kb() if ok else None)
        return False
    if action == "textshow":
        def _flip(c):
            c["show_text"] = not c.get("show_text", True)
        ok, txt = apply_and_save(_flip, "bot: text show toggle")
        if ok:
            cfg2, _e = store_read()
            state = "ON 👁 (text box dikhega)" if (cfg2 or {}).get("show_text", True) else "OFF (text box chhup jayega)"
            send(chat_id, "👁 <b>Text Show: " + state + "</b>", panel_kb())
        else:
            send(chat_id, txt)
        return False

    transforms = {
        "normal": (t_normal, "bot: normal mode"),
        "force": (t_force, "bot: force update ON"),
        "off": (t_off, "bot: dialog OFF"),
        "on": (t_on, "bot: dialog ON"),
    }
    if action in transforms:
        fn, msg = transforms[action]
        ok, txt = apply_and_save(fn, msg)
        send(chat_id, txt, panel_kb() if ok else None)
        return False
    if action == "text" and arg:
        ok, txt = apply_and_save(lambda c: c.update(description=arg, show_text=True), "bot: description update")
        send(chat_id, txt)
        return False
    if action == "title" and arg:
        ok, txt = apply_and_save(lambda c: c.update(title=arg), "bot: title update")
        send(chat_id, txt)
        return False
    if action == "expire":
        val = "" if arg.lower() in ("off", "band", "none", "") else arg
        ok, txt = apply_and_save(lambda c: c.update(expire_date=val), "bot: expire set")
        send(chat_id, txt)
        return False
    if action == "applink" and arg:
        ok, txt = apply_and_save(lambda c: c.update(update_link=arg), "bot: update link")
        send(chat_id, txt)
        return False
    if action == "announce":
        if not arg:
            send(chat_id, "📢 To send an announcement, type it like this:\n\n"
                          "<code>/announce Your message here</code>\n\n"
                          "To turn it off: /announce off")
            return False
        if arg.lower() in ("off", "band", "clear", "none"):
            def clr(c):
                c["announcement"] = ""; c["announcement_id"] = 0
            ok, txt = apply_and_save(clr, "bot: announcement OFF")
            send(chat_id, ("✅ Announcement turned OFF." if ok else txt))
            return False
        txt_ann = arg
        def setann(c):
            c["announcement"] = txt_ann
            c["announcement_id"] = int(time.time())
        ok, txt = apply_and_save(setann, "bot: announcement set")
        send(chat_id, ("📢 <b>Announcement sent!</b>\nIt will pop up in apps within ~20-30 seconds (once per user).\n\n" + txt) if ok else txt)
        return False
    if action == "annoff":
        def clr2(c):
            c["announcement"] = ""; c["announcement_id"] = 0
        ok, txt = apply_and_save(clr2, "bot: announcement OFF")
        send(chat_id, ("✅ Announcement turned OFF." if ok else txt))
        return False
    if action == "anninfo":
        send(chat_id, "📢 To send an announcement, type it like this:\n\n"
                      "<code>/announce Your message here</code>\n\n"
                      "Example: <code>/announce A new update is coming tomorrow at 6 PM!</code>\n"
                      "To turn it off: /announce off or use the OFF button below.")
        return False
    if action == "tglink" and arg:
        ok, txt = apply_and_save(lambda c: c.update(telegram_link=arg), "bot: telegram link")
        send(chat_id, txt)
        return False
    send(chat_id, "Didn't understand 😅\n\n" + HELP, panel_kb())
    return False


def handle_update(update):
    try:
        if "callback_query" in update:
            cb = update["callback_query"]
            uid = cb.get("from", {}).get("id")
            chat_id = cb.get("message", {}).get("chat", {}).get("id")
            msg_id = cb.get("message", {}).get("message_id")
            data = cb.get("data", "")
            if not is_owner(uid):
                answer_cb(cb["id"], "This bot is for the owner only 😈")
                return
            answer_cb(cb["id"], "Working...")
            amap = {"m_normal": "normal", "m_force": "force", "m_off": "off",
                    "m_on": "on", "m_status": "status", "m_config": "config",
                    "m_annoff": "annoff", "m_anninfo": "anninfo"}
            if data in amap:
                do_action(chat_id, amap[data])
            return

        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat_id = msg.get("chat", {}).get("id")
        uid = msg.get("from", {}).get("id")
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/id"):
            send(chat_id, "Your Telegram ID: <code>%s</code>" % uid)
            return
        if not is_owner(uid):
            send(chat_id, "😈 This bot is for the KMJ TIPS owner only.")
            return
        if text.startswith("/start") or text.startswith("/menu") or text.startswith("/help"):
            send(chat_id, HELP, panel_kb())
            return
        # /command arg...
        parts = text.split(None, 1)
        cmd = parts[0].lower().split("@")[0].lstrip("/")
        arg = parts[1].strip() if len(parts) > 1 else ""
        do_action(chat_id, cmd, arg)
    except Exception as e:
        print("handle error", e)


# ---------------- Polling mode (Termux/Render/local) ----------------

def main_polling():
    print("KMJ Toast Bot polling started. Owners:", OWNER_IDS, "| Storage: Telegram pinned message")
    tg_call("deleteWebhook", {})
    offset = 0
    while True:
        try:
            req = urllib.request.Request(
                TG + "/getUpdates?timeout=30&offset=" + str(offset))
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read().decode("utf-8"))
            for u in d.get("result", []):
                offset = u["update_id"] + 1
                handle_update(u)
        except Exception as e:
            print("poll error", e)
            time.sleep(3)


if __name__ == "__main__":
    main_polling()


# ---------------- Vercel webhook handler (place this same file as api/index.py) ----------------

from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    """The Vercel Python runtime looks for this 'handler' class."""

    def do_GET(self):
        if "config" in (self.path or ""):
            cfg = serve_config()
            if cfg is None:
                body = b'{"enabled": false}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps({"ok": True, "bot": "KMJ TIPS Toast Control",
                           "mode": "webhook",
                           "owner_set": bool(OWNER_IDS),
                           "storage": "telegram"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            update = json.loads(self.rfile.read(length).decode("utf-8"))
            handle_update(update)
        except Exception as e:
  
