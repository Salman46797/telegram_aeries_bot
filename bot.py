import os
import re
import time
import json
import hashlib
import threading
from datetime import datetime, timezone, timedelta
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

DEFAULT_DELETE_AFTER = 30
DEFAULT_SYNC_INTERVAL = 120

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is missing")


# ============================================================
# APP / SUPABASE / TELEGRAM
# ============================================================

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

app = Flask(__name__)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ============================================================
# HTTP / THREAD POOLS
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

FAST_EXECUTOR = ThreadPoolExecutor(max_workers=16)
MEDIA_EXECUTOR = ThreadPoolExecutor(max_workers=4)
DELIVERY_EXECUTOR = ThreadPoolExecutor(max_workers=8)
DB_EXECUTOR = ThreadPoolExecutor(max_workers=2)


# ============================================================
# RAM CACHE
# ============================================================

EPISODES = {}
TYPE_INDEX = {}
SPONSORS = []
SETTINGS = {}

CACHE_LOCK = threading.RLock()

BOT_USERNAME = ""
CACHE_READY = False


# ============================================================
# DELIVERY / STATS
# ============================================================

DELIVERING_USERS = set()
DELIVERY_LOCK = threading.RLock()

STAT_QUEUE = []
STAT_LOCK = threading.RLock()


# ============================================================
# ADMIN STATE
# ============================================================

ADMIN_STATE = {}
ADMIN_LOCK = threading.RLock()


# ============================================================
# BASIC HELPERS
# ============================================================

def now():
    return datetime.now(timezone.utc)


def telegram_api(method, data=None, timeout=25):
    try:
        response = HTTP.post(
            f"{TG_API}/{method}",
            json=data or {},
            timeout=(4, timeout)
        )

        return response.json()

    except Exception as e:
        print("Telegram API error:", method, e)

        return {
            "ok": False
        }


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    return telegram_api(
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

    return telegram_api(
        "editMessageText",
        data
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

    return telegram_api(
        "answerCallbackQuery",
        data
    )


def delete_message(chat_id, message_id):
    return telegram_api(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


# ============================================================
# TELEGRAM MEMBERSHIP
# ============================================================

def is_member(chat_id, user_id):
    result = telegram_api(
        "getChatMember",
        {
            "chat_id": chat_id,
            "user_id": user_id
        }
    )

    if not result.get("ok"):
        return False

    info = result.get("result", {})

    status = info.get("status", "")

    if status in (
        "creator",
        "administrator",
        "member"
    ):
        return True

    if (
        status == "restricted"
        and info.get("is_member") is True
    ):
        return True

    return False


def check_membership(user_id):
    with CACHE_LOCK:
        sponsors = list(SPONSORS)

    targets = [
        CHANNEL_ID
    ]

    for sponsor in sponsors:
        chat_id = sponsor.get("chat_id")

        if chat_id:
            targets.append(chat_id)

    futures = []

    for chat_id in targets:
        futures.append(
            FAST_EXECUTOR.submit(
                is_member,
                chat_id,
                user_id
            )
        )

    results = []

    for future in futures:
        try:
            results.append(
                bool(future.result())
            )
        except Exception:
            results.append(False)

    if not results:
        return False, []

    main_ok = results[0]

    missing_sponsors = []

    for i in range(1, len(results)):
        if not results[i]:
            index = i - 1

            if index < len(sponsors):
                missing_sponsors.append(
                    sponsors[index]
                )

    return main_ok, missing_sponsors


# ============================================================
# FILE SENDING
# ============================================================

def send_media_file(chat_id, file_info):
    file_type = file_info.get(
        "type",
        "video"
    )

    file_id = file_info.get(
        "file_id"
    )

    caption = file_info.get(
        "caption",
        ""
    )

    if not file_id:
        return {
            "ok": False
        }

    data = {
        "chat_id": chat_id
    }

    if file_type == "document":
        data["document"] = file_id

        if caption:
            data["caption"] = caption

        return telegram_api(
            "sendDocument",
            data,
            40
        )

    data["video"] = file_id
    data["supports_streaming"] = True

    if caption:
        data["caption"] = caption

    return telegram_api(
        "sendVideo",
        data,
        40
    )


# ============================================================
# EPISODE KEYS
# ============================================================

def make_series_key(series_name):
    value = (
        series_name
        .strip()
        .lower()
        .encode("utf-8")
    )

    return (
        "s"
        + hashlib.sha1(value).hexdigest()[:10]
    )


def make_episode_key(
    series_name,
    episode_number
):
    return (
        "ep_"
        + make_series_key(series_name)
        + "_"
        + str(episode_number)
    )


def make_type_code(
    episode_key,
    file_type
):
    value = (
        f"{episode_key}|{file_type}"
        .encode("utf-8")
    )

    return (
        "t_"
        + hashlib.sha1(value).hexdigest()[:12]
    )


# ============================================================
# CAPTION / FILE TYPE
# ============================================================

def normalize_file_type(caption):
    text = (
        caption or ""
    ).replace("ي", "ی").replace(
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
        or "زیرنویس‌مووی‌باز" in text
        or "زیرنویس مووی‌باز" in text
    ):
        return "زیرنویس مووی باز"

    return "سایر"


def enrich_files(
    files,
    episode_key
):
    result = []

    for file_info in files or []:
        item = dict(file_info)

        file_type = (
            item.get("file_type")
            or normalize_file_type(
                item.get("caption", "")
            )
        )

        item["file_type"] = file_type

        item["type_code"] = (
            item.get("type_code")
            or make_type_code(
                episode_key,
                file_type
            )
        )

        result.append(item)

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
            r"سریال\s*[:：-]?\s*(.+?)(?:\n|$)",
            caption,
            re.IGNORECASE
        )

    episode_match = re.search(
        r"قسمت\s*[:：-]?\s*(\d+)",
        caption,
        re.IGNORECASE
    )

    if (
        not series_match
        or not episode_match
    ):
        return None

    series_name = (
        series_match
        .group(1)
        .strip()
    )

    episode_number = int(
        episode_match.group(1)
    )

    return {
        "series_name": series_name,
        "episode_number": episode_number,
        "episode_key": make_episode_key(
            series_name,
            episode_number
        )
    }


def is_preview_caption(caption):
    return bool(
        re.search(
            r"پیش[\s‌-]*نمایش|\bpreview\b",
            caption or "",
            re.IGNORECASE
        )
    )


# ============================================================
# CACHE SYNC
# ============================================================

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
        local_type_index = {}

        for row in episodes_result.data or []:
            episode_key = row.get(
                "episode_key"
            )

            if not episode_key:
                continue

            item = dict(row)

            item["files"] = enrich_files(
                item.get("files") or [],
                episode_key
            )

            local_episodes[
                episode_key
            ] = item

            for file_info in item["files"]:
                type_code = file_info.get(
                    "type_code"
                )

                if type_code:
                    local_type_index[
                        type_code
                    ] = (
                        episode_key,
                        file_info.get(
                            "file_type"
                        )
                    )

        settings = {}

        try:
            settings_result = (
                supabase
                .table("bot_settings")
                .select("key,value")
                .execute()
            )

            settings = {
                row["key"]: row["value"]
                for row in (
                    settings_result.data
                    or []
                )
                if row.get("key")
            }

        except Exception as e:
            print(
                "bot_settings sync unavailable:",
                e
            )

        me_result = telegram_api(
            "getMe"
        )

        username = (
            me_result
            .get("result", {})
            .get("username", "")
        )

        with CACHE_LOCK:
            EPISODES.clear()
            EPISODES.update(
                local_episodes
            )

            TYPE_INDEX.clear()
            TYPE_INDEX.update(
                local_type_index
            )

            SPONSORS = list(
                sponsors_result.data or []
            )

            SETTINGS.update(
                settings
            )

            if username:
                BOT_USERNAME = username

            CACHE_READY = True

        print(
            "CACHE SYNC OK:",
            len(EPISODES),
            "episodes /",
            len(TYPE_INDEX),
            "type links /",
            len(SPONSORS),
            "sponsors"
        )

        return True

    except Exception as e:
        print(
            "CACHE SYNC ERROR:",
            e
        )

        return False


def get_setting(
    key,
    default=None
):
    with CACHE_LOCK:
        return SETTINGS.get(
            key,
            default
        )


def get_setting_int(
    key,
    default
):
    try:
        return int(
            get_setting(
                key,
                default
            )
        )
    except Exception:
        return default


def set_setting(
    key,
    value
):
    try:
        (
            supabase
            .table("bot_settings")
            .upsert(
                {
                    "key": key,
                    "value": str(value)
                },
                on_conflict="key"
            )
            .execute()
        )

        with CACHE_LOCK:
            SETTINGS[key] = str(value)

        return True

    except Exception as e:
        print(
            "SETTING ERROR:",
            e
        )

        return False


def cache_sync_loop():
    while True:
        interval = get_setting_int(
            "sync_interval",
            DEFAULT_SYNC_INTERVAL
        )

        time.sleep(
            max(
                30,
                interval
            )
        )

        try:
            sync_cache()

        except Exception as e:
            print(
                "CACHE LOOP ERROR:",
                e
            )


# ============================================================
# EPISODES
# ============================================================

def get_episode(
    episode_key
):
    with CACHE_LOCK:
        episode = EPISODES.get(
            episode_key
        )

    if episode:
        return episode

    try:
        result = (
            supabase
            .table("episodes")
            .select("*")
            .eq(
                "episode_key",
                episode_key
            )
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        episode = dict(
            result.data[0]
        )

        episode["files"] = enrich_files(
            episode.get("files") or [],
            episode_key
        )

        with CACHE_LOCK:
            EPISODES[
                episode_key
            ] = episode

        return episode

    except Exception as e:
        print(
            "GET EPISODE ERROR:",
            e
        )

        return None


def save_episode(row):
    row = dict(row)

    row["files"] = enrich_files(
        row.get("files") or [],
        row["episode_key"]
    )

    try:
        (
            supabase
            .table("episodes")
            .upsert(
                row,
                on_conflict="episode_key"
            )
            .execute()
        )

        with CACHE_LOCK:
            EPISODES[
                row["episode_key"]
            ] = row

            for file_info in row["files"]:
                code = file_info.get(
                    "type_code"
                )

                if code:
                    TYPE_INDEX[
                        code
                    ] = (
                        row["episode_key"],
                        file_info.get(
                            "file_type"
                        )
                    )

        return True

    except Exception as e:
        print(
            "SAVE EPISODE ERROR:",
            e
        )

        return False


def delete_episode(
    episode_key
):
    try:
        (
            supabase
            .table("episodes")
            .delete()
            .eq(
                "episode_key",
                episode_key
            )
            .execute()
        )

        with CACHE_LOCK:
            EPISODES.pop(
                episode_key,
                None
            )

            for code, value in list(
                TYPE_INDEX.items()
            ):
                if value[0] == episode_key:
                    TYPE_INDEX.pop(
                        code,
                        None
                    )

        return True

    except Exception as e:
        print(
            "DELETE EPISODE ERROR:",
            e
        )

        return False


def delete_all_episodes():
    try:
        (
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

        return True

    except Exception as e:
        print(
            "DELETE ALL ERROR:",
            e
        )

        return False


# ============================================================
# SPONSORS
# ============================================================

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

        return True

    except Exception as e:
        print(
            "ADD SPONSOR ERROR:",
            e
        )

        return False


def remove_sponsor(
    sponsor_id
):
    global SPONSORS

    try:
        (
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
                sponsor
                for sponsor in SPONSORS
                if int(
                    sponsor.get(
                        "id",
                        -1
                    )
                ) != int(sponsor_id)
            ]

        return True

    except Exception as e:
        print(
            "REMOVE SPONSOR ERROR:",
            e
        )

        return False


# ============================================================
# ADVANCED STATS
# ============================================================

def record_stat(
    user_id,
    event_type,
    episode_key=None,
    file_type=None,
    file_count=0
):
    row = {
        "user_id": int(user_id),
        "event_type": event_type,
        "episode_key": episode_key,
        "file_type": file_type,
        "file_count": int(
            file_count or 0
        )
    }

    with STAT_LOCK:
        STAT_QUEUE.append(row)

        should_flush = (
            len(STAT_QUEUE) >= 50
        )

    if should_flush:
        flush_stats()


def flush_stats():
    with STAT_LOCK:
        if not STAT_QUEUE:
            return

        rows = list(
            STAT_QUEUE
        )

        STAT_QUEUE.clear()

    try:
        (
            supabase
            .table("bot_stats")
            .insert(rows)
            .execute()
        )

    except Exception as e:
        print(
            "STATS FLUSH ERROR:",
            e
        )

        with STAT_LOCK:
            STAT_QUEUE[
                0:0
            ] = rows


def stats_loop():
    while True:
        time.sleep(10)

        try:
            flush_stats()

        except Exception as e:
            print(
                "STATS LOOP ERROR:",
                e
            )


def advanced_stats():
    try:
        flush_stats()

        result = (
            supabase
            .table("bot_stats")
            .select(
                "event_type,episode_key,"
                "file_type,file_count,"
                "created_at,user_id"
            )
            .execute()
        )

        rows = result.data or []

        current = now()

        month_start = current.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

        thirty_days_ago = (
            current
            - timedelta(days=30)
        )

        users = set()

        starts = 0
        downloads = 0
        redownloads = 0
        recent_events = 0
        month_events = 0

        episode_counts = {}
        type_counts = {}

        for row in rows:
            user_id = row.get(
                "user_id"
            )

            if user_id:
                try:
                    users.add(
                        int(user_id)
                    )
                except Exception:
                    pass

            event_type = row.get(
                "event_type"
            )

            if event_type == "start":
                starts += 1

            if event_type in (
                "download",
                "redownload"
            ):
                downloads += 1

                episode_key = row.get(
                    "episode_key"
                )

                if episode_key:
                    episode_counts[
                        episode_key
                    ] = (
                        episode_counts.get(
                            episode_key,
                            0
                        ) + 1
                    )

                file_type = row.get(
                    "file_type"
                )

                if file_type:
                    type_counts[
                        file_type
                    ] = (
                        type_counts.get(
                            file_type,
                            0
                        ) + 1
                    )

            if event_type == "redownload":
                redownloads += 1

            created_at = row.get(
                "created_at"
            )

            if created_at:
                try:
                    dt = datetime.fromisoformat(
                        str(
                            created_at
                        ).replace(
                            "Z",
                            "+00:00"
                        )
                    )

                    if dt.tzinfo is None:
                        dt = dt.replace(
                            tzinfo=timezone.utc
                        )

                    if dt >= thirty_days_ago:
                        recent_events += 1

                    if dt >= month_start:
                        month_events += 1

                except Exception:
                    pass

        top_episodes = sorted(
            episode_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]

        top_types = sorted(
            type_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]

        return {
            "users": len(users),
            "starts": starts,
            "downloads": downloads,
            "redownloads": redownloads,
            "last30": recent_events,
            "month": month_events,
            "top_ep": top_episodes,
            "top_type": top_types
        }

    except Exception as e:
        print(
            "ADVANCED STATS ERROR:",
            e
        )

        return None


# ============================================================
# TOKEN RESOLUTION
# ============================================================

def resolve_token(token):
    if not token:
        return None, None

    if token.startswith("t_"):
        info = TYPE_INDEX.get(
            token
        )

        if not info:
            return None, None

        return (
            info[0],
            info[1]
        )

    if token.startswith("ep_"):
        episode = get_episode(
            token
        )

        if not episode:
            return None, None

        return (
            token,
            None
        )

    return None, None


# ============================================================
# KEYBOARDS
# ============================================================

def admin_keyboard():
    return {
        "keyboard": [
            [
                "🎬 مدیریت قسمت‌ها",
                "📣 اسپانسرها"
            ],
            [
                "📊 آمار پیشرفته",
                "⚡ وضعیت ربات"
            ],
            [
                "🗂 لیست قسمت‌ها",
                "🔄 سینک دیتابیس"
            ],
            [
                "🗑 حذف قسمت",
                "⚙️ تنظیمات"
            ]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def settings_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🗑 حذف بعد 30 ثانیه",
                    "callback_data": "setdel:30"
                },
                {
                    "text": "🗑 60 ثانیه",
                    "callback_data": "setdel:60"
                }
            ],
            [
                {
                    "text": "🗑 120 ثانیه",
                    "callback_data": "setdel:120"
                }
            ],
            [
                {
                    "text": "🔄 سینک 120 ثانیه",
                    "callback_data": "setsync:120"
                },
                {
                    "text": "🔄 300 ثانیه",
                    "callback_data": "setsync:300"
                }
            ],
            [
                {
                    "text": "🔄 همین الان سینک کن",
                    "callback_data": "syncnow"
                }
            ]
        ]
    }


def sponsor_keyboard(token):
    with CACHE_LOCK:
        sponsors = list(
            SPONSORS
        )

    rows = []

    for sponsor in sponsors:
        title = (
            sponsor.get("title")
            or "کانال اسپانسر"
        )

        url = sponsor.get(
            "url"
        )

        if url:
            rows.append(
                [
                    {
                        "text":
                            f"عضویت در {title}",
                        "url": url
                    }
                ]
            )

    rows.append(
        [
            {
                "text":
                    "عضو شدم ✅",
                "callback_data":
                    "join:"
                    + token[:50]
            }
        ]
    )

    return {
        "inline_keyboard": rows
    }


def main_join_keyboard(token):
    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "عضویت در کانال 📺",
                    "url":
                        CHANNEL_URL
                }
            ],
            [
                {
                    "text":
                        "عضو شدم ✅",
                    "callback_data":
                        "join:"
                        + token[:50]
                }
            ]
        ]
    }


def reaction_keyboard(token):
    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "انجام شد ✅",
                    "callback_data":
                        "deliver:"
                        + token[:50]
                }
            ]
        ]
    }


def redownload_keyboard(token):
    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "🔄 دریافت دوباره",
                    "callback_data":
                        "redl:"
                        + token[:50]
                }
            ]
        ]
    }


# ============================================================
# USER PAGES
# ============================================================

def show_join_page(
    chat_id,
    token
):
    with CACHE_LOCK:
        sponsors = list(
            SPONSORS
        )

    if sponsors:
        send_message(
            chat_id,
            "📣برای استفاده از ربات و دریافت فایل :\n\n"
            "1️⃣ابتدا عضو کانال های زیر بشید\n"
            "2️⃣سپس رو دکمه عضو شدم کلیک کنید",
            sponsor_keyboard(
                token
            )
        )

    else:
        send_message(
            chat_id,
            "📣برای استفاده از ربات و دریافت فایل :\n\n"
            "1️⃣ابتدا عضو کانال زیر بشید\n"
            "2️⃣سپس رو دکمه عضو شدم کلیک کنید",
            main_join_keyboard(
                token
            )
        )


def show_reaction_page(
    chat_id,
    token
):
    send_message(
        chat_id,
        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر کانال "
        "@altiustuistsnbol را ری اکت بزنید و سپس برگردید "
        "و دکمه انجام دادم را کلیک کنید ♥️",
        reaction_keyboard(
            token
        )
    )


# ============================================================
# START FLOW
# ============================================================

def handle_start(
    chat_id,
    user_id,
    payload
):
    if not payload:
        show_join_page(
            chat_id,
            ""
        )
        return

    if (
        payload.startswith("t_")
        and payload in TYPE_INDEX
    ):
        episode_key, file_type = (
            TYPE_INDEX[payload]
        )

        record_stat(
            user_id,
            "start",
            episode_key,
            file_type,
            0
        )

        show_join_page(
            chat_id,
            payload
        )

        return

    if payload.startswith("ep_"):
        episode = get_episode(
            payload
        )

        if episode:
            record_stat(
                user_id,
                "start",
                payload,
                None,
                0
            )

            show_join_page(
                chat_id,
                payload
            )

            return

    if payload.startswith(
        "preview__"
    ):
        episode_key = payload.replace(
            "preview__",
            "",
            1
        )

        episode = get_episode(
            episode_key
        )

        if episode:
            previews = [
                f
                for f in episode.get(
                    "files",
                    []
                )
                if f.get(
                    "is_preview"
                )
            ]

            if previews:
                send_media_file(
                    chat_id,
                    previews[0]
                )

        return

    show_join_page(
        chat_id,
        ""
    )


# ============================================================
# DELIVERY
# ============================================================

def claim_delivery(
    user_id
):
    with DELIVERY_LOCK:
        if user_id in DELIVERING_USERS:
            return False

        DELIVERING_USERS.add(
            user_id
        )

        return True


def release_delivery(
    user_id
):
    with DELIVERY_LOCK:
        DELIVERING_USERS.discard(
            user_id
        )


def deliver_episode(
    chat_id,
    user_id,
    token
):
    user_id = int(user_id)

    if not claim_delivery(
        user_id
    ):
        send_message(
            chat_id,
            "⏳ فایل در حال ارسال است..."
        )
        return

    try:
        episode_key, selected_type = (
            resolve_token(token)
        )

        if not episode_key:
            send_message(
                chat_id,
                "لینک قسمت پیدا نشد یا منقضی شده."
            )
            return

        episode = get_episode(
            episode_key
        )

        if not episode:
            send_message(
                chat_id,
                "❌ اطلاعات قسمت پیدا نشد."
            )
            return

        all_files = [
            f
            for f in episode.get(
                "files",
                []
            )
            if not f.get(
                "is_preview"
            )
        ]

        if selected_type:
            files = [
                f
                for f in all_files
                if f.get(
                    "file_type"
                ) == selected_type
            ]

            if not files:
                files = all_files

        else:
            files = all_files

        if not files:
            send_message(
                chat_id,
                "❌ برای این قسمت فایلی ثبت نشده."
            )
            return

        delete_after = get_setting_int(
            "delete_after",
            DEFAULT_DELETE_AFTER
        )

        warning = send_message(
            chat_id,
            "⚠️ توجه:\n"
            f"فایل‌های ارسالی بعد از "
            f"{delete_after} ثانیه حذف می‌شوند.\n"
            "قبل از تمام شدن زمان، فایل‌ها را ذخیره کن."
        )

        sent_message_ids = []

        for file_info in files:
            result = send_media_file(
                chat_id,
                file_info
            )

            if result.get("ok"):
                message_id = (
                    result
                    .get("result", {})
                    .get("message_id")
                )

                if message_id:
                    sent_message_ids.append(
                        message_id
                    )

        record_stat(
            user_id,
            "download",
            episode_key,
            selected_type,
            len(files)
        )

        def cleanup():
            time.sleep(
                max(
                    1,
                    delete_after
                )
            )

            for message_id in (
                sent_message_ids
            ):
                delete_message(
                    chat_id,
                    message_id
                )

            if warning.get("ok"):
                warning_id = (
                    warning
                    .get("result", {})
                    .get("message_id")
                )

                if warning_id:
                    delete_message(
                        chat_id,
                        warning_id
                    )

            send_message(
                chat_id,
                "فایل‌های ارسالی حذف شدند 🗑️",
                redownload_keyboard(
                    token
                )
            )

        MEDIA_EXECUTOR.submit(
            cleanup
        )

    finally:
        release_delivery(
            user_id
        )


# ============================================================
# ADMIN HELPERS
# ============================================================

def is_admin(user_id):
    return int(user_id) == ADMIN_ID


def admin_status_text():
    with CACHE_LOCK:
        episodes_count = len(
            EPISODES
        )

        type_count = len(
            TYPE_INDEX
        )

        sponsors_count = len(
            SPONSORS
        )

        ready = CACHE_READY

    return (
        "⚡ وضعیت ربات\n\n"
        f"Cache: "
        f"{'آماده ✅' if ready else 'آماده نیست ❌'}\n"
        f"قسمت‌ها: {episodes_count}\n"
        f"لینک‌های نوع: {type_count}\n"
        f"اسپانسرها: {sponsors_count}\n"
        f"زمان حذف: "
        f"{get_setting_int('delete_after', 30)} ثانیه\n"
        f"Sync: "
        f"{get_setting_int('sync_interval', 120)} ثانیه"
    )


def list_episodes_text():
    with CACHE_LOCK:
        episodes = list(
            EPISODES.values()
        )

    if not episodes:
        return (
            "🗂 هیچ قسمتی ثبت نشده."
        )

    episodes.sort(
        key=lambda x: (
            x.get(
                "series_name",
                ""
            ),
            int(
                x.get(
                    "episode_number",
                    0
                )
            )
        )
    )

    lines = []

    for episode in episodes:
        lines.append(
            "• "
            f"{episode.get('series_name')} "
            "— قسمت "
            f"{episode.get('episode_number')} "
            "— "
            f"{episode.get('episode_key')}"
        )

    return (
        "🗂 قسمت‌ها:\n\n"
        + "\n".join(lines)
    )


def settings_text():
    return (
        "⚙️ تنظیمات\n\n"
        f"🗑 زمان حذف: "
        f"{get_setting_int('delete_after', 30)} ثانیه\n"
        f"🔄 فاصله سینک: "
        f"{get_setting_int('sync_interval', 120)} ثانیه"
    )


def episode_links(episode):
    username = BOT_USERNAME

    if not username:
        return []

    links = []
    seen_types = set()

    for file_info in episode.get(
        "files",
        []
    ):
        if file_info.get(
            "is_preview"
        ):
            continue

        file_type = file_info.get(
            "file_type",
            "سایر"
        )

        code = file_info.get(
            "type_code"
        )

        if (
            not code
            or file_type in seen_types
        ):
            continue

        seen_types.add(
            file_type
        )

        links.append(
            f"• {file_type}: "
            f"https://t.me/{username}"
            f"?start={code}"
        )

    links.append(
        "• کل قسمت: "
        f"https://t.me/{username}"
        f"?start={episode['episode_key']}"
    )

    return links


# ============================================================
# ADMIN TEXT BUTTONS
# ============================================================

def handle_admin_button(
    user_id,
    text
):
    if not is_admin(
        user_id
    ):
        return False

    if text == "🎬 مدیریت قسمت‌ها":
        send_message(
            user_id,
            "🎬 مدیریت قسمت‌ها\n\n"
            "فایل یا ویدیو را با کپشن استاندارد "
            "برای ربات ارسال کن.\n\n"
            "دستورات:\n"
            "/episodes\n"
            "/delete_episode EPISODE_KEY\n"
            "/delete_all",
            admin_keyboard()
        )
        return True

    if text == "📣 اسپانسرها":
        send_message(
            user_id,
            "📣 مدیریت اسپانسرها\n\n"
            "/add_sponsor @channel | نام کانال | https://t.me/channel\n"
            "/remove_sponsor ID\n"
            "/sponsors",
            admin_keyboard()
        )
        return True

    if text == "📊 آمار پیشرفته":
        stats = advanced_stats()

        if not stats:
            send_message(
                user_id,
                "❌ دریافت آمار انجام نشد.",
                admin_keyboard()
            )
            return True

        episode_lines = (
            "\n".join(
                f"• {key}: {value}"
                for key, value
                in stats["top_ep"]
            )
            or "—"
        )

        type_lines = (
            "\n".join(
                f"• {key}: {value}"
                for key, value
                in stats["top_type"]
            )
            or "—"
        )

        text = (
            "📊 آمار پیشرفته\n\n"
            f"کاربران یکتا در لاگ: "
            f"{stats['users']}\n"
            f"شروع‌ها: "
            f"{stats['starts']}\n"
            f"دانلودها: "
            f"{stats['downloads']}\n"
            f"دریافت دوباره: "
            f"{stats['redownloads']}\n"
            f"رویدادهای ۳۰ روز اخیر: "
            f"{stats['last30']}\n"
            f"رویدادهای این ماه: "
            f"{stats['month']}\n\n"
            "پرمصرف‌ترین قسمت‌ها:\n"
            f"{episode_lines}\n\n"
            "انواع فایل:\n"
            f"{type_lines}"
        )

        send_message(
            user_id,
            text,
            admin_keyboard()
        )

        return True

    if text == "⚡ وضعیت ربات":
        send_message(
            user_id,
            admin_status_text(),
            admin_keyboard()
        )
        return True

    if text == "🗂 لیست قسمت‌ها":
        send_message(
            user_id,
            list_episodes_text(),
            admin_keyboard()
        )
        return True

    if text == "🔄 سینک دیتابیس":
        success = sync_cache()

        send_message(
            user_id,
            "✅ سینک انجام شد."
            if success
            else
            "❌ سینک خطا داشت.",
            admin_keyboard()
        )

        return True

    if text == "🗑 حذف قسمت":
        send_message(
            user_id,
            "فرمت حذف:\n\n"
            "/delete_episode EPISODE_KEY",
            admin_keyboard()
        )

        return True

    if text == "⚙️ تنظیمات":
        send_message(
            user_id,
            settings_text(),
            settings_keyboard()
        )

        return True

    return False


# ============================================================
# ADMIN MEDIA / COMMANDS
# ============================================================

def handle_admin_message(
    message
):
    user_id = (
        message
        .get("from", {})
        .get("id")
    )

    if not user_id:
        return False

    if not is_admin(
        user_id
    ):
        return False

    text = message.get(
        "text",
        ""
    )

    if text and handle_admin_button(
        user_id,
        text
    ):
        return True

    if text == "/start":
        send_message(
            user_id,
            "پنل مدیریت آماده است.",
            admin_keyboard()
        )
        return True

    if text == "/stats":
        stats = advanced_stats()

        send_message(
            user_id,
            json.dumps(
                stats or {},
                ensure_ascii=False,
                indent=2
            ),
            admin_keyboard()
        )

        return True

    if text == "/sponsors":
        with CACHE_LOCK:
            sponsors = list(
                SPONSORS
            )

        if not sponsors:
            result = (
                "📣 هیچ اسپانسری ثبت نشده."
            )
        else:
            result = "\n".join(
                f"{x.get('id')} — "
                f"{x.get('title')} — "
                f"{x.get('chat_id')}"
                for x in sponsors
            )

        send_message(
            user_id,
            result,
            admin_keyboard()
        )

        return True

    if text.startswith(
        "/add_sponsor "
    ):
        parts = [
            x.strip()
            for x in text[
                13:
            ].split("|")
        ]

        if len(parts) != 3:
            send_message(
                user_id,
                "❌ فرمت اشتباه است.\n\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel",
                admin_keyboard()
            )
            return True

        success = add_sponsor(
            parts[0],
            parts[1],
            parts[2]
        )

        send_message(
            user_id,
            "✅ اسپانسر اضافه شد."
            if success
            else
            "❌ ذخیره اسپانسر انجام نشد.",
            admin_keyboard()
        )

        return True

    if text.startswith(
        "/remove_sponsor "
    ):
        try:
            sponsor_id = text.split(
                maxsplit=1
            )[1]

            success = remove_sponsor(
                sponsor_id
            )

        except Exception:
            success = False

        send_message(
            user_id,
            "✅ اسپانسر حذف شد."
            if success
            else
            "❌ حذف اسپانسر انجام نشد.",
            admin_keyboard()
        )

        return True

    if text == "/sync":
        success = sync_cache()

        send_message(
            user_id,
            "✅ سینک شد."
            if success
            else
            "❌ سینک نشد.",
            admin_keyboard()
        )

        return True

    if text == "/cache":
        send_message(
            user_id,
            admin_status_text(),
            admin_keyboard()
        )

        return True

    if text == "/episodes":
        send_message(
            user_id,
            list_episodes_text(),
            admin_keyboard()
        )

        return True

    if text.startswith(
        "/delete_episode "
    ):
        episode_key = text.split(
            maxsplit=1
        )[1].strip()

        success = delete_episode(
            episode_key
        )

        send_message(
            user_id,
            "✅ قسمت حذف شد."
            if success
            else
            "❌ حذف قسمت انجام نشد.",
            admin_keyboard()
        )

        return True

    if text == "/delete_all":
        success = delete_all_episodes()

        send_message(
            user_id,
            "✅ همه قسمت‌ها حذف شدند."
            if success
            else
            "❌ حذف همه قسمت‌ها انجام نشد.",
            admin_keyboard()
        )

        return True

    delete_match = re.match(
        r"^/delete_after\s+(\d+)$",
        text
    )

    if delete_match:
        value = max(
            5,
            min(
                3600,
                int(
                    delete_match.group(1)
                )
            )
        )

        success = set_setting(
            "delete_after",
            value
        )

        send_message(
            user_id,
            "✅ زمان حذف ذخیره شد."
            if success
            else
            "❌ ذخیره نشد.",
            admin_keyboard()
        )

        return True

    sync_match = re.match(
        r"^/sync_interval\s+(\d+)$",
        text
    )

    if sync_match:
        value = max(
            30,
            min(
                3600,
                int(
                    sync_match.group(1)
                )
            )
        )

        success = set_setting(
            "sync_interval",
            value
        )

        send_message(
            user_id,
            "✅ زمان سینک ذخیره شد."
            if success
            else
            "❌ ذخیره نشد.",
            admin_keyboard()
        )

        return True

    # --------------------------------------------------------
    # ADMIN MEDIA
    # --------------------------------------------------------

    caption = message.get(
        "caption",
        ""
    )

    parsed = parse_caption(
        caption
    )

    media = (
        message.get("video")
        or message.get("document")
    )

    if parsed and media:
        media_type = (
            "video"
            if message.get("video")
            else
            "document"
        )

        file_info = {
            "type": media_type,
            "file_id": media.get(
                "file_id"
            ),
            "caption": caption,
            "file_type": normalize_file_type(
                caption
            ),
            "is_preview": is_preview_caption(
                caption
            )
        }

        episode_key = parsed[
            "episode_key"
        ]

        old_episode = get_episode(
            episode_key
        )

        old_files = (
            old_episode.get(
                "files",
                []
            )
            if old_episode
            else []
        )

        files = (
            old_files
            + [file_info]
        )

        row = {
            "episode_key":
                episode_key,
            "series_name":
                parsed[
                    "series_name"
                ],
            "episode_number":
                parsed[
                    "episode_number"
                ],
            "files":
                files
        }

        success = save_episode(
            row
        )

        if not success:
            send_message(
                user_id,
                "❌ ذخیره قسمت انجام نشد.",
                admin_keyboard()
            )

            return True

        fresh = get_episode(
            episode_key
        ) or row

        links = episode_links(
            fresh
        )

        if links:
            link_text = "\n".join(
                links
            )
        else:
            link_text = (
                "لینک قابل استفاده‌ای "
                "برای این قسمت ساخته نشد."
            )

        send_message(
            user_id,
            "✅ فایل قسمت ذخیره شد.\n\n"
            "🔗 لینک‌ها:\n"
            + link_text,
            admin_keyboard()
        )

        return True

    return False


# ============================================================
# USER MESSAGES
# ============================================================

def handle_user_message(
    message
):
    user = message.get(
        "from",
        {}
    )

    user_id = user.get(
        "id"
    )

    chat_id = (
        message
        .get("chat", {})
        .get("id")
    )

    if not user_id:
        return

    text = message.get(
        "text",
        ""
    )

    if text.startswith(
        "/start"
    ):
        parts = text.split(
            maxsplit=1
        )

        payload = (
            parts[1].strip()
            if len(parts) > 1
            else ""
        )

        handle_start(
            chat_id,
            user_id,
            payload
        )

        return


# ============================================================
# CALLBACKS
# ============================================================

def handle_callback(
    callback
):
    user_id = (
        callback
        .get("from", {})
        .get("id")
    )

    message = callback.get(
        "message",
        {}
    )

    chat_id = (
        message
        .get("chat", {})
        .get("id")
    )

    callback_id = callback.get(
        "id"
    )

    data = callback.get(
        "data",
        ""
    )

    if not user_id:
        return

    # --------------------------------------------------------
    # MEMBERSHIP
    # --------------------------------------------------------

    if data.startswith(
        "join:"
    ):
        token = data[
            5:
        ]

        episode_key, _ = resolve_token(
            token
        )

        if not episode_key:
            answer_callback(
                callback_id,
                "لینک منقضی یا نامعتبر است.",
                True
            )
            return

        main_ok, missing = check_membership(
            user_id
        )

        if not main_ok:
            answer_callback(
                callback_id,
                "هنوز عضو کانال اصلی نیستی.",
                True
            )
            return

        if missing:
            answer_callback(
                callback_id,
                "اول عضو همه کانال‌های اسپانسر شو.",
                True
            )
            return

        answer_callback(
            callback_id,
            "عضویت تأیید شد ✅"
        )

        show_reaction_page(
            chat_id,
            token
        )

        return

    # --------------------------------------------------------
    # FAKE REACTION GATE
    #
    # طبق درخواست:
    # هیچ بررسی واقعی ری‌اکشن انجام نمی‌شود.
    # هیچ درخواست Supabase برای reactions وجود ندارد.
    # --------------------------------------------------------

    if data.startswith(
        "deliver:"
    ):
        token = data[
            8:
        ]

        episode_key, _ = resolve_token(
            token
        )

        if not episode_key:
            answer_callback(
                callback_id,
                "لینک منقضی یا نامعتبر است.",
                True
            )
            return

        answer_callback(
            callback_id,
            "در حال ارسال..."
        )

        DELIVERY_EXECUTOR.submit(
            deliver_episode,
            chat_id,
            user_id,
            token
        )

        return

    # --------------------------------------------------------
    # REDOWNLOAD
    # --------------------------------------------------------

    if data.startswith(
        "redl:"
    ):
        token = data[
            5:
        ]

        episode_key, file_type = (
            resolve_token(token)
        )

        if not episode_key:
            answer_callback(
                callback_id,
                "لینک منقضی یا نامعتبر است.",
                True
            )
            return

        record_stat(
            user_id,
            "redownload",
            episode_key,
            file_type,
            0
        )

        show_join_page(
            chat_id,
            token
        )

        answer_callback(
            callback_id
        )

        return

    # --------------------------------------------------------
    # ADMIN SETTINGS
    # --------------------------------------------------------

    if (
        is_admin(user_id)
        and data.startswith(
            "setdel:"
        )
    ):
        value = int(
            data.split(
                ":",
                1
            )[1]
        )

        value = max(
            5,
            min(
                3600,
                value
            )
        )

        success = set_setting(
            "delete_after",
            value
        )

        answer_callback(
            callback_id,
            "ذخیره شد ✅"
            if success
            else
            "خطا ❌"
        )

        edit_message(
            chat_id,
            message.get(
                "message_id"
            ),
            settings_text(),
            settings_keyboard()
        )

        return

    if (
        is_admin(user_id)
        and data.startswith(
            "setsync:"
        )
    ):
        value = int(
            data.split(
                ":",
                1
            )[1]
        )

        value = max(
            30,
            min(
                3600,
                value
            )
        )

        success = set_setting(
            "sync_interval",
            value
        )

        answer_callback(
            callback_id,
            "ذخیره شد ✅"
            if success
            else
            "خطا ❌"
        )

        edit_message(
            chat_id,
            message.get(
                "message_id"
            ),
            settings_text(),
            settings_keyboard()
        )

        return

    if (
        is_admin(user_id)
        and data == "syncnow"
    ):
        success = sync_cache()

        answer_callback(
            callback_id,
            "سینک شد ✅"
            if success
            else
            "خطا ❌"
        )

        edit_message(
            chat_id,
            message.get(
                "message_id"
            ),
            settings_text(),
            settings_keyboard()
        )

        return

    answer_callback(
        callback_id
    )


# ============================================================
# UPDATE ROUTER
# ============================================================

def handle_update(
    update
):
    if update.get(
        "message"
    ):
        message = update[
            "message"
        ]

        user_id = (
            message
            .get("from", {})
            .get("id")
        )

        if is_admin(
            user_id
            or 0
        ):
            handled = handle_admin_message(
                message
            )

            if handled:
                return

        handle_user_message(
            message
        )

        return

    if update.get(
        "callback_query"
    ):
        handle_callback(
            update[
                "callback_query"
            ]
        )

        return

    # channel_post intentionally ignored.
    # No reaction tracking.
    # No channel_posts table.
    if update.get(
        "channel_post"
    ):
        return


# ============================================================
# WEBHOOK
# ============================================================

@app.post("/webhook")
def webhook():
    try:
        update = (
            request
            .get_json(
                silent=True
            )
            or {}
        )

        handle_update(
            update
        )

    except Exception as e:
        print(
            "WEBHOOK ERROR:",
            e
        )

    return jsonify(
        {
            "ok": True
        }
    )


@app.get("/")
def health():
    return "OK", 200


# ============================================================
# WEBHOOK SETUP
# ============================================================

def setup_webhook():
    webhook_url = os.getenv(
        "WEBHOOK_URL",
        "https://telegram-aeries-bot.onrender.com/webhook"
    )

    result = telegram_api(
        "setWebhook",
        {
            "url": webhook_url,
            "allowed_updates": [
                "message",
                "callback_query",
                "channel_post"
            ]
        }
    )

    print(
        "WEBHOOK:",
        result
    )


# ============================================================
# STARTUP
# ============================================================

sync_cache()

setup_webhook()

threading.Thread(
    target=cache_sync_loop,
    daemon=True
).start()

threading.Thread(
    target=stats_loop,
    daemon=True
).start()


# ============================================================
# LOCAL RUN
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "10000"
            )
        )
    )
