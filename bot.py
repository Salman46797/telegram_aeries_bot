import os
import re
import time
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from flask import Flask, request, jsonify
from supabase import create_client


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

DELETE_AFTER = 30

CACHE_SYNC_INTERVAL = max(
    30,
    int(os.getenv("CACHE_SYNC_INTERVAL", "90"))
)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is missing")

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

app = Flask(__name__)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ============================================================
# HTTP / THREADS
# ============================================================

HTTP = requests.Session()

HTTP.mount(
    "https://",
    HTTPAdapter(
        pool_connections=32,
        pool_maxsize=64,
        max_retries=0
    )
)

EXEC = ThreadPoolExecutor(max_workers=16)
MEDIA_EXEC = ThreadPoolExecutor(max_workers=4)


# ============================================================
# CACHE
# ============================================================

CACHE_LOCK = threading.RLock()

EPISODES = {}
TYPE_INDEX = {}
SPONSORS = []
PENDING = {}

BOT_USERNAME = ""
CACHE_READY = False


# ============================================================
# DELIVERY
# ============================================================

DELIVERY_LOCK = threading.RLock()
DELIVERING = set()


# ============================================================
# ADMIN
# ============================================================

ADMIN_STATE = {}
BROADCAST_RUNNING = False


# ============================================================
# TELEGRAM
# ============================================================

def tg(method, data=None, timeout=30):
    try:
        r = HTTP.post(
            f"{TG_API}/{method}",
            json=data or {},
            timeout=(4, timeout)
        )
        return r.json()
    except Exception as e:
        print("TG ERROR:", method, e)
        return {"ok": False}


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return tg("sendMessage", data)


def delete_message(chat_id, message_id):
    return tg(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


def answer_callback(callback_id, text=None, alert=False):
    data = {
        "callback_query_id": callback_id,
        "show_alert": alert
    }

    if text:
        data["text"] = text

    return tg("answerCallbackQuery", data)


def get_chat_member(chat_id, user_id):
    return tg(
        "getChatMember",
        {
            "chat_id": chat_id,
            "user_id": user_id
        }
    )


def get_me():
    return tg("getMe")


def send_video(chat_id, file_id, caption="", entities=None):
    data = {
        "chat_id": chat_id,
        "video": file_id,
        "supports_streaming": True
    }

    if caption:
        data["caption"] = caption

    if entities:
        data["caption_entities"] = entities

    return tg("sendVideo", data)


def send_document(chat_id, file_id, caption="", entities=None):
    data = {
        "chat_id": chat_id,
        "document": file_id
    }

    if caption:
        data["caption"] = caption

    if entities:
        data["caption_entities"] = entities

    return tg("sendDocument", data)


def send_file(chat_id, f):
    if not f.get("file_id"):
        return {"ok": False}

    entities = f.get("caption_entities") or []
    caption = f.get("caption") or ""

    if f.get("type") == "document":
        return send_document(
            chat_id,
            f["file_id"],
            caption,
            entities
        )

    return send_video(
        chat_id,
        f["file_id"],
        caption,
        entities
    )


# ============================================================
# EPISODES
# ============================================================

def series_key(name):
    return (
        "s" +
        hashlib.sha1(
            name.strip().lower().encode("utf-8")
        ).hexdigest()[:10]
    )


def episode_key(name, number):
    return f"ep_{series_key(name)}_{int(number)}"


def preview_key(name, number):
    return "preview__" + episode_key(name, number)


def type_code(ep_key, file_type):
    raw = f"{ep_key}|{file_type}"

    return (
        "t_" +
        hashlib.sha1(
            raw.encode("utf-8")
        ).hexdigest()[:12]
    )


def normalize_type(caption):
    text = (
        caption or ""
    ).replace("ي", "ی").replace("ك", "ک")

    if "زبان اصلی" in text or "زبان‌اصلی" in text:
        return "زبان اصلی"

    if "زیرنویس فوری" in text or "زیرنویس‌فوری" in text:
        return "زیرنویس فوری"

    if (
        "زیرنویس مووی باز" in text
        or "زیرنویس‌مووی‌ باز" in text
        or "زیرنویس مووی‌باز" in text
    ):
        return "زیرنویس مووی باز"

    return "سایر"


def enrich_files(files, key):
    result = []

    for raw in files or []:
        f = dict(raw)

        ft = (
            f.get("file_type")
            or normalize_type(
                f.get("caption", "")
            )
        )

        f["file_type"] = ft
        f["type_code"] = (
            f.get("type_code")
            or type_code(key, ft)
        )

        f["caption_entities"] = (
            f.get("caption_entities") or []
        )

        result.append(f)

    return result


def parse_caption(caption):
    if not caption:
        return None

    sm = re.search(
        r"سریال\s*[«\"]([^»\"]+)[»\"]",
        caption,
        re.I
    )

    if not sm:
        sm = re.search(
            r"سریال\s*[:：\-]?\s*(.+?)(?:\n|$)",
            caption,
            re.I
        )

    em = re.search(
        r"قسمت\s*[:：\-]?\s*(\d+)",
        caption,
        re.I
    )

    if not sm or not em:
        return None

    name = sm.group(1).strip()
    number = int(em.group(1))

    return {
        "series_name": name,
        "episode_number": number,
        "episode_key": episode_key(
            name,
            number
        )
    }


def is_preview(caption):
    return bool(
        re.search(
            r"پیش[\s‌-]*نمایش|\bpreview\b",
            caption or "",
            re.I
        )
    )


# ============================================================
# CACHE
# ============================================================

def cache_episode(row):
    if not row or not row.get("episode_key"):
        return

    key = row["episode_key"]
    row = dict(row)

    row["files"] = enrich_files(
        row.get("files") or [],
        key
    )

    EPISODES[key] = row

    for f in row["files"]:
        code = f.get("type_code")

        if code:
            TYPE_INDEX[code] = (
                key,
                f.get("file_type")
            )


def sync_cache():
    global SPONSORS
    global BOT_USERNAME
    global CACHE_READY

    try:
        er = (
            supabase
            .table("episodes")
            .select("*")
            .execute()
        )

        sr = (
            supabase
            .table("sponsors")
            .select("*")
            .order("id")
            .execute()
        )

        episodes = {}
        types = {}

        for row in er.data or []:
            key = row.get("episode_key")

            if not key:
                continue

            row = dict(row)
            row["files"] = enrich_files(
                row.get("files") or [],
                key
            )

            episodes[key] = row

            for f in row["files"]:
                code = f.get("type_code")

                if code:
                    types[code] = (
                        key,
                        f.get("file_type")
                    )

        if not BOT_USERNAME:
            BOT_USERNAME = (
                get_me().get("result") or {}
            ).get("username", "")

        with CACHE_LOCK:
            EPISODES.clear()
            EPISODES.update(episodes)

            TYPE_INDEX.clear()
            TYPE_INDEX.update(types)

            SPONSORS = list(
                sr.data or []
            )

            CACHE_READY = True

        print(
            "CACHE:",
            len(EPISODES),
            "episodes /",
            len(TYPE_INDEX),
            "types /",
            len(SPONSORS),
            "sponsors"
        )

        return True

    except Exception as e:
        print("CACHE ERROR:", e)
        return False


def cache_loop():
    while True:
        time.sleep(CACHE_SYNC_INTERVAL)
        sync_cache()


def get_episode(key):
    with CACHE_LOCK:
        if key in EPISODES:
            return EPISODES[key]

    try:
        r = (
            supabase
            .table("episodes")
            .select("*")
            .eq("episode_key", key)
            .limit(1)
            .execute()
        )

        row = r.data[0] if r.data else None

        if row:
            with CACHE_LOCK:
                cache_episode(row)

        return row

    except Exception as e:
        print("GET EP ERROR:", e)
        return None


def save_episode(key, name, number, files):
    files = enrich_files(files, key)

    payload = {
        "episode_key": key,
        "series_name": name,
        "episode_number": number,
        "files": files
    }

    try:
        r = (
            supabase
            .table("episodes")
            .upsert(
                payload,
                on_conflict="episode_key"
            )
            .execute()
        )

        with CACHE_LOCK:
            cache_episode(payload)

        return r

    except Exception as e:
        print("SAVE ERROR:", e)
        return None


def delete_episode(key):
    try:
        r = (
            supabase
            .table("episodes")
            .delete()
            .eq("episode_key", key)
            .execute()
        )

        with CACHE_LOCK:
            EPISODES.pop(key, None)

            for code, value in list(TYPE_INDEX.items()):
                if value[0] == key:
                    TYPE_INDEX.pop(code, None)

        return r

    except Exception as e:
        print("DELETE ERROR:", e)
        return None


def delete_all_episodes():
    try:
        r = (
            supabase
            .table("episodes")
            .delete()
            .neq("episode_key", "")
            .execute()
        )

        with CACHE_LOCK:
            EPISODES.clear()
            TYPE_INDEX.clear()

        return r

    except Exception as e:
        print("DELETE ALL ERROR:", e)
        return None


# ============================================================
# PENDING
# ============================================================

def set_pending(user_id, value):
    user_id = int(user_id)
    PENDING[user_id] = value

    try:
        (
            supabase
            .table("pending")
            .upsert(
                {
                    "user_id": str(user_id),
                    "episode_key": value
                },
                on_conflict="user_id"
            )
            .execute()
        )
    except Exception as e:
        print("PENDING SAVE:", e)


def get_pending(user_id):
    user_id = int(user_id)

    if PENDING.get(user_id):
        return PENDING[user_id]

    try:
        r = (
            supabase
            .table("pending")
            .select("episode_key")
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )

        if r.data:
            value = r.data[0].get("episode_key")

            if value:
                PENDING[user_id] = value
                return value

    except Exception as e:
        print("PENDING GET:", e)

    return None


def clear_pending(user_id):
    user_id = int(user_id)
    value = PENDING.pop(user_id, None)

    try:
        q = (
            supabase
            .table("pending")
            .delete()
            .eq("user_id", str(user_id))
        )

        if value:
            q = q.eq("episode_key", value)

        q.execute()

    except Exception as e:
        print("PENDING DELETE:", e)


# ============================================================
# SPONSORS
# ============================================================

def get_sponsors():
    with CACHE_LOCK:
        return list(SPONSORS)


def add_sponsor(chat_id, title, url):
    global SPONSORS

    try:
        r = (
            supabase
            .table("sponsors")
            .insert(
                {
                    "chat_id": chat_id,
                    "title": title,
                    "url": url
                }
            )
            .execute()
        )

        if r.data:
            with CACHE_LOCK:
                SPONSORS.append(r.data[0])

        return r

    except Exception as e:
        print("SPONSOR ADD:", e)
        return None


def remove_sponsor(sponsor_id):
    global SPONSORS

    try:
        r = (
            supabase
            .table("sponsors")
            .delete()
            .eq("id", int(sponsor_id))
            .execute()
        )

        with CACHE_LOCK:
            SPONSORS = [
                x for x in SPONSORS
                if int(x.get("id", -1)) != int(sponsor_id)
            ]

        return r

    except Exception as e:
        print("SPONSOR REMOVE:", e)
        return None


# ============================================================
# STATS
# ============================================================

def record_stat(
    user_id,
    event_type,
    episode_key=None,
    file_type=None,
    file_count=0
):
    try:
        (
            supabase
            .table("bot_stats")
            .insert(
                {
                    "user_id": int(user_id),
                    "event_type": event_type,
                    "episode_key": episode_key,
                    "file_type": file_type,
                    "file_count": int(file_count or 0)
                }
            )
            .execute()
        )
    except Exception as e:
        print("STATS:", e)


def get_all_users():
    users = set()
    start = 0
    step = 1000

    while True:
        try:
            r = (
                supabase
                .table("bot_stats")
                .select("user_id")
                .range(start, start + step - 1)
                .execute()
            )

            rows = r.data or []

            for x in rows:
                uid = x.get("user_id")
                if uid:
                    users.add(int(uid))

            if len(rows) < step:
                break

            start += step

        except Exception as e:
            print("USERS:", e)
            break

    return list(users)


def stats_report():
    try:
        r = (
            supabase
            .table("bot_stats")
            .select(
                "user_id,event_type,episode_key,file_count,created_at"
            )
            .limit(10000)
            .execute()
        )

        rows = r.data or []

        now = time.time()
        today = time.strftime(
            "%Y-%m-%d",
            time.localtime(now)
        )

        month = time.strftime(
            "%Y-%m",
            time.localtime(now)
        )

        users = {
            int(x["user_id"])
            for x in rows
            if x.get("user_id")
        }

        starts = [
            x for x in rows
            if x.get("event_type") == "start"
        ]

        downloads = [
            x for x in rows
            if x.get("event_type") == "download"
        ]

        uploads = [
            x for x in rows
            if x.get("event_type") == "upload"
        ]

        def is_day(x):
            return str(
                x.get("created_at", "")
            )[:10] == today

        def is_month(x):
            return str(
                x.get("created_at", "")
            )[:7] == month

        today_downloads = sum(
            1 for x in downloads
            if is_day(x)
        )

        month_downloads = sum(
            1 for x in downloads
            if is_month(x)
        )

        files = sum(
            int(x.get("file_count") or 0)
            for x in downloads
        )

        episode_count = {}

        for x in downloads:
            key = x.get("episode_key")

            if key:
                episode_count[key] = (
                    episode_count.get(key, 0) + 1
                )

        top = sorted(
            episode_count.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]

        lines = [
            "📊 آمار پیشرفته",
            "",
            f"👥 کاربران شناخته‌شده: {len(users)}",
            f"▶️ کل /start: {len(starts)}",
            f"📥 کل دانلود: {len(downloads)}",
            f"📥 دانلود امروز: {today_downloads}",
            f"📆 دانلود این ماه: {month_downloads}",
            f"📦 فایل‌های ارسال‌شده: {files}",
            f"📤 آپلودها: {len(uploads)}",
            "",
            "🏆 پربازدیدترین قسمت‌ها:"
        ]

        if not top:
            lines.append("هنوز آماری ثبت نشده.")
        else:
            for key, count in top:
                lines.append(
                    f"• {key}: {count} دانلود"
                )

        return "\n".join(lines)

    except Exception as e:
        return f"❌ خطا در آمار:\n{e}"


# ============================================================
# MEMBERSHIP
# ============================================================

def member_ok(chat_id, user_id):
    r = get_chat_member(chat_id, user_id)

    if not r.get("ok"):
        return False

    m = r.get("result") or {}
    status = m.get("status", "")

    if status in (
        "creator",
        "administrator",
        "member"
    ):
        return True

    if status == "restricted":
        return bool(m.get("is_member"))

    return False


def check_membership(user_id):
    targets = [
        (CHANNEL_ID, None)
    ]

    for s in get_sponsors():
        if s.get("chat_id"):
            targets.append(
                (s["chat_id"], s)
            )

    futures = [
        EXEC.submit(
            member_ok,
            chat_id,
            user_id
        )
        for chat_id, _ in targets
    ]

    results = []

    for f in futures:
        try:
            results.append(bool(f.result()))
        except Exception:
            results.append(False)

    main_ok = bool(results) and results[0]
    missing = []

    for i in range(1, len(targets)):
        if not results[i] and targets[i][1]:
            missing.append(targets[i][1])

    return main_ok, missing


# ============================================================
# USER KEYBOARDS
# ============================================================

def join_keyboard(missing=None):
    rows = []

    if missing is None:
        rows.append([
            {
                "text": "عضویت در کانال 📺",
                "url": CHANNEL_URL
            }
        ])

        for s in get_sponsors():
            if s.get("url"):
                rows.append([
                    {
                        "text": f"عضویت در {s.get('title') or 'اسپانسر'}",
                        "url": s["url"]
                    }
                ])

    else:
        for s in missing:
            if s.get("url"):
                rows.append([
                    {
                        "text": f"عضویت در {s.get('title') or 'اسپانسر'}",
                        "url": s["url"]
                    }
                ])

    rows.append([
        {
            "text": "عضو شدم ✅",
            "callback_data": "check_join"
        }
    ])

    return {"inline_keyboard": rows}


def reaction_keyboard():
    return {
        "inline_keyboard": [[
            {
                "text": "انجام شد ✅",
                "callback_data": "check_reactions"
            }
        ]]
    }


def redownload_keyboard(target):
    global BOT_USERNAME

    if not BOT_USERNAME:
        BOT_USERNAME = (
            get_me().get("result") or {}
        ).get("username", "")

    if not BOT_USERNAME:
        return None

    return {
        "inline_keyboard": [[
            {
                "text": "دانلود مجدد ♻️",
                "url":
                    f"https://t.me/{BOT_USERNAME}?start={target}"
            }
        ]]
    }


# ============================================================
# USER FLOW
# ============================================================

def send_reaction_page(chat_id):
    send_message(
        chat_id,
        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر "
        "کانال @altiustuistsnbol را ری‌اکت بزنید "
        "و سپس روی «انجام شد» بزنید ♥️",
        reaction_keyboard()
    )


def send_join_page(chat_id, user_id):
    main_ok, missing = check_membership(user_id)

    if main_ok and not missing:
        send_reaction_page(chat_id)
        return

    if not main_ok:
        send_message(
            chat_id,
            "📣 برای دریافت فایل ابتدا عضو کانال اصلی شو.",
            join_keyboard(None)
        )
        return

    send_message(
        chat_id,
        "📣 هنوز عضویت بعضی کانال‌ها کامل نیست.",
        join_keyboard(missing)
    )


# ============================================================
# DELIVERY
# ============================================================

def claim_delivery(user_id):
    with DELIVERY_LOCK:
        user_id = int(user_id)

        if user_id in DELIVERING:
            return False

        DELIVERING.add(user_id)
        return True


def release_delivery(user_id):
    with DELIVERY_LOCK:
        DELIVERING.discard(int(user_id))


def delete_files_later(chat_id, ids):
    time.sleep(DELETE_AFTER)

    for mid in ids:
        delete_message(chat_id, mid)


def deliver_episode(chat_id, user_id):
    if not claim_delivery(user_id):
        send_message(
            chat_id,
            "⏳ فایل در حال ارسال است..."
        )
        return

    try:
        pending = get_pending(user_id)

        if not pending:
            send_message(
                chat_id,
                "❌ لینک قسمت پیدا نشد."
            )
            return

        selected_type = None
        real_key = pending

        if pending.startswith("TYPE::"):
            p = pending.split("::", 2)

            if len(p) != 3:
                send_message(
                    chat_id,
                    "❌ لینک فایل نامعتبر است."
                )
                return

            real_key = p[1]
            selected_type = p[2]

        episode = get_episode(real_key)

        if not episode:
            clear_pending(user_id)
            send_message(
                chat_id,
                "❌ این قسمت دیگر موجود نیست."
            )
            return

        files = enrich_files(
            episode.get("files") or [],
            real_key
        )

        if selected_type:
            files = [
                f for f in files
                if f.get("file_type") == selected_type
            ]

        if not files:
            send_message(
                chat_id,
                "❌ فایل پیدا نشد."
            )
            return

        target = (
            type_code(real_key, selected_type)
            if selected_type
            else real_key
        )

        clear_pending(user_id)

        sent = []

        # عمداً ترتیبی؛ برای اینکه هشدار زیر فایل‌ها بماند.
        for f in files:
            r = send_file(chat_id, f)

            if r.get("ok"):
                mid = (
                    r.get("result") or {}
                ).get("message_id")

                if mid:
                    sent.append(mid)

        if not sent:
            send_message(
                chat_id,
                "❌ ارسال فایل انجام نشد."
            )
            return

        send_message(
            chat_id,
            f"⚠️ فایل‌ها بعد از {DELETE_AFTER} ثانیه حذف می‌شوند.\n"
            "قبل از حذف، فایل‌ها را ذخیره کن.",
            redownload_keyboard(target)
        )

        EXEC.submit(
            record_stat,
            user_id,
            "download",
            real_key,
            selected_type,
            len(sent)
        )

        threading.Thread(
            target=delete_files_later,
            args=(chat_id, sent),
            daemon=True
        ).start()

    finally:
        release_delivery(user_id)


# ============================================================
# ADMIN FILE
# ============================================================

def extract_file(message):
    caption = message.get("caption", "")
    entities = message.get("caption_entities") or []

    if message.get("video"):
        return {
            "type": "video",
            "file_id": message["video"].get("file_id"),
            "caption": caption,
            "caption_entities": entities
        }

    if message.get("document"):
        return {
            "type": "document",
            "file_id": message["document"].get("file_id"),
            "caption": caption,
            "caption_entities": entities
        }

    return None


def handle_admin_file(message):
    f = extract_file(message)

    if not f:
        return

    parsed = parse_caption(
        f.get("caption", "")
    )

    if not parsed:
        send_message(
            ADMIN_ID,
            "❌ کپشن قابل تشخیص نیست.\n\n"
            "نمونه:\n"
            "🪴 سریال «اسم سریال»\n"
            "🪷 قسمت : 1\n"
            "🫧 زبان اصلی\n"
            "🎍 کیفیت : 1080"
        )
        return

    preview = is_preview(
        f.get("caption", "")
    )

    key = (
        preview_key(
            parsed["series_name"],
            parsed["episode_number"]
        )
        if preview
        else parsed["episode_key"]
    )

    old = get_episode(key)
    files = list(
        old.get("files") or []
    ) if old else []

    f["file_type"] = normalize_type(
        f.get("caption", "")
    )

    f["type_code"] = type_code(
        key,
        f["file_type"]
    )

    files.append(f)

    saved = save_episode(
        key,
        parsed["series_name"],
        parsed["episode_number"],
        files
    )

    if saved is None:
        send_message(
            ADMIN_ID,
            "❌ ذخیره در Supabase انجام نشد."
        )
        return

    global BOT_USERNAME

    if not BOT_USERNAME:
        BOT_USERNAME = (
            get_me().get("result") or {}
        ).get("username", "")

    if not BOT_USERNAME:
        send_message(
            ADMIN_ID,
            "❌ نام کاربری ربات پیدا نشد."
        )
        return

    groups = {}

    for x in enrich_files(files, key):
        groups[x["file_type"]] = x["type_code"]

    label = "پیش‌نمایش" if preview else "قسمت"

    lines = [
        f"✅ {label} ذخیره شد.",
        "",
        f"🪴 سریال: {parsed['series_name']}",
        f"🪷 قسمت: {parsed['episode_number']}",
        ""
    ]

    for ft, code in groups.items():
        lines.append(f"🎬 {ft}")
        lines.append(
            f"https://t.me/{BOT_USERNAME}?start={code}"
        )
        lines.append("")

    lines.append(
        f"🔗 لینک کل {label}:\n"
        f"https://t.me/{BOT_USERNAME}?start={key}"
    )

    send_message(
        ADMIN_ID,
        "\n".join(lines)
    )

    EXEC.submit(
        record_stat,
        ADMIN_ID,
        "preview" if preview else "upload",
        key,
        None,
        1
    )


# ============================================================
# ADMIN INLINE PANEL
# ============================================================

def btn(text, data):
    return {
        "text": text,
        "callback_data": data
    }


def admin_panel():
    return {
        "inline_keyboard": [
            [
                btn("🎬 مدیریت قسمت‌ها", "admin_episodes"),
                btn("📢 اسپانسرها", "admin_sponsors")
            ],
            [
                btn("📣 پیام همگانی", "admin_broadcast"),
                btn("📊 آمار پیشرفته", "admin_stats")
            ],
            [
                btn("📋 لیست قسمت‌ها", "admin_list"),
                btn("🗑 حذف قسمت", "admin_delete")
            ],
            [
                btn("🗑 حذف همه قسمت‌ها", "admin_delete_all")
            ],
            [
                btn("🔄 سینک دیتابیس", "admin_sync"),
                btn("⚡ وضعیت ربات", "admin_status")
            ]
        ]
    }


def admin_back():
    return {
        "inline_keyboard": [[
            btn("🔙 برگشت به پنل", "admin_home")
        ]]
    }


def send_admin_panel(chat_id):
    send_message(
        chat_id,
        "⚙️ پنل مدیریت ربات\n\n"
        "قابلیت موردنظر را انتخاب کن:",
        admin_panel()
    )


# ============================================================
# BROADCAST
# ============================================================

def broadcast_worker(message_id):
    global BROADCAST_RUNNING

    users = get_all_users()

    success = 0
    failed = 0

    for uid in users:
        try:
            r = tg(
                "copyMessage",
                {
                    "chat_id": uid,
                    "from_chat_id": ADMIN_ID,
                    "message_id": message_id
                },
                timeout=20
            )

            if r.get("ok"):
                success += 1
            else:
                failed += 1

        except Exception:
            failed += 1

        time.sleep(0.04)

    BROADCAST_RUNNING = False

    send_message(
        ADMIN_ID,
        "📣 پیام همگانی تمام شد.\n\n"
        f"✅ موفق: {success}\n"
        f"❌ ناموفق: {failed}\n"
        f"👥 مجموع: {len(users)}"
    )

    record_stat(
        ADMIN_ID,
        "broadcast",
        None,
        None,
        success
    )


# ============================================================
# ADMIN CALLBACK
# ============================================================

def handle_admin_callback(callback):
    global BROADCAST_RUNNING

    uid = (
        callback.get("from") or {}
    ).get("id")

    if uid != ADMIN_ID:
        answer_callback(
            callback.get("id"),
            "⛔ دسترسی ندارید.",
            True
        )
        return

    data = callback.get("data", "")
    cid = (
        callback.get("message") or {}
    ).get("chat", {}).get("id")

    answer_callback(callback.get("id"))

    if data == "admin_home":
        send_admin_panel(cid)
        return

    if data == "admin_episodes":
        send_message(
            cid,
            "🎬 مدیریت قسمت‌ها\n\n"
            "برای افزودن قسمت، فایل ویدیویی/Document را "
            "با کپشن استاندارد ارسال کن.",
            admin_back()
        )
        return

    if data == "admin_sponsors":
        sponsors = get_sponsors()

        text = "📢 مدیریت اسپانسرها\n\n"

        if sponsors:
            for s in sponsors:
                text += (
                    f"🆔 {s.get('id')}\n"
                    f"📌 {s.get('title')}\n"
                    f"🔗 {s.get('url')}\n\n"
                )
        else:
            text += "اسپانسری ثبت نشده.\n\n"

        text += (
            "برای افزودن:\n"
            "/add_sponsor @channel | نام کانال | https://t.me/channel\n\n"
            "برای حذف:\n"
            "/remove_sponsor ID"
        )

        send_message(
            cid,
            text,
            admin_back()
        )
        return

    if data == "admin_stats":
        send_message(
            cid,
            stats_report(),
            admin_back()
        )
        return

    if data == "admin_broadcast":
        if BROADCAST_RUNNING:
            send_message(
                cid,
                "⏳ یک پیام همگانی در حال ارسال است.",
                admin_back()
            )
            return

        ADMIN_STATE[ADMIN_ID] = "broadcast"

        send_message(
            cid,
            "📣 پیام همگانی\n\n"
            "حالا پیام موردنظر را همینجا بفرست.\n"
            "متن، عکس، ویدیو یا فایل قابل ارسال است.\n\n"
            "برای لغو: /cancel_broadcast",
            admin_back()
        )
        return

    if data == "admin_list":
        with CACHE_LOCK:
            rows = [
                x for x in EPISODES.values()
                if not str(
                    x.get("episode_key", "")
                ).startswith("preview__")
            ]

        rows.sort(
            key=lambda x: (
                str(x.get("series_name", "")),
                int(x.get("episode_number", 0))
            )
        )

        if not rows:
            text = "📋 هیچ قسمتی ذخیره نشده."
        else:
            text = "📋 قسمت‌های ذخیره‌شده:\n\n"

            for x in rows[:100]:
                text += (
                    f"• {x.get('series_name')} "
                    f"— قسمت {x.get('episode_number')}\n"
                    f"{x.get('episode_key')}\n\n"
                )

            if len(rows) > 100:
                text += "⚠️ فقط ۱۰۰ مورد اول نمایش داده شد."

        send_message(cid, text, admin_back())
        return

    if data == "admin_delete":
        ADMIN_STATE[ADMIN_ID] = "delete"

        send_message(
            cid,
            "🗑 کلید قسمت را بفرست:\n\n"
            "مثال:\n"
            "ep_xxxxxxxxxx_1\n\n"
            "لغو: /cancel",
            admin_back()
        )
        return

    if data == "admin_delete_all":
        ADMIN_STATE[ADMIN_ID] = "delete_all_confirm"

        send_message(
            cid,
            "⚠️ مطمئنی می‌خواهی تمام قسمت‌ها حذف شوند؟",
            {
                "inline_keyboard": [
                    [
                        btn("بله، حذف همه 🗑", "confirm_delete_all"),
                        btn("لغو ❌", "admin_home")
                    ]
                ]
            }
        )
        return

    if data == "confirm_delete_all":
        delete_all_episodes()
        ADMIN_STATE.pop(ADMIN_ID, None)

        send_message(
            cid,
            "✅ تمام قسمت‌ها حذف شدند.",
            admin_panel()
        )
        return

    if data == "admin_sync":
        ok = sync_cache()

        send_message(
            cid,
            "✅ سینک انجام شد."
            if ok else
            "❌ سینک ناموفق بود.",
            admin_back()
        )
        return

    if data == "admin_status":
        try:
            (
                supabase
                .table("episodes")
                .select("episode_key")
                .limit(1)
                .execute()
            )
            db = "🟢"
        except Exception:
            db = "🔴"

        with CACHE_LOCK:
            ep = len(EPISODES)
            sp = len(SPONSORS)

        send_message(
            cid,
            "⚡ وضعیت ربات\n\n"
            "🤖 Telegram: 🟢\n"
            f"🗄 Supabase: {db}\n"
            f"⚡ Cache: {'🟢' if CACHE_READY else '🔴'}\n"
            f"🎬 قسمت‌ها: {ep}\n"
            f"📢 اسپانسرها: {sp}\n"
            f"🔄 سینک خودکار: هر {CACHE_SYNC_INTERVAL} ثانیه",
            admin_back()
        )
        return


# ============================================================
# ADMIN TEXT
# ============================================================

def handle_admin_text(chat_id, text):
    global BROADCAST_RUNNING

    state = ADMIN_STATE.get(ADMIN_ID)

    if text == "/start":
        send_admin_panel(chat_id)
        return True

    if text == "/cancel":
        ADMIN_STATE.pop(ADMIN_ID, None)

        send_admin_panel(chat_id)
        return True

    if text == "/cancel_broadcast":
        ADMIN_STATE.pop(ADMIN_ID, None)

        send_message(
            chat_id,
            "❌ پیام همگانی لغو شد.",
            admin_panel()
        )
        return True

    if text.startswith("/add_sponsor"):
        raw = text[len("/add_sponsor"):].strip()
        parts = [x.strip() for x in raw.split("|")]

        if len(parts) != 3:
            send_message(
                chat_id,
                "فرمت:\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )
            return True

        r = add_sponsor(
            parts[0],
            parts[1],
            parts[2]
        )

        send_message(
            chat_id,
            "✅ اسپانسر اضافه شد."
            if r is not None
            else
            "❌ ذخیره اسپانسر انجام نشد.",
            admin_panel()
        )
        return True

    if text.startswith("/remove_sponsor"):
        p = text.split()

        if len(p) != 2 or not p[1].isdigit():
            send_message(
                chat_id,
                "فرمت:\n/remove_sponsor ID"
            )
            return True

        r = remove_sponsor(int(p[1]))

        send_message(
            chat_id,
            "✅ اسپانسر حذف شد."
            if r is not None
            else
            "❌ حذف اسپانسر انجام نشد.",
            admin_panel()
        )
        return True

    if state == "delete":
        delete_episode(text.strip())
        ADMIN_STATE.pop(ADMIN_ID, None)

        send_message(
            chat_id,
            "✅ قسمت حذف شد.",
            admin_panel()
        )
        return True

    if state == "delete_all_confirm":
        return True

    return False


# ============================================================
# WEBHOOK
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return "Bot is running."


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True) or {}

    # ========================================================
    # MESSAGE
    # ========================================================

    message = update.get("message")

    if message:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        chat_id = chat.get("id")
        user_id = sender.get("id")
        text = message.get("text", "")

        # ----------------------------------------------------
        # ADMIN
        # ----------------------------------------------------

        if user_id == ADMIN_ID:

            # Broadcast MUST be checked before admin-file handling.
            if ADMIN_STATE.get(ADMIN_ID) == "broadcast":
                if text == "/cancel_broadcast":
                    ADMIN_STATE.pop(ADMIN_ID, None)

                    send_message(
                        chat_id,
                        "❌ پیام همگانی لغو شد.",
                        admin_panel()
                    )

                    return jsonify({"ok": True})

                if BROADCAST_RUNNING:
                    send_message(
                        chat_id,
                        "⏳ یک ارسال همگانی در حال انجام است."
                    )
                    return jsonify({"ok": True})

                ADMIN_STATE.pop(ADMIN_ID, None)
                BROADCAST_RUNNING = True

                send_message(
                    chat_id,
                    "📣 پیام دریافت شد.\n"
                    "ارسال برای کاربران شروع شد..."
                )

                MEDIA_EXEC.submit(
                    broadcast_worker,
                    message.get("message_id")
                )

                return jsonify({"ok": True})

            if message.get("video") or message.get("document"):
                handle_admin_file(message)
                return jsonify({"ok": True})

            if text and handle_admin_text(chat_id, text):
                return jsonify({"ok": True})

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        if text.startswith("/start"):
            parts = text.split(maxsplit=1)

            if len(parts) == 1:
                if user_id != ADMIN_ID:
                    send_message(
                        chat_id,
                        "سلام 👋\n"
                        "لینک قسمت موردنظرت را باز کن."
                    )

                EXEC.submit(
                    record_stat,
                    user_id,
                    "start"
                )

                return jsonify({"ok": True})

            token = parts[1].strip()

            if token.startswith("t_"):
                with CACHE_LOCK:
                    target = TYPE_INDEX.get(token)

                if not target:
                    sync_cache()

                    with CACHE_LOCK:
                        target = TYPE_INDEX.get(token)

                if not target:
                    send_message(
                        chat_id,
                        "❌ لینک فایل پیدا نشد."
                    )
                    return jsonify({"ok": True})

                real_key, file_type = target

                set_pending(
                    user_id,
                    f"TYPE::{real_key}::{file_type}"
                )

            else:
                if not get_episode(token):
                    send_message(
                        chat_id,
                        "❌ این قسمت پیدا نشد."
                    )
                    return jsonify({"ok": True})

                set_pending(
                    user_id,
                    token
                )

            EXEC.submit(
                record_stat,
                user_id,
                "start"
            )

            send_join_page(
                chat_id,
                user_id
            )

            return jsonify({"ok": True})

    # ========================================================
    # CALLBACK
    # ========================================================

    callback = update.get("callback_query")

    if callback:
        uid = (
            callback.get("from") or {}
        ).get("id")

        data = callback.get("data", "")

        if uid == ADMIN_ID and data.startswith("admin_"):
            handle_admin_callback(callback)
            return jsonify({"ok": True})

        if uid == ADMIN_ID and data == "confirm_delete_all":
            handle_admin_callback(callback)
            return jsonify({"ok": True})

        cid = (
            callback.get("message") or {}
        ).get("chat", {}).get("id")

        mid = (
            callback.get("message") or {}
        ).get("message_id")

        if data == "check_join":
            answer_callback(
                callback.get("id"),
                "در حال بررسی عضویت..."
            )

            pending = get_pending(uid)

            if not pending:
                send_message(
                    cid,
                    "❌ لینک قسمت پیدا نشد."
                )
                return jsonify({"ok": True})

            main_ok, missing = check_membership(uid)

            if main_ok and not missing:
                if mid:
                    delete_message(cid, mid)

                send_reaction_page(cid)
                return jsonify({"ok": True})

            if not main_ok:
                send_message(
                    cid,
                    "هنوز عضو کانال اصلی نیستی 👇",
                    join_keyboard(None)
                )
            else:
                send_message(
                    cid,
                    "هنوز عضویت بعضی کانال‌ها تأیید نشده 👇",
                    join_keyboard(missing)
                )

            return jsonify({"ok": True})

        if data == "check_reactions":
            answer_callback(
                callback.get("id"),
                "در حال ارسال فایل..."
            )

            if mid:
                delete_message(cid, mid)

            deliver_episode(
                cid,
                uid
            )

            return jsonify({"ok": True})

    return jsonify({"ok": True})


# ============================================================
# START
# ============================================================

def setup_webhook():
    base = os.getenv(
        "RENDER_EXTERNAL_URL",
        "https://telegram-aeries-bot.onrender.com"
    ).rstrip("/")

    url = base + "/webhook"

    result = tg(
        "setWebhook",
        {
            "url": url,
            "allowed_updates": [
                "message",
                "callback_query"
            ],
            "drop_pending_updates": False
        }
    )

    print("WEBHOOK:", url)
    print("RESULT:", result)


sync_cache()
setup_webhook()

threading.Thread(
    target=cache_loop,
    daemon=True
).start()


if __name__ == "__main__":
    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
