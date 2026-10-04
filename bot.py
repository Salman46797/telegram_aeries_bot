import os
import re
import time
import hashlib
import threading
from datetime import datetime, timezone, timedelta
from collections import Counter
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

# هر 90 ثانیه Cache از Supabase به‌روزرسانی می‌شود.
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
# HTTP / EXECUTORS
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

EXEC = ThreadPoolExecutor(
    max_workers=16
)

MEDIA_EXEC = ThreadPoolExecutor(
    max_workers=4
)


# ============================================================
# CACHE
# ============================================================

CACHE_LOCK = threading.RLock()

EPISODES = {}
TYPE_INDEX = {}
SPONSORS = []

# Pending فقط در RAM نگه داشته می‌شود تا مسیر دانلود
# در حالت عادی هیچ درخواست اضافه‌ای به Supabase نداشته باشد.
PENDING = {}

BOT_USERNAME = ""
CACHE_READY = False


# ============================================================
# DELIVERY LOCK
# ============================================================

DELIVERY_LOCK = threading.RLock()

DELIVERING = set()


# ============================================================
# ADMIN STATE
# ============================================================

ADMIN_STATE = {}

BROADCAST_LOCK = threading.RLock()
BROADCAST_RUNNING = False


# ============================================================
# TELEGRAM API
# ============================================================

def tg(method, data=None, timeout=30):
    try:
        response = HTTP.post(
            f"{TG_API}/{method}",
            json=data or {},
            timeout=(4, timeout)
        )

        return response.json()

    except Exception as e:
        print("Telegram error:", method, e)

        return {
            "ok": False
        }


def send_message(
    chat_id,
    text,
    reply_markup=None
):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    return tg(
        "sendMessage",
        data
    )


def edit_message(
    chat_id,
    message_id,
    text,
    reply_markup=None
):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    return tg(
        "editMessageText",
        data
    )


def delete_message(
    chat_id,
    message_id
):
    return tg(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


def answer_callback(
    callback_id,
    text=None,
    show_alert=False
):
    data = {
        "callback_query_id": callback_id,
        "show_alert": show_alert
    }

    if text:
        data["text"] = text

    return tg(
        "answerCallbackQuery",
        data
    )


def get_chat_member(
    chat_id,
    user_id
):
    return tg(
        "getChatMember",
        {
            "chat_id": chat_id,
            "user_id": user_id
        }
    )


def get_me():
    return tg(
        "getMe"
    )


# ============================================================
# MEDIA SEND
# ============================================================

def send_video(
    chat_id,
    file_id,
    caption=None,
    caption_entities=None
):
    data = {
        "chat_id": chat_id,
        "video": file_id,
        "supports_streaming": True
    }

    if caption:
        data["caption"] = caption

    if caption_entities:
        data["caption_entities"] = caption_entities

    return tg(
        "sendVideo",
        data
    )


def send_document(
    chat_id,
    file_id,
    caption=None,
    caption_entities=None
):
    data = {
        "chat_id": chat_id,
        "document": file_id
    }

    if caption:
        data["caption"] = caption

    if caption_entities:
        data["caption_entities"] = caption_entities

    return tg(
        "sendDocument",
        data
    )


def send_file(
    chat_id,
    file_info
):
    if not file_info.get("file_id"):
        return {
            "ok": False
        }

    caption = file_info.get(
        "caption",
        ""
    )

    caption_entities = file_info.get(
        "caption_entities"
    ) or []

    if file_info.get("type") == "document":

        return send_document(
            chat_id,
            file_info["file_id"],
            caption,
            caption_entities
        )

    return send_video(
        chat_id,
        file_info["file_id"],
        caption,
        caption_entities
    )


# ============================================================
# COPY MESSAGE - BROADCAST
# ============================================================

def copy_message(
    target_chat_id,
    from_chat_id,
    message_id
):
    return tg(
        "copyMessage",
        {
            "chat_id": target_chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id
        },
        timeout=30
    )


# ============================================================
# EPISODE HELPERS
# ============================================================

def series_key(name):
    value = name.strip().lower()

    return (
        "s"
        + hashlib.sha1(
            value.encode("utf-8")
        ).hexdigest()[:10]
    )


def episode_key(
    name,
    number
):
    return (
        f"ep_{series_key(name)}_{int(number)}"
    )


def preview_key(
    name,
    number
):
    return (
        "preview__"
        + episode_key(name, number)
    )


def type_code(
    ep_key,
    file_type
):
    raw = f"{ep_key}|{file_type}"

    return (
        "t_"
        + hashlib.sha1(
            raw.encode("utf-8")
        ).hexdigest()[:12]
    )


def normalize_type(caption):
    text = (
        caption or ""
    ).replace(
        "ي",
        "ی"
    ).replace(
        "ك",
        "ک"
    )

    if (
        "زبان اصلی" in text
        or "زبان‌اصلی" in text
    ):
        return "زبان اصلی"

    if (
        "زیرنویس فوری" in text
        or "زیرنویس‌فوری" in text
    ):
        return "زیرنویس فوری"

    if (
        "زیرنویس مووی باز" in text
        or "زیرنویس‌مووی‌ باز" in text
        or "زیرنویس مووی‌باز" in text
    ):
        return "زیرنویس مووی باز"

    return "سایر"


def enrich_files(
    files,
    ep_key
):
    result = []

    for raw in files or []:

        file_info = dict(raw)

        file_type = (
            file_info.get("file_type")
            or normalize_type(
                file_info.get(
                    "caption",
                    ""
                )
            )
        )

        file_info["file_type"] = file_type

        file_info["type_code"] = (
            file_info.get("type_code")
            or type_code(
                ep_key,
                file_type
            )
        )

        # برای سازگاری با فایل‌های قدیمی
        if "caption_entities" not in file_info:
            file_info["caption_entities"] = []

        result.append(
            file_info
        )

    return result


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

    name = series_match.group(1).strip()

    number = int(
        episode_match.group(1)
    )

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
            re.IGNORECASE
        )
    )


# ============================================================
# CACHE / SUPABASE
# ============================================================

def cache_episode(row):
    if not row:
        return

    key = row.get("episode_key")

    if not key:
        return

    row = dict(row)

    row["files"] = enrich_files(
        row.get("files") or [],
        key
    )

    EPISODES[key] = row

    for file_info in row["files"]:

        code = file_info.get(
            "type_code"
        )

        if code:

            TYPE_INDEX[code] = (
                key,
                file_info.get(
                    "file_type"
                )
            )


def sync_cache():
    global SPONSORS
    global BOT_USERNAME
    global CACHE_READY

    try:

        episodes_result = (
            supabase
            .table("episodes")
            .select("*")
            .execute()
        )

        sponsors_result = (
            supabase
            .table("sponsors")
            .select("*")
            .order("id")
            .execute()
        )

        local_episodes = {}
        local_types = {}

        for row in episodes_result.data or []:

            if not row.get(
                "episode_key"
            ):
                continue

            row = dict(row)

            row["files"] = enrich_files(
                row.get("files") or [],
                row["episode_key"]
            )

            local_episodes[
                row["episode_key"]
            ] = row

            for file_info in row["files"]:

                code = file_info.get(
                    "type_code"
                )

                if code:

                    local_types[code] = (
                        row["episode_key"],
                        file_info.get(
                            "file_type"
                        )
                    )

        if not BOT_USERNAME:

            me = get_me()

            BOT_USERNAME = (
                me.get("result") or {}
            ).get(
                "username",
                ""
            )

        with CACHE_LOCK:

            EPISODES.clear()
            EPISODES.update(
                local_episodes
            )

            TYPE_INDEX.clear()
            TYPE_INDEX.update(
                local_types
            )

            SPONSORS = list(
                sponsors_result.data or []
            )

            CACHE_READY = True

        print(
            "CACHE OK:",
            len(EPISODES),
            "episodes /",
            len(TYPE_INDEX),
            "types /",
            len(SPONSORS),
            "sponsors"
        )

        return True

    except Exception as e:

        print(
            "CACHE ERROR:",
            e
        )

        return False


def cache_loop():

    while True:

        time.sleep(
            CACHE_SYNC_INTERVAL
        )

        sync_cache()


def get_episode(key):

    with CACHE_LOCK:

        row = EPISODES.get(
            key
        )

        if row is not None:
            return row

    # فقط در صورت نبودن در Cache
    # یک درخواست مستقیم زده می‌شود.

    try:

        result = (
            supabase
            .table("episodes")
            .select("*")
            .eq(
                "episode_key",
                key
            )
            .limit(1)
            .execute()
        )

        row = (
            result.data[0]
            if result.data
            else None
        )

        if row:

            with CACHE_LOCK:
                cache_episode(row)

        return row

    except Exception as e:

        print(
            "get_episode error:",
            e
        )

        return None


def save_episode(
    key,
    name,
    number,
    files
):

    files = enrich_files(
        files,
        key
    )

    payload = {
        "episode_key": key,
        "series_name": name,
        "episode_number": number,
        "files": files
    }

    try:

        result = (
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

        return result

    except Exception as e:

        print(
            "save_episode error:",
            e
        )

        return None


def delete_episode(key):

    try:

        result = (
            supabase
            .table("episodes")
            .delete()
            .eq(
                "episode_key",
                key
            )
            .execute()
        )

        with CACHE_LOCK:

            EPISODES.pop(
                key,
                None
            )

            for code, value in list(
                TYPE_INDEX.items()
            ):

                if value[0] == key:

                    TYPE_INDEX.pop(
                        code,
                        None
                    )

        return result

    except Exception as e:

        print(
            "delete_episode error:",
            e
        )

        return None


def delete_all_episodes():

    try:

        result = (
            supabase
            .table("episodes")
            .delete()
            .neq(
                "episode_key",
                ""
            )
            .execute()
        )

        with CACHE_LOCK:

            EPISODES.clear()
            TYPE_INDEX.clear()

        return result

    except Exception as e:

        print(
            "delete_all error:",
            e
        )

        return None


# ============================================================
# PENDING
# ============================================================

def set_pending(
    user_id,
    value
):
    PENDING[
        int(user_id)
    ] = value


def get_pending(user_id):

    return PENDING.get(
        int(user_id)
    )


def clear_pending(user_id):

    PENDING.pop(
        int(user_id),
        None
    )


# ============================================================
# SPONSORS
# ============================================================

def get_sponsors():

    with CACHE_LOCK:
        return list(
            SPONSORS
        )


def add_sponsor(
    chat_id,
    title,
    url
):

    global SPONSORS

    try:

        result = (
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

        if result.data:

            with CACHE_LOCK:

                SPONSORS.append(
                    result.data[0]
                )

        return result

    except Exception as e:

        print(
            "add sponsor error:",
            e
        )

        return None


def remove_sponsor(
    sponsor_id
):

    global SPONSORS

    try:

        result = (
            supabase
            .table("sponsors")
            .delete()
            .eq(
                "id",
                int(sponsor_id)
            )
            .execute()
        )

        with CACHE_LOCK:

            SPONSORS = [
                s
                for s in SPONSORS
                if int(
                    s.get(
                        "id",
                        -1
                    )
                ) != int(sponsor_id)
            ]

        return result

    except Exception as e:

        print(
            "remove sponsor error:",
            e
        )

        return None


# ============================================================
# USERS
# ============================================================

def register_user(user):

    if not user:
        return

    try:

        user_id = int(
            user.get("id")
        )

        payload = {
            "user_id": user_id,
            "first_name": user.get(
                "first_name",
                ""
            ) or "",
            "last_name": user.get(
                "last_name",
                ""
            ) or "",
            "username": user.get(
                "username",
                ""
            ) or "",
            "is_blocked": False,
            "last_seen": datetime.now(
                timezone.utc
            ).isoformat()
        }

        (
            supabase
            .table("bot_users")
            .upsert(
                payload,
                on_conflict="user_id"
            )
            .execute()
        )

    except Exception as e:

        print(
            "register user error:",
            e
        )


def mark_user_blocked(
    user_id
):

    try:

        (
            supabase
            .table("bot_users")
            .update(
                {
                    "is_blocked": True
                }
            )
            .eq(
                "user_id",
                int(user_id)
            )
            .execute()
        )

    except Exception as e:

        print(
            "mark blocked error:",
            e
        )


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
                    "user_id": int(
                        user_id
                    ),
                    "event_type": event_type,
                    "episode_key": episode_key,
                    "file_type": file_type,
                    "file_count": int(
                        file_count or 0
                    )
                }
            )
            .execute()
        )

    except Exception as e:

        print(
            "stats error:",
            e
        )


def utc_day_start():

    now = datetime.now(
        timezone.utc
    )

    return now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )


def utc_month_start():

    now = datetime.now(
        timezone.utc
    )

    return now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )


def count_stats(
    event_type=None,
    start=None,
    end=None
):

    try:

        query = (
            supabase
            .table("bot_stats")
            .select(
                "user_id",
                count="exact",
                head=True
            )
        )

        if event_type:
            query = query.eq(
                "event_type",
                event_type
            )

        if start:
            query = query.gte(
                "created_at",
                start.isoformat()
            )

        if end:
            query = query.lt(
                "created_at",
                end.isoformat()
            )

        result = query.execute()

        return int(
            result.count or 0
        )

    except Exception as e:

        print(
            "count stats error:",
            e
        )

        return 0


def count_users(
    start=None,
    end=None
):

    try:

        query = (
            supabase
            .table("bot_users")
            .select(
                "user_id",
                count="exact",
                head=True
            )
        )

        if start:
            query = query.gte(
                "created_at",
                start.isoformat()
            )

        if end:
            query = query.lt(
                "created_at",
                end.isoformat()
            )

        result = query.execute()

        return int(
            result.count or 0
        )

    except Exception as e:

        print(
            "count users error:",
            e
        )

        return 0


def advanced_stats_text():

    now = datetime.now(
        timezone.utc
    )

    today = utc_day_start()

    month = utc_month_start()

    tomorrow = today + timedelta(
        days=1
    )

    next_month = (
        month.replace(
            year=month.year + 1,
            month=1
        )
        if month.month == 12
        else
        month.replace(
            month=month.month + 1
        )
    )

    total_users = count_users()

    new_today = count_users(
        today,
        tomorrow
    )

    new_month = count_users(
        month,
        next_month
    )

    starts_today = count_stats(
        "start",
        today,
        tomorrow
    )

    starts_month = count_stats(
        "start",
        month,
        next_month
    )

    downloads_total = count_stats(
        "download"
    )

    downloads_today = count_stats(
        "download",
        today,
        tomorrow
    )

    downloads_month = count_stats(
        "download",
        month,
        next_month
    )

    broadcasts = count_stats(
        "broadcast_success"
    )

    failed_broadcasts = count_stats(
        "broadcast_fail"
    )

    # ========================================================
    # محبوب‌ترین قسمت‌ها
    # ========================================================

    top_lines = []

    try:

        result = (
            supabase
            .table("bot_stats")
            .select(
                "episode_key,event_type"
            )
            .eq(
                "event_type",
                "download"
            )
            .limit(10000)
            .execute()
        )

        counter = Counter()

        for row in result.data or []:

            key = row.get(
                "episode_key"
            )

            if key:
                counter[key] += 1

        for key, count in counter.most_common(5):

            episode = get_episode(
                key
            )

            if episode:

                name = episode.get(
                    "series_name",
                    "نامشخص"
                )

                number = episode.get(
                    "episode_number",
                    "?"
                )

                top_lines.append(
                    f"• {name} — قسمت {number}: "
                    f"{count} دانلود"
                )

    except Exception as e:

        print(
            "top stats error:",
            e
        )

    text = (
        "📊 آمار پیشرفته ربات\n\n"

        f"👥 کل کاربران: {total_users}\n"
        f"🆕 کاربران جدید امروز: {new_today}\n"
        f"📅 کاربران جدید این ماه: {new_month}\n\n"

        f"▶️ /start امروز: {starts_today}\n"
        f"▶️ /start این ماه: {starts_month}\n\n"

        f"📥 کل دانلودها: {downloads_total}\n"
        f"📥 دانلود امروز: {downloads_today}\n"
        f"📥 دانلود این ماه: {downloads_month}\n\n"

        f"📢 ارسال موفق Broadcast: {broadcasts}\n"
        f"❌ خطای Broadcast: {failed_broadcasts}\n\n"

        "🔥 محبوب‌ترین قسمت‌ها:\n"
    )

    if top_lines:

        text += "\n".join(
            top_lines
        )

    else:

        text += "هنوز آماری ثبت نشده."

    return text


# ============================================================
# MEMBERSHIP
# ============================================================

def member_ok(
    chat_id,
    user_id
):

    result = get_chat_member(
        chat_id,
        user_id
    )

    if not result.get("ok"):
        return False

    member = (
        result.get("result")
        or {}
    )

    status = member.get(
        "status",
        ""
    )

    if status in (
        "creator",
        "administrator",
        "member"
    ):
        return True

    if status == "restricted":

        return bool(
            member.get(
                "is_member"
            )
        )

    return False


def check_membership(
    user_id
):

    sponsor_list = get_sponsors()

    targets = [
        (
            CHANNEL_ID,
            None
        )
    ]

    for sponsor in sponsor_list:

        chat_id = sponsor.get(
            "chat_id"
        )

        if chat_id:

            targets.append(
                (
                    chat_id,
                    sponsor
                )
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

    for future in futures:

        try:

            results.append(
                bool(
                    future.result()
                )
            )

        except Exception:

            results.append(
                False
            )

    main_ok = (
        bool(results)
        and results[0]
    )

    missing = []

    for i in range(
        1,
        len(targets)
    ):

        if (
            not results[i]
            and targets[i][1]
        ):

            missing.append(
                targets[i][1]
            )

    return (
        main_ok,
        missing
    )


# ============================================================
# USER KEYBOARDS
# ============================================================

def join_keyboard(
    missing=None
):

    rows = []

    if missing is None:

        rows.append(
            [
                {
                    "text": "عضویت در کانال 📺",
                    "url": CHANNEL_URL
                }
            ]
        )

        for sponsor in get_sponsors():

            if sponsor.get("url"):

                rows.append(
                    [
                        {
                            "text":
                                f"عضویت در "
                                f"{sponsor.get('title') or 'کانال اسپانسر'}",
                            "url":
                                sponsor["url"]
                        }
                    ]
                )

    else:

        for sponsor in missing:

            if sponsor.get("url"):

                rows.append(
                    [
                        {
                            "text":
                                f"عضویت در "
                                f"{sponsor.get('title') or 'کانال اسپانسر'}",
                            "url":
                                sponsor["url"]
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


def redownload_keyboard(
    target
):

    global BOT_USERNAME

    if not BOT_USERNAME:

        BOT_USERNAME = (
            get_me()
            .get("result") or {}
        ).get(
            "username",
            ""
        )

    if not BOT_USERNAME:
        return None

    return {
        "inline_keyboard": [
            [
                {
                    "text": "دانلود مجدد ♻️",
                    "url":
                        f"https://t.me/"
                        f"{BOT_USERNAME}"
                        f"?start={target}"
                }
            ]
        ]
    }


# ============================================================
# ADMIN INLINE PANEL
# ============================================================

def admin_panel_keyboard():

    return {
        "inline_keyboard": [

            [
                {
                    "text": "🎬 مدیریت قسمت‌ها",
                    "callback_data": "admin:episodes"
                },
                {
                    "text": "📢 اسپانسرها",
                    "callback_data": "admin:sponsors"
                }
            ],

            [
                {
                    "text": "📢 پیام همگانی",
                    "callback_data": "admin:broadcast"
                },
                {
                    "text": "📊 آمار پیشرفته",
                    "callback_data": "admin:stats"
                }
            ],

            [
                {
                    "text": "📋 لیست قسمت‌ها",
                    "callback_data": "admin:list"
                },
                {
                    "text": "🗑 حذف قسمت",
                    "callback_data": "admin:delete"
                }
            ],

            [
                {
                    "text": "🗑 حذف همه قسمت‌ها",
                    "callback_data": "admin:delete_all"
                }
            ],

            [
                {
                    "text": "🔄 سینک دیتابیس",
                    "callback_data": "admin:sync"
                },
                {
                    "text": "⚡ وضعیت ربات",
                    "callback_data": "admin:status"
                }
            ]

        ]
    }


def admin_back_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "⬅️ برگشت به پنل",
                    "callback_data": "admin:panel"
                }
            ]
        ]
    }


def sponsor_keyboard():

    return {
        "inline_keyboard": [

            [
                {
                    "text": "➕ افزودن اسپانسر",
                    "callback_data": "admin:add_sponsor"
                },
                {
                    "text": "➖ حذف اسپانسر",
                    "callback_data": "admin:remove_sponsor"
                }
            ],

            [
                {
                    "text": "📋 لیست اسپانسرها",
                    "callback_data": "admin:sponsors_list"
                }
            ],

            [
                {
                    "text": "⬅️ برگشت",
                    "callback_data": "admin:panel"
                }
            ]

        ]
    }


def send_admin_panel(
    chat_id
):

    send_message(
        chat_id,

        "⚙️ پنل مدیریت ربات\n\n"
        "قابلیت موردنظر را انتخاب کن:",

        admin_panel_keyboard()
    )


# ============================================================
# USER FLOW
# ============================================================

def send_reaction_page(
    chat_id
):

    send_message(
        chat_id,

        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر "
        "کانال @altiustuistsnbol را ری‌اکت بزنید "
        "و سپس برگردید و دکمه انجام شد را بزنید ♥️",

        reaction_keyboard()
    )


def send_join_page(
    chat_id,
    user_id
):

    main_ok, missing = check_membership(
        user_id
    )

    if main_ok and not missing:

        send_reaction_page(
            chat_id
        )

        return "reaction"

    if not main_ok:

        send_message(
            chat_id,

            "📣 برای استفاده از ربات و دریافت فایل:\n\n"
            "1️⃣ ابتدا عضو کانال اصلی بشو\n"
            "2️⃣ سپس روی «عضو شدم» بزن",

            join_keyboard(None)
        )

        return "join"

    send_message(
        chat_id,

        "📣 هنوز عضویت بعضی کانال‌ها کامل نیست:\n\n"
        "1️⃣ در کانال‌های زیر عضو شو\n"
        "2️⃣ سپس روی «عضو شدم» بزن",

        join_keyboard(missing)
    )

    return "join"


# ============================================================
# DELIVERY
# ============================================================

def claim_delivery(
    user_id
):

    user_id = int(
        user_id
    )

    with DELIVERY_LOCK:

        if user_id in DELIVERING:

            return False

        DELIVERING.add(
            user_id
        )

        return True


def release_delivery(
    user_id
):

    with DELIVERY_LOCK:

        DELIVERING.discard(
            int(user_id)
        )


def delete_files_later(
    chat_id,
    message_ids
):

    time.sleep(
        DELETE_AFTER
    )

    for message_id in message_ids:

        try:

            delete_message(
                chat_id,
                message_id
            )

        except Exception:
            pass


def deliver_episode(
    chat_id,
    user_id
):

    if not claim_delivery(
        user_id
    ):

        send_message(
            chat_id,
            "⏳ فایل در حال ارسال است..."
        )

        return

    try:

        pending = get_pending(
            user_id
        )

        if not pending:

            send_message(
                chat_id,
                "❌ لینک قسمت پیدا نشد. "
                "دوباره لینک قسمت را باز کن."
            )

            return

        selected_type = None
        real_key = pending

        if pending.startswith(
            "TYPE::"
        ):

            parts = pending.split(
                "::",
                2
            )

            if len(parts) != 3:

                send_message(
                    chat_id,
                    "❌ لینک فایل نامعتبر است."
                )

                return

            real_key = parts[1]
            selected_type = parts[2]

        episode = get_episode(
            real_key
        )

        if not episode:

            clear_pending(
                user_id
            )

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
                f
                for f in files
                if f.get(
                    "file_type"
                ) == selected_type
            ]

        if not files:

            send_message(
                chat_id,
                "❌ فایل این قسمت پیدا نشد."
            )

            return

        if selected_type:

            redownload_target = type_code(
                real_key,
                selected_type
            )

        else:

            redownload_target = real_key

        clear_pending(
            user_id
        )

        sent_message_ids = []

        # عمداً ترتیبی ارسال می‌شود
        # تا هشدار بالای فایل‌ها قرار نگیرد.

        for file_info in files:

            result = send_file(
                chat_id,
                file_info
            )

            if not result.get(
                "ok"
            ):
                continue

            message_id = (
                result.get(
                    "result"
                ) or {}
            ).get(
                "message_id"
            )

            if message_id:

                sent_message_ids.append(
                    message_id
                )

        if not sent_message_ids:

            send_message(
                chat_id,
                "❌ ارسال فایل انجام نشد. "
                "دوباره امتحان کن."
            )

            return

        warning_text = (
            "⚠️ توجه\n"
            f"فایل‌های ارسالی بعد از "
            f"{DELETE_AFTER} ثانیه حذف می‌شوند.\n"
            "قبل از تمام شدن زمان، فایل‌ها را ذخیره کن."
        )

        markup = redownload_keyboard(
            redownload_target
        )

        send_message(
            chat_id,
            warning_text,
            markup
        )

        EXEC.submit(
            record_stat,
            user_id,
            "download",
            real_key,
            selected_type,
            len(sent_message_ids)
        )

        threading.Thread(
            target=delete_files_later,
            args=(
                chat_id,
                sent_message_ids
            ),
            daemon=True
        ).start()

    finally:

        release_delivery(
            user_id
        )


# ============================================================
# ADMIN FILE HANDLING
# ============================================================

def extract_file(
    message
):

    caption = message.get(
        "caption",
        ""
    )

    caption_entities = (
        message.get(
            "caption_entities"
        )
        or []
    )

    if message.get("video"):

        video = message["video"]

        return {
            "type": "video",
            "file_id":
                video.get("file_id"),
            "caption": caption,
            "caption_entities":
                caption_entities
        }

    if message.get("document"):

        document = message["document"]

        return {
            "type": "document",
            "file_id":
                document.get("file_id"),
            "caption": caption,
            "caption_entities":
                caption_entities
        }

    return None


def handle_admin_file(
    message
):

    file_info = extract_file(
        message
    )

    if not file_info:
        return

    caption = file_info.get(
        "caption",
        ""
    )

    parsed = parse_caption(
        caption
    )

    if not parsed:

        send_message(
            ADMIN_ID,

            "❌ کپشن قابل تشخیص نیست.\n\n"
            "نمونه:\n"
            "🪴 سریال «اسم سریال»\n"
            "🪷 قسمت : 1\n"
            "🫧 زبان اصلی\n"
            "🎍 کیفیت : 1080",

            admin_back_keyboard()
        )

        return

    preview = is_preview(
        caption
    )

    if preview:

        key = preview_key(
            parsed["series_name"],
            parsed["episode_number"]
        )

    else:

        key = parsed[
            "episode_key"
        ]

    old_episode = get_episode(
        key
    )

    if old_episode:

        files = list(
            old_episode.get(
                "files"
            ) or []
        )

    else:

        files = []

    file_info["file_type"] = normalize_type(
        caption
    )

    file_info["type_code"] = type_code(
        key,
        file_info["file_type"]
    )

    files.append(
        file_info
    )

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
            get_me()
            .get("result") or {}
        ).get(
            "username",
            ""
        )

    if not BOT_USERNAME:

        send_message(
            ADMIN_ID,
            "❌ نام کاربری ربات پیدا نشد."
        )

        return

    base_link = (
        f"https://t.me/{BOT_USERNAME}"
    )

    groups = {}

    for f in enrich_files(
        files,
        key
    ):

        groups.setdefault(
            f["file_type"],
            f["type_code"]
        )

    label = (
        "پیش‌نمایش"
        if preview
        else
        "قسمت"
    )

    lines = [
        f"✅ {label} ذخیره شد.",
        "",
        f"🪴 سریال: {parsed['series_name']}",
        f"🪷 قسمت: {parsed['episode_number']}",
        ""
    ]

    for file_type, code in groups.items():

        lines.append(
            f"🎬 {file_type}"
        )

        lines.append(
            f"{base_link}?start={code}"
        )

        lines.append("")

    lines.append(
        f"🔗 لینک مستقیم کل {label}: "
        f"{base_link}?start={key}"
    )

    send_message(
        ADMIN_ID,
        "\n".join(lines),
        admin_back_keyboard()
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
# ADMIN LIST
# ============================================================

def send_episode_list(
    chat_id
):

    with CACHE_LOCK:

        rows = [
            row
            for row in EPISODES.values()
            if not str(
                row.get(
                    "episode_key",
                    ""
                )
            ).startswith(
                "preview__"
            )
        ]

    rows.sort(
        key=lambda row: (
            str(
                row.get(
                    "series_name",
                    ""
                )
            ),
            int(
                row.get(
                    "episode_number",
                    0
                )
            )
        )
    )

    if not rows:

        send_message(
            chat_id,
            "📋 هیچ قسمتی ذخیره نشده.",
            admin_back_keyboard()
        )

        return

    lines = [
        "📋 قسمت‌های ذخیره‌شده:"
    ]

    for row in rows:

        lines.append(
            f"\n{row.get('series_name')}"
            f" — قسمت "
            f"{row.get('episode_number')}\n"
            f"{row.get('episode_key')}"
        )

    text = "\n".join(
        lines
    )

    if len(text) > 3900:

        text = (
            text[:3900]
            + "\n\n..."
        )

    send_message(
        chat_id,
        text,
        admin_back_keyboard()
    )


# ============================================================
# ADMIN SPONSORS
# ============================================================

def sponsors_text():

    sponsor_list = get_sponsors()

    if not sponsor_list:

        return "📢 هیچ اسپانسری ثبت نشده."

    lines = [
        "📢 اسپانسرهای فعال:"
    ]

    for sponsor in sponsor_list:

        lines.append(
            "\n"
            f"ID: {sponsor.get('id')}\n"
            f"کانال: {sponsor.get('chat_id')}\n"
            f"نام: {sponsor.get('title')}\n"
            f"لینک: {sponsor.get('url')}"
        )

    return "\n".join(
        lines
    )


# ============================================================
# BROADCAST
# ============================================================

def get_all_users_for_broadcast():

    users = []
    start = 0
    page_size = 1000

    while True:

        try:

            result = (
                supabase
                .table("bot_users")
                .select("user_id")
                .eq(
                    "is_blocked",
                    False
                )
                .range(
                    start,
                    start + page_size - 1
                )
                .execute()
            )

            batch = result.data or []

            if not batch:
                break

            for row in batch:

                user_id = row.get(
                    "user_id"
                )

                if user_id:
                    users.append(
                        int(user_id)
                    )

            if len(batch) < page_size:
                break

            start += page_size

        except Exception as e:

            print(
                "broadcast users error:",
                e
            )

            break

    return users


def run_broadcast(
    source_message_id
):

    global BROADCAST_RUNNING

    with BROADCAST_LOCK:

        if BROADCAST_RUNNING:
            return

        BROADCAST_RUNNING = True

    success = 0
    failed = 0

    try:

        users = get_all_users_for_broadcast()

        total = len(users)

        send_message(
            ADMIN_ID,

            "📢 ارسال همگانی شروع شد.\n"
            f"👥 تعداد کاربران: {total}"
        )

        for index, user_id in enumerate(
            users,
            start=1
        ):

            result = copy_message(
                user_id,
                ADMIN_ID,
                source_message_id
            )

            if result.get("ok"):

                success += 1

                EXEC.submit(
                    record_stat,
                    user_id,
                    "broadcast_success"
                )

            else:

                failed += 1

                description = str(
                    result.get(
                        "description",
                        ""
                    )
                ).lower()

                if (
                    "blocked" in description
                    or "deactivated" in description
                    or "chat not found" in description
                ):

                    EXEC.submit(
                        mark_user_blocked,
                        user_id
                    )

                EXEC.submit(
                    record_stat,
                    user_id,
                    "broadcast_fail"
                )

            # فاصله کوچک برای جلوگیری از فشار زیاد
            # روی Telegram API
            time.sleep(0.05)

        send_message(
            ADMIN_ID,

            "✅ پیام همگانی تمام شد.\n\n"
            f"👥 کل: {total}\n"
            f"✅ موفق: {success}\n"
            f"❌ ناموفق: {failed}"
        )

    except Exception as e:

        print(
            "broadcast error:",
            e
        )

        send_message(
            ADMIN_ID,
            f"❌ خطا در پیام همگانی:\n{e}"
        )

    finally:

        with BROADCAST_LOCK:
            BROADCAST_RUNNING = False


# ============================================================
# ADMIN COMMANDS
# ============================================================

def handle_admin_command(
    chat_id,
    text
):

    text = text.strip()

    if text == "/start":

        ADMIN_STATE.pop(
            ADMIN_ID,
            None
        )

        send_admin_panel(
            chat_id
        )

        return True

    if text in (
        "/cancel",
        "/cancel_broadcast"
    ):

        ADMIN_STATE.pop(
            ADMIN_ID,
            None
        )

        send_message(
            chat_id,
            "❌ عملیات لغو شد."
        )

        send_admin_panel(
            chat_id
        )

        return True

    if text == "/broadcast":

        ADMIN_STATE[
            ADMIN_ID
        ] = "broadcast"

        send_message(
            chat_id,

            "📢 پیام همگانی\n\n"
            "حالا پیام موردنظر را بفرست.\n"
            "می‌تواند متن، عکس، ویدیو، فایل و... باشد.\n\n"
            "برای لغو:\n"
            "/cancel_broadcast",

            admin_back_keyboard()
        )

        return True

    if text == "/sponsors":

        send_message(
            chat_id,
            sponsors_text(),
            sponsor_keyboard()
        )

        return True

    if text.startswith(
        "/add_sponsor"
    ):

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
                "/add_sponsor @channel | "
                "نام کانال | "
                "https://t.me/channel",

                admin_back_keyboard()
            )

            return True

        result = add_sponsor(
            parts[0],
            parts[1],
            parts[2]
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

    if text.startswith(
        "/remove_sponsor"
    ):

        parts = text.split()

        if (
            len(parts) != 2
            or not parts[1].isdigit()
        ):

            send_message(
                chat_id,
                "فرمت درست:\n"
                "/remove_sponsor ID"
            )

            return True

        result = remove_sponsor(
            int(parts[1])
        )

        send_message(
            chat_id,

            "✅ اسپانسر حذف شد."
            if result is not None
            else
            "❌ حذف اسپانسر انجام نشد."
        )

        return True

    if text.startswith(
        "/delete_episode"
    ):

        parts = text.split(
            maxsplit=1
        )

        if len(parts) != 2:

            send_message(
                chat_id,
                "فرمت:\n"
                "/delete_episode EPISODE_KEY"
            )

            return True

        result = delete_episode(
            parts[1].strip()
        )

        send_message(
            chat_id,

            "✅ اطلاعات قسمت حذف شد."
            if result is not None
            else
            "❌ حذف انجام نشد."
        )

        return True

    if text == "/delete_all":

        delete_all_episodes()

        send_message(
            chat_id,
            "✅ همه قسمت‌ها حذف شدند."
        )

        return True

    if ADMIN_STATE.get(
        ADMIN_ID
    ) == "delete":

        result = delete_episode(
            text
        )

        ADMIN_STATE.pop(
            ADMIN_ID,
            None
        )

        send_message(
            chat_id,

            "✅ اطلاعات قسمت حذف شد."
            if result is not None
            else
            "❌ حذف قسمت انجام نشد."
        )

        send_admin_panel(
            chat_id
        )

        return True

    if ADMIN_STATE.get(
        ADMIN_ID
    ) == "add_sponsor":

        parts = [
            x.strip()
            for x in text.split("|")
        ]

        if len(parts) != 3:

            send_message(
                chat_id,

                "فرمت اشتباه است.\n\n"
                "@channel | نام کانال | https://t.me/channel",

                admin_back_keyboard()
            )

            return True

        result = add_sponsor(
            parts[0],
            parts[1],
            parts[2]
        )

        ADMIN_STATE.pop(
            ADMIN_ID,
            None
        )

        send_message(
            chat_id,

            "✅ اسپانسر اضافه شد."
            if result is not None
            else
            "❌ ذخیره اسپانسر انجام نشد."
        )

        send_admin_panel(
            chat_id
        )

        return True

    if ADMIN_STATE.get(
        ADMIN_ID
    ) == "remove_sponsor":

        if not text.isdigit():

            send_message(
                chat_id,
                "❌ فقط ID اسپانسر را بفرست."
            )

            return True

        result = remove_sponsor(
            int(text)
        )

        ADMIN_STATE.pop(
            ADMIN_ID,
            None
        )

        send_message(
            chat_id,

            "✅ اسپانسر حذف شد."
            if result is not None
            else
            "❌ حذف اسپانسر انجام نشد."
        )

        send_admin_panel(
            chat_id
        )

        return True

    return False


# ============================================================
# ADMIN CALLBACKS
# ============================================================

def handle_admin_callback(
    callback
):

    global BROADCAST_RUNNING

    from_user = (
        callback.get("from")
        or {}
    )

    user_id = from_user.get(
        "id"
    )

    if user_id != ADMIN_ID:
        return False

    callback_id = callback.get(
        "id"
    )

    data = callback.get(
        "data",
        ""
    )

    message = (
        callback.get("message")
        or {}
    )

    chat = (
        message.get("chat")
        or {}
    )

    chat_id = chat.get(
        "id"
    )

    message_id = message.get(
        "message_id"
    )

    if not data.startswith(
        "admin:"
    ):
        return False

    answer_callback(
        callback_id
    )

    action = data.split(
        ":",
        1
    )[1]

    if action == "panel":

        ADMIN_STATE.pop(
            ADMIN_ID,
            None
        )

        edit_message(
            chat_id,
            message_id,

            "⚙️ پنل مدیریت ربات\n\n"
            "قابلیت موردنظر را انتخاب کن:",

            admin_panel_keyboard()
        )

        return True

    if action == "episodes":

        ADMIN_STATE[
            ADMIN_ID
        ] = "episode"

        edit_message(
            chat_id,
            message_id,

            "🎬 مدیریت قسمت‌ها\n\n"
            "ویدیو یا فایل قسمت را با کپشن خودش بفرست.\n\n"
            "نمونه:\n"
            "🪴 سریال «اسم سریال»\n"
            "🪷 قسمت : 1\n"
            "🫧 زبان اصلی\n"
            "🎍 کیفیت : 1080",

            admin_back_keyboard()
        )

        return True

    if action == "sponsors":

        edit_message(
            chat_id,
            message_id,

            sponsors_text(),

            sponsor_keyboard()
        )

        return True

    if action == "sponsors_list":

        edit_message(
            chat_id,
            message_id,

            sponsors_text(),

            sponsor_keyboard()
        )

        return True

    if action == "add_sponsor":

        ADMIN_STATE[
            ADMIN_ID
        ] = "add_sponsor"

        edit_message(
            chat_id,
            message_id,

            "➕ افزودن اسپانسر\n\n"
            "این فرمت را بفرست:\n\n"
            "@channel | نام کانال | https://t.me/channel",

            admin_back_keyboard()
        )

        return True

    if action == "remove_sponsor":

        ADMIN_STATE[
            ADMIN_ID
        ] = "remove_sponsor"

        edit_message(
            chat_id,
            message_id,

            "➖ حذف اسپانسر\n\n"
            "ID اسپانسر را بفرست.",

            admin_back_keyboard()
        )

        return True

    if action == "broadcast":

        with BROADCAST_LOCK:

            if BROADCAST_RUNNING:

                answer_callback(
                    callback_id,
                    "یک پیام همگانی در حال ارسال است.",
                    True
                )

                return True

        ADMIN_STATE[
            ADMIN_ID
        ] = "broadcast"

        edit_message(
            chat_id,
            message_id,

            "📢 پیام همگانی\n\n"
            "پیامی که می‌خواهی برای کاربران ارسال شود "
            "را همینجا بفرست.\n\n"
            "متن، عکس، ویدیو، فایل و... قابل ارسال است.\n\n"
            "لغو:\n"
            "/cancel_broadcast",

            admin_back_keyboard()
        )

        return True

    if action == "stats":

        text = advanced_stats_text()

        edit_message(
            chat_id,
            message_id,
            text,
            admin_back_keyboard()
        )

        return True

    if action == "list":

        # برای جلوگیری از پیام اضافه،
        # اینجا پیام جدید می‌فرستیم.
        send_episode_list(
            chat_id
        )

        return True

    if action == "delete":

        ADMIN_STATE[
            ADMIN_ID
        ] = "delete"

        edit_message(
            chat_id,
            message_id,

            "🗑 حذف قسمت\n\n"
            "کلید قسمت را بفرست.\n\n"
            "مثال:\n"
            "ep_xxxxxxxxxx_1",

            admin_back_keyboard()
        )

        return True

    if action == "delete_all":

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "❌ لغو",
                        "callback_data": "admin:panel"
                    },
                    {
                        "text": "⚠️ بله، حذف همه",
                        "callback_data": "admin:delete_all_yes"
                    }
                ]
            ]
        }

        edit_message(
            chat_id,
            message_id,

            "⚠️ مطمئنی؟\n\n"
            "تمام قسمت‌های ذخیره‌شده حذف می‌شوند.",

            keyboard
        )

        return True

    if action == "delete_all_yes":

        result = delete_all_episodes()

        edit_message(
            chat_id,
            message_id,

            "✅ همه قسمت‌ها حذف شدند."
            if result is not None
            else
            "❌ حذف همه قسمت‌ها ناموفق بود.",

            admin_panel_keyboard()
        )

        return True

    if action == "sync":

        ok = sync_cache()

        edit_message(
            chat_id,
            message_id,

            "🔄 سینک دیتابیس\n\n"
            + (
                "✅ سینک با موفقیت انجام شد."
                if ok
                else
                "❌ سینک ناموفق بود."
            ),

            admin_back_keyboard()
        )

        return True

    if action == "status":

        db_ok = False

        try:

            (
                supabase
                .table("episodes")
                .select("episode_key")
                .limit(1)
                .execute()
            )

            db_ok = True

        except Exception:
            pass

        with CACHE_LOCK:

            episode_count = len(
                EPISODES
            )

            sponsor_count = len(
                SPONSORS
            )

            type_count = len(
                TYPE_INDEX
            )

        edit_message(
            chat_id,
            message_id,

            "🟢 وضعیت ربات\n\n"
            "🤖 Telegram: 🟢\n"
            f"🗄 Supabase: "
            f"{'🟢' if db_ok else '🔴'}\n"
            f"⚡ Cache: "
            f"{'🟢' if CACHE_READY else '🔴'}\n"
            f"🎬 قسمت‌ها: {episode_count}\n"
            f"🎞 نوع فایل‌ها: {type_count}\n"
            f"📢 اسپانسرها: {sponsor_count}\n"
            f"⏱ سینک Cache: هر {CACHE_SYNC_INTERVAL} ثانیه",

            admin_back_keyboard()
        )

        return True

    return True


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return "Bot is running."


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify(
        {
            "ok": True
        }
    )


@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = (
        request
        .get_json(
            silent=True
        )
        or {}
    )

    # ========================================================
    # NORMAL MESSAGE
    # ========================================================

    message = update.get(
        "message"
    )

    if message:

        chat = (
            message.get("chat")
            or {}
        )

        chat_id = chat.get(
            "id"
        )

        sender = (
            message.get("from")
            or {}
        )

        user_id = sender.get(
            "id"
        )

        text = message.get(
            "text",
            ""
        )

        # ====================================================
        # ثبت کاربر بدون معطل کردن webhook
        # ====================================================

        if user_id:

            EXEC.submit(
                register_user,
                sender
            )

        # ====================================================
        # ADMIN
        # ====================================================

        if user_id == ADMIN_ID:

            # ------------------------------------------------
            # BROADCAST
            # ------------------------------------------------

            if ADMIN_STATE.get(
                ADMIN_ID
            ) == "broadcast":

                if text in (
                    "/cancel",
                    "/cancel_broadcast"
                ):

                    ADMIN_STATE.pop(
                        ADMIN_ID,
                        None
                    )

                    send_message(
                        chat_id,
                        "❌ پیام همگانی لغو شد."
                    )

                    send_admin_panel(
                        chat_id
                    )

                    return jsonify(
                        {"ok": True}
                    )

                ADMIN_STATE.pop(
                    ADMIN_ID,
                    None
                )

                with BROADCAST_LOCK:

                    if BROADCAST_RUNNING:

                        send_message(
                            chat_id,
                            "⏳ یک پیام همگانی "
                            "در حال ارسال است."
                        )

                        return jsonify(
                            {"ok": True}
                        )

                # پیام ادمین به‌صورت Copy به کاربران می‌رود.
                # بنابراین فرمت متن و رسانه حفظ می‌شود.

                MEDIA_EXEC.submit(
                    run_broadcast,
                    message.get(
                        "message_id"
                    )
                )

                send_message(
                    chat_id,
                    "📢 پیام دریافت شد.\n"
                    "ارسال همگانی در پس‌زمینه شروع می‌شود."
                )

                return jsonify(
                    {"ok": True}
                )

            # ------------------------------------------------
            # EPISODE FILE
            # ------------------------------------------------

            if (
                message.get("video")
                or
                message.get("document")
            ):

                handle_admin_file(
                    message
                )

                return jsonify(
                    {"ok": True}
                )

            # ------------------------------------------------
            # ADMIN TEXT
            # ------------------------------------------------

            if text:

                if handle_admin_command(
                    chat_id,
                    text
                ):

                    return jsonify(
                        {"ok": True}
                    )

        # ====================================================
        # USER /start
        # ====================================================

        if text.startswith(
            "/start"
        ):

            parts = text.split(
                maxsplit=1
            )

            # ------------------------------------------------
            # /start بدون لینک
            # ------------------------------------------------

            if len(parts) == 1:

                EXEC.submit(
                    record_stat,
                    user_id,
                    "start"
                )

                if user_id == ADMIN_ID:

                    send_admin_panel(
                        chat_id
                    )

                else:

                    send_message(
                        chat_id,

                        "سلام 👋\n"
                        "لینک قسمت موردنظرت رو باز کن."
                    )

                return jsonify(
                    {"ok": True}
                )

            # ثبت start حتی با لینک
            EXEC.submit(
                record_stat,
                user_id,
                "start"
            )

            token = parts[1].strip()

            # ------------------------------------------------
            # TYPE LINK
            # ------------------------------------------------

            if token.startswith(
                "t_"
            ):

                with CACHE_LOCK:

                    target = TYPE_INDEX.get(
                        token
                    )

                if not target:

                    sync_cache()

                    with CACHE_LOCK:

                        target = TYPE_INDEX.get(
                            token
                        )

                if not target:

                    send_message(
                        chat_id,
                        "❌ لینک نوع فایل پیدا نشد "
                        "یا حذف شده."
                    )

                    return jsonify(
                        {"ok": True}
                    )

                real_key, file_type = target

                set_pending(
                    user_id,
                    f"TYPE::{real_key}::{file_type}"
                )

            # ------------------------------------------------
            # FULL EPISODE LINK
            # ------------------------------------------------

            else:

                if not get_episode(
                    token
                ):

                    send_message(
                        chat_id,
                        "❌ این قسمت پیدا نشد "
                        "یا حذف شده."
                    )

                    return jsonify(
                        {"ok": True}
                    )

                set_pending(
                    user_id,
                    token
                )

            send_join_page(
                chat_id,
                user_id
            )

            return jsonify(
                {"ok": True}
            )

    # ========================================================
    # CALLBACK QUERY
    # ========================================================

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

        from_user = (
            callback.get("from")
            or {}
        )

        user_id = from_user.get(
            "id"
        )

        # ثبت فعالیت کاربر
        EXEC.submit(
            register_user,
            from_user
        )

        # ----------------------------------------------------
        # ADMIN CALLBACK
        # ----------------------------------------------------

        if user_id == ADMIN_ID and data.startswith(
            "admin:"
        ):

            handle_admin_callback(
                callback
            )

            return jsonify(
                {"ok": True}
            )

        callback_message = (
            callback.get("message")
            or {}
        )

        callback_chat = (
            callback_message.get("chat")
            or {}
        )

        chat_id = callback_chat.get(
            "id"
        )

        message_id = callback_message.get(
            "message_id"
        )

        # ====================================================
        # CHECK JOIN
        # ====================================================

        if data == "check_join":

            answer_callback(
                callback_id,
                "در حال بررسی عضویت..."
            )

            pending = get_pending(
                user_id
            )

            if not pending:

                if message_id:

                    delete_message(
                        chat_id,
                        message_id
                    )

                send_message(
                    chat_id,
                    "❌ لینک قسمت پیدا نشد."
                )

                return jsonify(
                    {"ok": True}
                )

            main_ok, missing = check_membership(
                user_id
            )

            if main_ok and not missing:

                if message_id:

                    delete_message(
                        chat_id,
                        message_id
                    )

                send_reaction_page(
                    chat_id
                )

                return jsonify(
                    {"ok": True}
                )

            if not main_ok:

                send_message(
                    chat_id,

                    "هنوز عضو کانال اصلی نیستی 👇",

                    join_keyboard(None)
                )

                return jsonify(
                    {"ok": True}
                )

            send_message(
                chat_id,

                "هنوز عضویت بعضی کانال‌ها "
                "تأیید نشده 👇",

                join_keyboard(missing)
            )

            return jsonify(
                {"ok": True}
            )

        # ====================================================
        # REACTION BUTTON
        # ====================================================

        if data == "check_reactions":

            answer_callback(
                callback_id,
                "در حال ارسال فایل..."
            )

            pending = get_pending(
                user_id
            )

            if not pending:

                if message_id:

                    delete_message(
                        chat_id,
                        message_id
                    )

                send_message(
                    chat_id,
                    "❌ لینک قسمت پیدا نشد."
                )

                return jsonify(
                    {"ok": True}
                )

            if message_id:

                delete_message(
                    chat_id,
                    message_id
                )

            deliver_episode(
                chat_id,
                user_id
            )

            return jsonify(
                {"ok": True}
            )

    return jsonify(
        {"ok": True}
    )


# ============================================================
# WEBHOOK SETUP
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
                "callback_query"
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
