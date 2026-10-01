import os
import re
import time
import hashlib
import threading
import secrets
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import requests
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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is missing")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def tg(method, data=None, timeout=30):
    try:
        r = requests.post(
            f"{TG_API}/{method}",
            json=data or {},
            timeout=timeout
        )
        return r.json()
    except Exception as e:
        print("Telegram error:", method, e)
        return {"ok": False}


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }
    if reply_markup is not None:
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


def edit_message(chat_id, message_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }
    if reply_markup is not None:
        data["reply_markup"] = reply_markup
    return tg("editMessageText", data)


def answer_callback(callback_id, text=None, show_alert=False):
    data = {
        "callback_query_id": callback_id,
        "show_alert": show_alert
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


def get_chat(chat_id):
    return tg("getChat", {"chat_id": chat_id})


def get_me():
    return tg("getMe")


def send_video(chat_id, file_id, caption=None):
    data = {
        "chat_id": chat_id,
        "video": file_id,
        "supports_streaming": True
    }
    if caption:
        data["caption"] = caption
    return tg("sendVideo", data)


def send_document(chat_id, file_id, caption=None):
    data = {
        "chat_id": chat_id,
        "document": file_id
    }
    if caption:
        data["caption"] = caption
    return tg("sendDocument", data)


def send_media_file(chat_id, file_info):
    file_type = file_info.get("type", "video")
    file_id = file_info.get("file_id")
    caption = file_info.get("caption", "")

    if not file_id:
        return {"ok": False}

    if file_type == "document":
        return send_document(chat_id, file_id, caption)

    return send_video(chat_id, file_id, caption)


# ============================================================
# KEY / CAPTION HELPERS
# ============================================================

def make_series_key(series_name):
    series_hash = hashlib.sha1(
        series_name.strip().lower().encode("utf-8")
    ).hexdigest()[:10]
    return "s" + series_hash


def make_episode_key(series_name, episode_number):
    return (
        "ep_"
        + make_series_key(series_name)
        + "_"
        + str(episode_number)
    )


def make_preview_key(series_name, episode_number):
    return "preview__" + make_episode_key(series_name, episode_number)


def is_preview_caption(caption):
    return bool(re.search(
        r"پیش[\s‌-]*نمایش",
        caption or "",
        re.IGNORECASE
    )) or bool(re.search(
        r"\bpreview\b",
        caption or "",
        re.IGNORECASE
    ))


def normalize_file_type(caption):
    text = (caption or "").replace("ي", "ی").replace("ك", "ک")

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

    for line in text.splitlines():
        line = line.strip()

        if (
            line
            and not line.startswith(("🪴", "🪷", "🎍"))
            and "سریال" not in line
            and "قسمت" not in line
            and "کیفیت" not in line
        ):
            return line[:80]

    return "سایر"


def make_type_code(episode_key, file_type):
    raw = f"{episode_key}|{file_type}".encode("utf-8")
    return "t_" + hashlib.sha1(raw).hexdigest()[:12]


def enrich_files(files, episode_key):
    out = []

    for f in files or []:
        item = dict(f)

        ftype = (
            item.get("file_type")
            or normalize_file_type(item.get("caption", ""))
        )

        item["file_type"] = ftype

        item["type_code"] = (
            item.get("type_code")
            or make_type_code(episode_key, ftype)
        )

        out.append(item)

    return out


def parse_caption(caption):
    if not caption:
        return None

    series_match = re.search(
        r"سریال\s*[«\"]([^»\"]+)[»\"]",
        caption,
        re.IGNORECASE
    )

    if not series_match:
        series_match = re.search(
            r"سریال\s*[:：\-]?\s*(.+?)(?:\n|$)",
            caption,
            re.IGNORECASE
        )

    episode_match = re.search(
        r"قسمت\s*[:：\-]?\s*(\d+)",
        caption,
        re.IGNORECASE
    )

    if not series_match or not episode_match:
        return None

    series_name = series_match.group(1).strip()
    episode_number = int(episode_match.group(1))

    return {
        "series_name": series_name,
        "episode_number": episode_number,
        "episode_key": make_episode_key(
            series_name,
            episode_number
        )
    }


# ============================================================
# SUPABASE - EPISODES
# ============================================================

def get_episode(episode_key):
    try:
        result = (
            supabase
            .table("episodes")
            .select("*")
            .eq("episode_key", episode_key)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:
        print("get_episode error:", e)

    return None


def save_episode(
    episode_key,
    series_name,
    episode_number,
    files
):
    files = enrich_files(files, episode_key)

    payload = {
        "episode_key": episode_key,
        "series_name": series_name,
        "episode_number": episode_number,
        "files": files
    }

    try:
        return (
            supabase
            .table("episodes")
            .upsert(
                payload,
                on_conflict="episode_key"
            )
            .execute()
        )

    except Exception as e:
        print("save_episode error:", e)
        return None


def delete_episode_from_db(episode_key):
    try:
        return (
            supabase
            .table("episodes")
            .delete()
            .eq("episode_key", episode_key)
            .execute()
        )

    except Exception as e:
        print("delete_episode error:", e)
        return None


def delete_all_episodes():
    try:
        return (
            supabase
            .table("episodes")
            .delete()
            .neq("episode_key", "")
            .execute()
        )

    except Exception as e:
        print("delete_all_episodes error:", e)
        return None


# ============================================================
# SUPABASE - PENDING
# ============================================================

def set_pending(user_id, episode_key):
    try:
        return (
            supabase
            .table("pending")
            .upsert(
                {
                    "user_id": str(user_id),
                    "episode_key": episode_key
                },
                on_conflict="user_id"
            )
            .execute()
        )

    except Exception as e:
        print("set_pending error:", e)
        return None


def get_pending(user_id):
    try:
        result = (
            supabase
            .table("pending")
            .select("*")
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0].get("episode_key")

    except Exception as e:
        print("get_pending error:", e)

    return None


def clear_pending(user_id):
    try:
        return (
            supabase
            .table("pending")
            .delete()
            .eq("user_id", str(user_id))
            .execute()
        )

    except Exception as e:
        print("clear_pending error:", e)

    return None


def clear_all_pending():
    try:
        return (
            supabase
            .table("pending")
            .delete()
            .neq("user_id", "")
            .execute()
        )

    except Exception as e:
        print("clear_all_pending error:", e)

    return None


# ============================================================
# SUPABASE - SPONSORS
# ============================================================

def get_sponsors():
    try:
        result = (
            supabase
            .table("sponsors")
            .select("*")
            .order("id")
            .execute()
        )

        return result.data or []

    except Exception as e:
        print("get_sponsors error:", e)
        return []


def add_sponsor(chat_id, title, url):
    try:
        return (
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

    except Exception as e:
        print("add_sponsor error:", e)
        return None


def remove_sponsor(sponsor_id):
    try:
        return (
            supabase
            .table("sponsors")
            .delete()
            .eq("id", sponsor_id)
            .execute()
        )

    except Exception as e:
        print("remove_sponsor error:", e)
        return None


# ============================================================
# SUPABASE - CHANNEL POSTS / REACTIONS
# ============================================================

def save_channel_post(message_id):
    try:
        (
            supabase
            .table("channel_posts")
            .upsert(
                {
                    "message_id": int(message_id)
                },
                on_conflict="message_id"
            )
            .execute()
        )

    except Exception as e:
        print("save_channel_post error:", e)


def get_last_posts(limit=5):
    try:
        result = (
            supabase
            .table("channel_posts")
            .select("message_id,created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        return result.data or []

    except Exception as e:
        print("get_last_posts error:", e)
        return []


def save_reaction(user_id, message_id, reacted=True):
    try:
        (
            supabase
            .table("reactions")
            .delete()
            .eq("user_id", int(user_id))
            .eq("message_id", int(message_id))
            .execute()
        )

        (
            supabase
            .table("reactions")
            .insert(
                {
                    "user_id": int(user_id),
                    "message_id": int(message_id),
                    "reacted": bool(reacted)
                }
            )
            .execute()
        )

    except Exception as e:
        print("save_reaction error:", e)


def has_reacted(user_id, message_id):
    try:
        result = (
            supabase
            .table("reactions")
            .select("reacted")
            .eq("user_id", int(user_id))
            .eq("message_id", int(message_id))
            .limit(1)
            .execute()
        )

        if result.data:
            return bool(result.data[0].get("reacted"))

    except Exception as e:
        print("has_reacted error:", e)

    return False


def user_reacted_to_last_posts(user_id, limit=5):
    posts = get_last_posts(limit)

    if len(posts) < limit:
        return False, len(posts)

    for post in posts:
        message_id = post.get("message_id")

        if not message_id:
            return False, len(posts)

        if not has_reacted(user_id, message_id):
            return False, len(posts)

    return True, len(posts)


# ============================================================
# SUPABASE - STATS
# ============================================================

def record_stat(
    user_id,
    event_type,
    episode_key=None,
    file_type=None,
    file_count=0
):
    try:
        return (
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
        print("record_stat error:", e)
        return None


def get_stats():
    try:
        result = (
            supabase
            .table("bot_stats")
            .select(
                "user_id,event_type,episode_key,file_type,"
                "file_count,created_at"
            )
            .order("created_at", desc=True)
            .limit(10000)
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        print("get_stats error:", e)
        return None

    now = datetime.now(timezone.utc)

    month_prefix = (
        f"{now.year:04d}-{now.month:02d}"
    )

    month = [
        r for r in rows
        if str(r.get("created_at", "")).startswith(month_prefix)
    ]

    downloads = [
        r for r in month
        if r.get("event_type") == "download"
    ]

    previews = [
        r for r in month
        if r.get("event_type") == "preview"
    ]

    users = {
        int(r["user_id"])
        for r in month
        if r.get("event_type") == "download"
        and r.get("user_id") is not None
    }

    all_downloads = [
        r for r in rows
        if r.get("event_type") == "download"
    ]

    all_users = {
        int(r["user_id"])
        for r in rows
        if r.get("event_type") == "download"
        and r.get("user_id") is not None
    }

    files = sum(
        int(r.get("file_count") or 0)
        for r in downloads
    )

    return {
        "month": month_prefix,
        "monthly_downloads": len(downloads),
        "monthly_files": files,
        "monthly_users": len(users),
        "monthly_previews": len(previews),
        "total_downloads": len(all_downloads),
        "total_users": len(all_users)
    }


# ============================================================
# MEMBERSHIP
# ============================================================

def member_status(chat_id, user_id):
    result = get_chat_member(chat_id, user_id)

    if not result.get("ok"):
        return False

    status = result.get("result", {}).get("status", "")

    return status in (
        "creator",
        "administrator",
        "member"
    )


def is_main_channel_member(user_id):
    return member_status(CHANNEL_ID, user_id)


def check_all_sponsors(user_id):
    sponsors = get_sponsors()

    missing = []

    for sponsor in sponsors:
        chat_id = sponsor.get("chat_id")

        if not chat_id:
            continue

        if not member_status(chat_id, user_id):
            missing.append(sponsor)

    return missing


# ============================================================
# KEYBOARDS
# ============================================================

def sponsor_keyboard(sponsors):
    rows = []

    for sponsor in sponsors:
        title = sponsor.get("title") or "کانال اسپانسر"
        url = sponsor.get("url")

        if url:
            rows.append(
                [
                    {
                        "text": f"عضویت در {title}",
                        "url": url
                    }
                ]
            )

    rows.append(
        [
            {
                "text": "عضو شدم ✅",
                "callback_data": "check_join"
            }
        ]
    )

    return {
        "inline_keyboard": rows
    }


def reaction_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "انجام شد ✅",
                    "callback_data": "check_reactions"
                }
            ]
        ]
    }


def main_channel_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "ورود به کانال 📺",
                    "url": CHANNEL_URL
                }
            ]
        ]
    }


# ============================================================
# USER FLOW
# ============================================================

def show_join_page(chat_id, user_id):
    if not is_main_channel_member(user_id):
        send_message(
            chat_id,
            "برای دریافت فایل اول باید عضو کانال اصلی بشی 👇",
            main_channel_keyboard()
        )
        return False

    missing = check_all_sponsors(user_id)

    if missing:
        send_message(
            chat_id,
            "برای دریافت فایل، اول عضو کانال‌های زیر شو 👇",
            sponsor_keyboard(missing)
        )
        return False

    show_reaction_page(chat_id, user_id)
    return True


def show_reaction_page(chat_id, user_id):
    ok, count = user_reacted_to_last_posts(user_id, 5)

    if ok:
        send_episode_to_user(chat_id, user_id)
        return

    send_message(
        chat_id,
        "برای دریافت فایل موردنظرت، ۵ پست آخر این کانال رو ری‌اکشن (❤️) بزن 👇\n\n"
        + CHANNEL_URL,
        reaction_keyboard()
    )


def send_episode_to_user(chat_id, user_id):
    episode_key = get_pending(user_id)

    if not episode_key:
        send_message(
            chat_id,
            "لینک قسمت پیدا نشد. دوباره لینک قسمت رو باز کن."
        )
        return

    selected_type = None
    lookup_key = episode_key

    if episode_key.startswith("TYPE::"):
        parts = episode_key.split("::", 2)

        if len(parts) == 3:
            lookup_key = parts[1]
            selected_type = parts[2]

    episode = get_episode(lookup_key)

    if not episode:
        clear_pending(user_id)

        send_message(
            chat_id,
            "این قسمت دیگه موجود نیست."
        )
        return

    files = enrich_files(
        episode.get("files") or [],
        lookup_key
    )

    if selected_type:
        files = [
            f for f in files
            if f.get("file_type") == selected_type
        ]

    if not files:
        send_message(
            chat_id,
            "فایل این قسمت پیدا نشد."
        )
        return

    clear_pending(user_id)

    sent_ids = []

    for file_info in files:
        result = send_media_file(
            chat_id,
            file_info
        )

        if result.get("ok"):
            sent_message = result.get("result", {})
            message_id = sent_message.get("message_id")

            if message_id:
                sent_ids.append(message_id)

    if not sent_ids:
        send_message(
            chat_id,
            "ارسال فایل انجام نشد. چند لحظه بعد دوباره امتحان کن."
        )
        return

    record_stat(
        user_id,
        "download",
        lookup_key,
        selected_type,
        len(sent_ids)
    )

    send_message(
        chat_id,
        f"فایل‌ها ارسال شد ✅\n"
        f"تا {DELETE_AFTER} ثانیه فرصت داری ذخیره‌شون کنی."
    )

    threading.Thread(
        target=delete_sent_messages_later,
        args=(chat_id, sent_ids),
        daemon=True
    ).start()


def delete_sent_messages_later(chat_id, message_ids):
    time.sleep(DELETE_AFTER)

    for message_id in message_ids:
        delete_message(
            chat_id,
            message_id
        )

    send_message(
        chat_id,
        "فایل‌های ارسالی حذف شدند 🗑️\n"
        "برای دریافت دوباره، لینک قسمت رو دوباره باز کن."
    )


# ============================================================
# ADMIN FILE HANDLING
# ============================================================

def extract_file_from_message(message):
    if message.get("video"):
        video = message["video"]

        return {
            "type": "video",
            "file_id": video.get("file_id"),
            "caption": message.get("caption", "")
        }

    if message.get("document"):
        document = message["document"]

        return {
            "type": "document",
            "file_id": document.get("file_id"),
            "caption": message.get("caption", "")
        }

    return None


def handle_admin_file(message):
    file_info = extract_file_from_message(message)

    if not file_info:
        return False

    caption = file_info.get("caption", "")
    parsed = parse_caption(caption)

    if not parsed:
        send_message(
            ADMIN_ID,
            "❌ کپشن فایل قابل تشخیص نیست.\n\n"
            "نمونه:\n"
            "🪴 سریال «عشق و تخت»\n"
            "🪷 قسمت : 2\n"
            "🫧 زبان اصلی\n"
            "🎍 کیفیت : 1080"
        )
        return True

    is_preview = is_preview_caption(caption)

    episode_key = (
        make_preview_key(
            parsed["series_name"],
            parsed["episode_number"]
        )
        if is_preview
        else parsed["episode_key"]
    )

    episode = get_episode(episode_key)

    files = (
        episode.get("files") or []
        if episode
        else []
    )

    file_info["file_type"] = normalize_file_type(caption)

    file_info["type_code"] = make_type_code(
        episode_key,
        file_info["file_type"]
    )

    files.append(file_info)

    saved = save_episode(
        episode_key=episode_key,
        series_name=parsed["series_name"],
        episode_number=parsed["episode_number"],
        files=files
    )

    if saved is None:
        send_message(
            ADMIN_ID,
            "❌ ذخیره در Supabase انجام نشد."
        )
        return True

    bot_username_result = get_me()

    bot_username = (
        bot_username_result
        .get("result", {})
        .get("username", "")
    )

    base_link = (
        f"https://t.me/{bot_username}"
        if bot_username
        else None
    )

    if not base_link:
        send_message(
            ADMIN_ID,
            "❌ نام کاربری ربات پیدا نشد."
        )
        return True

    groups = {}

    for f in enrich_files(files, episode_key):
        groups.setdefault(
            f["file_type"],
            f["type_code"]
        )

    lines = []

    label = (
        "پیش‌نمایش"
        if is_preview
        else "قسمت"
    )

    lines.append(
        f"✅ {label} ذخیره شد."
    )

    lines.append("")

    lines.append(
        f"🪴 سریال: {parsed['series_name']}"
    )

    lines.append(
        f"🪷 قسمت: {parsed['episode_number']}"
    )

    lines.append("")

    for ftype, code in groups.items():
        lines.append(
            f"🎬 {ftype}"
        )

        lines.append(
            f"🔗 {base_link}?start={code}"
        )

        lines.append("")

    lines.append(
        f"🔗 لینک مستقیم کل {label}: "
        f"{base_link}?start={episode_key}"
    )

    send_message(
        ADMIN_ID,
        "\n".join(lines)
    )

    record_stat(
        ADMIN_ID,
        "preview" if is_preview else "upload",
        episode_key,
        None,
        1
    )

    return True


def find_type_target(type_code):
    try:
        result = (
            supabase
            .table("episodes")
            .select("episode_key,files")
            .execute()
        )

        for row in result.data or []:
            key = row.get("episode_key")

            for f in row.get("files") or []:
                if f.get("type_code") == type_code:
                    return (
                        key,
                        f.get("file_type")
                    )

    except Exception as e:
        print("find_type_target error:", e)

    return None, None


# ============================================================
# ADMIN COMMANDS
# ============================================================

def handle_admin_command(chat_id, user_id, text):
    if user_id != ADMIN_ID:
        return False

    text = text.strip()

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if text == "/start":
        send_message(
            chat_id,
            "پنل مدیریت ربات فعال است ✅\n\n"
            "/sponsors\n"
            "/stats\n"
            "/episodes\n"
            "/delete_all\n"
            "/delete_episode KEY\n"
            "/delete_preview اسم سریال | شماره قسمت\n"
            "/add_sponsor @channel | نام کانال | https://t.me/channel\n"
            "/remove_sponsor ID"
        )
        return True

    # --------------------------------------------------------
    # SPONSORS
    # --------------------------------------------------------

    if text == "/sponsors":
        sponsors = get_sponsors()

        if not sponsors:
            send_message(
                chat_id,
                "هیچ اسپانسری ثبت نشده."
            )
            return True

        lines = [
            "📋 لیست اسپانسرها:\n"
        ]

        for sponsor in sponsors:
            lines.append(
                f"ID: {sponsor.get('id')}\n"
                f"کانال: {sponsor.get('chat_id')}\n"
                f"نام: {sponsor.get('title')}\n"
                f"لینک: {sponsor.get('url')}\n"
            )

        send_message(
            chat_id,
            "\n".join(lines)
        )

        return True

    # --------------------------------------------------------
    # ADD SPONSOR
    # --------------------------------------------------------

    if text.startswith("/add_sponsor"):
        raw = text[
            len("/add_sponsor"):
        ].strip()

        parts = [
            x.strip()
            for x in raw.split("|")
        ]

        if len(parts) != 3:
            send_message(
                chat_id,
                "فرمت درست:\n\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )
            return True

        chat_id_sponsor, title, url = parts

        result = add_sponsor(
            chat_id_sponsor,
            title,
            url
        )

        if result is None:
            send_message(
                chat_id,
                "❌ ذخیره اسپانسر انجام نشد."
            )
        else:
            send_message(
                chat_id,
                "✅ اسپانسر اضافه شد."
            )

        return True

    # --------------------------------------------------------
    # REMOVE SPONSOR
    # --------------------------------------------------------

    if text.startswith("/remove_sponsor"):
        parts = text.split()

        if (
            len(parts) != 2
            or not parts[1].isdigit()
        ):
            send_message(
                chat_id,
                "فرمت درست:\n/remove_sponsor ID"
            )
            return True

        sponsor_id = int(parts[1])

        result = remove_sponsor(
            sponsor_id
        )

        if result is None:
            send_message(
                chat_id,
                "❌ حذف اسپانسر انجام نشد."
            )
        else:
            send_message(
                chat_id,
                "✅ اسپانسر حذف شد."
            )

        return True

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    if text == "/stats":
        stats = get_stats()

        if stats is None:
            send_message(
                chat_id,
                "❌ دریافت آمار انجام نشد.\n"
                "جدول bot_stats در Supabase ساخته نشده "
                "یا دسترسی ندارد."
            )
            return True

        send_message(
            chat_id,
            f"📊 آمار ربات ({stats['month']})\n\n"
            f"👤 کاربران این ماه: "
            f"{stats['monthly_users']}\n"
            f"📥 دریافت قسمت این ماه: "
            f"{stats['monthly_downloads']}\n"
            f"🎬 فایل‌های ارسال‌شده این ماه: "
            f"{stats['monthly_files']}\n"
            f"👁 پیش‌نمایش‌های ثبت‌شده این ماه: "
            f"{stats['monthly_previews']}\n\n"
            f"📈 کل دریافت‌ها: "
            f"{stats['total_downloads']}\n"
            f"👥 کل کاربران ثبت‌شده: "
            f"{stats['total_users']}"
        )

        return True

    # --------------------------------------------------------
    # DELETE PREVIEW
    # --------------------------------------------------------

    if text.startswith("/delete_preview"):
        raw = text[
            len("/delete_preview"):
        ].strip()

        parts = [
            x.strip()
            for x in raw.split("|", 1)
        ]

        if (
            len(parts) != 2
            or not parts[0]
            or not parts[1].isdigit()
        ):
            send_message(
                chat_id,
                "فرمت درست:\n"
                "/delete_preview اسم سریال | شماره قسمت"
            )
            return True

        key = make_preview_key(
            parts[0],
            int(parts[1])
        )

        if not get_episode(key):
            send_message(
                chat_id,
                "❌ پیش‌نمایش این قسمت پیدا نشد."
            )
            return True

        delete_episode_from_db(key)

        send_message(
            chat_id,
            f"✅ پیش‌نمایش قسمت {parts[1]} "
            f"از «{parts[0]}» حذف شد.\n\n"
            "قسمت اصلی هیچ تغییری نکرد."
        )

        return True

    # --------------------------------------------------------
    # EPISODES
    # --------------------------------------------------------

    if text == "/episodes":
        try:
            result = (
                supabase
                .table("episodes")
                .select(
                    "episode_key,"
                    "series_name,"
                    "episode_number"
                )
                .order("series_name")
                .order("episode_number")
                .execute()
            )

            rows = [
                r for r in (result.data or [])
                if not str(
                    r.get("episode_key", "")
                ).startswith("preview__")
            ]

            if not rows:
                send_message(
                    chat_id,
                    "هیچ قسمتی ذخیره نشده."
                )
                return True

            lines = [
                "📺 قسمت‌های ذخیره‌شده:\n"
            ]

            for row in rows:
                lines.append(
                    f"{row.get('series_name')} - "
                    f"قسمت {row.get('episode_number')}\n"
                    f"{row.get('episode_key')}\n"
                )

            send_message(
                chat_id,
                "\n".join(lines)
            )

        except Exception as e:
            print(
                "episodes command error:",
                e
            )

            send_message(
                chat_id,
                "❌ دریافت لیست قسمت‌ها انجام نشد."
            )

        return True

    # --------------------------------------------------------
    # DELETE ALL
    # --------------------------------------------------------

    if text == "/delete_all":
        delete_all_episodes()
        clear_all_pending()

        send_message(
            chat_id,
            "✅ اطلاعات قسمت‌ها و لینک‌های ذخیره‌شده پاک شدند.\n\n"
            "⚠️ فایل‌های اصلی که قبلاً در تلگرام آپلود شده‌اند "
            "حذف نمی‌شوند."
        )

        return True

    # --------------------------------------------------------
    # DELETE EPISODE
    # --------------------------------------------------------

    if text.startswith("/delete_episode"):
        parts = text.split(
            maxsplit=1
        )

        if len(parts) != 2:
            send_message(
                chat_id,
                "فرمت درست:\n"
                "/delete_episode EPISODE_KEY"
            )
            return True

        episode_key = parts[1].strip()

        delete_episode_from_db(
            episode_key
        )

        send_message(
            chat_id,
            f"✅ اطلاعات {episode_key} حذف شد."
        )

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
    return jsonify({
        "ok": True,
        "bot": "telegram"
    })


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(
        silent=True
    ) or {}

    print(
        "UPDATE:",
        update
    )

    # --------------------------------------------------------
    # CHANNEL POST
    # --------------------------------------------------------

    channel_post = update.get(
        "channel_post"
    )

    if channel_post:
        chat = channel_post.get(
            "chat",
            {}
        )

        chat_username = chat.get(
            "username"
        )

        if (
            chat_username
            and chat_username.lower()
            == CHANNEL_ID.replace(
                "@",
                ""
            ).lower()
        ):
            message_id = channel_post.get(
                "message_id"
            )

            if message_id:
                save_channel_post(
                    message_id
                )

        return jsonify({
            "ok": True
        })

    # --------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------

    message = update.get(
        "message"
    )

    if message:
        chat = message.get(
            "chat",
            {}
        )

        chat_id = chat.get(
            "id"
        )

        sender = message.get(
            "from",
            {}
        )

        user_id = sender.get(
            "id"
        )

        text = message.get(
            "text",
            ""
        )

        # ----------------------------------------------------
        # ADMIN UPLOAD
        # ----------------------------------------------------

        if user_id == ADMIN_ID:

            if (
                message.get("video")
                or message.get("document")
            ):
                handle_admin_file(
                    message
                )

                return jsonify({
                    "ok": True
                })

            if (
                text
                and handle_admin_command(
                    chat_id,
                    user_id,
                    text
                )
            ):
                return jsonify({
                    "ok": True
                })

        # ----------------------------------------------------
        # USER START
        # ----------------------------------------------------

        if text.startswith(
            "/start"
        ):
            parts = text.split(
                maxsplit=1
            )

            if len(parts) == 1:

                if user_id == ADMIN_ID:
                    handle_admin_command(
                        chat_id,
                        user_id,
                        "/start"
                    )

                else:
                    send_message(
                        chat_id,
                        "سلام 👋\n"
                        "لینک قسمت موردنظرت رو باز کن "
                        "تا فایل برات ارسال بشه."
                    )

                return jsonify({
                    "ok": True
                })

            episode_key = parts[1].strip()

            # ------------------------------------------------
            # TYPE LINK
            # ------------------------------------------------

            if episode_key.startswith(
                "t_"
            ):
                real_key, file_type = (
                    find_type_target(
                        episode_key
                    )
                )

                if not real_key:
                    send_message(
                        chat_id,
                        "❌ لینک نوع فایل پیدا نشد "
                        "یا حذف شده."
                    )

                    return jsonify({
                        "ok": True
                    })

                set_pending(
                    user_id,
                    "TYPE::"
                    + real_key
                    + "::"
                    + file_type
                )

                show_join_page(
                    chat_id,
                    user_id
                )

                return jsonify({
                    "ok": True
                })

            # ------------------------------------------------
            # NORMAL / PREVIEW LINK
            # ------------------------------------------------

            episode = get_episode(
                episode_key
            )

            if not episode:
                send_message(
                    chat_id,
                    "❌ این قسمت پیدا نشد "
                    "یا حذف شده."
                )

                return jsonify({
                    "ok": True
                })

            set_pending(
                user_id,
                episode_key
            )

            show_join_page(
                chat_id,
                user_id
            )

            return jsonify({
                "ok": True
            })

    # --------------------------------------------------------
    # CALLBACK QUERY
    # --------------------------------------------------------

    callback = update.get(
        "callback_query"
    )

    if callback:

        callback_id = callback.get(
            "id"
        )

        data = callback.get(
            "data",
            ""
        )

        from_user = callback.get(
            "from",
            {}
        )

        user_id = from_user.get(
            "id"
        )

        callback_message = (
            callback.get("message")
            or {}
        )

        chat = (
            callback_message.get("chat")
            or {}
        )

        chat_id = chat.get(
            "id"
        )

        message_id = (
            callback_message.get(
                "message_id"
            )
        )

        # ----------------------------------------------------
        # CHECK JOIN
        # ----------------------------------------------------

        if data == "check_join":

            answer_callback(
                callback_id,
                "در حال بررسی عضویت..."
            )

            episode_key = get_pending(
                user_id
            )

            if not episode_key:

                if message_id:
                    edit_message(
                        chat_id,
                        message_id,
                        "❌ لینک قسمت پیدا نشد."
                    )

                return jsonify({
                    "ok": True
                })

            if not is_main_channel_member(
                user_id
            ):

                send_message(
                    chat_id,
                    "هنوز عضو کانال اصلی نیستی 👇",
                    main_channel_keyboard()
                )

                return jsonify({
                    "ok": True
                })

            missing = check_all_sponsors(
                user_id
            )

            if missing:

                send_message(
                    chat_id,
                    "هنوز عضویت بعضی کانال‌ها "
                    "تأیید نشده 👇",
                    sponsor_keyboard(
                        missing
                    )
                )

                return jsonify({
                    "ok": True
                })

            if message_id:
                delete_message(
                    chat_id,
                    message_id
                )

            show_reaction_page(
                chat_id,
                user_id
            )

            return jsonify({
                "ok": True
            })

        # ----------------------------------------------------
        # CHECK REACTIONS
        # ----------------------------------------------------

        if data == "check_reactions":

            answer_callback(
                callback_id,
                "در حال بررسی ری‌اکشن‌ها..."
            )

            episode_key = get_pending(
                user_id
            )

            if not episode_key:

                send_message(
                    chat_id,
                    "❌ لینک قسمت پیدا نشد."
                )

                return jsonify({
                    "ok": True
                })

            ok, count = (
                user_reacted_to_last_posts(
                    user_id,
                    5
                )
            )

            if not ok:

                send_message(
                    chat_id,
                    "هنوز ری‌اکشن ۵ پست آخر "
                    "کامل نشده ❤️\n"
                    f"تعداد پست‌هایی که ربات "
                    f"بررسی می‌کند: {count}"
                )

                return jsonify({
                    "ok": True
                })

            if message_id:
                delete_message(
                    chat_id,
                    message_id
                )

            send_episode_to_user(
                chat_id,
                user_id
            )

            return jsonify({
                "ok": True
            })

        return jsonify({
            "ok": True
        })

    # --------------------------------------------------------
    # MESSAGE REACTION
    # --------------------------------------------------------

    reaction = update.get(
        "message_reaction"
    )

    if reaction:

        user = (
            reaction.get("user")
            or {}
        )

        user_id = user.get(
            "id"
        )

        message_id = reaction.get(
            "message_id"
        )

        if user_id and message_id:

            new_reaction = (
                reaction.get(
                    "new_reaction"
                )
                or []
            )

            reacted = any(
                item.get("type") == "emoji"
                and item.get("emoji") == "❤️"
                for item in new_reaction
            )

            save_reaction(
                user_id,
                message_id,
                reacted
            )

        return jsonify({
            "ok": True
        })

    return jsonify({
        "ok": True
    })


# ============================================================
# SET WEBHOOK
# ============================================================

def setup_webhook():

    render_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        "https://telegram-aeries-bot.onrender.com"
    ).rstrip("/")

    webhook_url = (
        render_url
        + "/webhook"
    )

    result = tg(
        "setWebhook",
        {
            "url": webhook_url,
            "allowed_updates": [
                "message",
                "callback_query",
                "channel_post",
                "message_reaction"
            ],
            "drop_pending_updates": False
        }
    )

    print(
        "Webhook:",
        webhook_url
    )

    print(
        "Webhook result:",
        result
    )


# ============================================================
# START
# ============================================================

setup_webhook()


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
