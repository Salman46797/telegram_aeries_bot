import os
import re
import time
import hashlib
import threading
from datetime import datetime, timezone
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
CACHE_SYNC_SECONDS = 90

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    "https://telegram-aeries-bot.onrender.com"
).rstrip("/")


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
# FAST HTTP
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


EXECUTOR = ThreadPoolExecutor(
    max_workers=20
)


# ============================================================
# RAM CACHE
# ============================================================

EPISODES = {}
TYPE_INDEX = {}
SPONSORS = []
PENDING = {}

CACHE_LOCK = threading.RLock()

BOT_USERNAME = ""
CACHE_READY = False


# ============================================================
# USER STATS CACHE
# ============================================================

TOUCHED_TODAY = set()
TOUCH_LOCK = threading.Lock()


# ============================================================
# TELEGRAM API
# ============================================================

def tg(method, data=None, timeout=25):

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


def get_me():

    return tg(
        "getMe"
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


def send_video(
    chat_id,
    file_id,
    caption=None
):

    data = {
        "chat_id": chat_id,
        "video": file_id,
        "supports_streaming": True
    }

    if caption:
        data["caption"] = caption

    return tg(
        "sendVideo",
        data
    )


def send_document(
    chat_id,
    file_id,
    caption=None
):

    data = {
        "chat_id": chat_id,
        "document": file_id
    }

    if caption:
        data["caption"] = caption

    return tg(
        "sendDocument",
        data
    )


# ============================================================
# FILE HELPERS
# ============================================================

def normalize_text(text):

    return (
        (text or "")
        .replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "‌")
    )


def normalize_file_type(caption):

    text = normalize_text(caption)

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
        or "زیرنویس‌مووی باز" in text
        or "زیرنویس مووی‌باز" in text
        or "زیرنویس‌مووی‌باز" in text
    ):
        return "زیرنویس مووی باز"

    return "سایر"


def make_series_key(series_name):

    value = (
        series_name
        .strip()
        .lower()
        .encode("utf-8")
    )

    return "s" + hashlib.sha1(
        value
    ).hexdigest()[:10]


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


def make_preview_key(
    series_name,
    episode_number
):

    return (
        "preview__"
        + make_episode_key(
            series_name,
            episode_number
        )
    )


def make_type_code(
    episode_key,
    file_type
):

    raw = (
        episode_key
        + "|"
        + file_type
    ).encode("utf-8")

    return (
        "t_"
        + hashlib.sha1(raw).hexdigest()[:12]
    )


def enrich_files(
    files,
    episode_key
):

    result = []

    for original in files or []:

        item = dict(original)

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


def is_preview_caption(caption):

    text = normalize_text(caption)

    return bool(
        re.search(
            r"پیش\s*[-‌]?\s*نمایش",
            text,
            re.IGNORECASE
        )
    ) or bool(
        re.search(
            r"\bpreview\b",
            text,
            re.IGNORECASE
        )
    )


def parse_caption(caption):

    if not caption:
        return None

    text = normalize_text(caption)

    series_match = re.search(
        r"سریال\s*[«\"]([^»\"]+)[»\"]",
        text,
        re.IGNORECASE
    )

    if not series_match:

        series_match = re.search(
            r"سریال\s*[:：-]?\s*(.+?)(?:\n|$)",
            text,
            re.IGNORECASE
        )

    episode_match = re.search(
        r"قسمت\s*[:：-]?\s*(\d+)",
        text,
        re.IGNORECASE
    )

    if not series_match:
        return None

    if not episode_match:
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


# ============================================================
# CACHE
# ============================================================

def add_episode_to_cache(row):

    if not row:
        return

    episode_key = row.get(
        "episode_key"
    )

    if not episode_key:
        return

    row = dict(row)

    row["files"] = enrich_files(
        row.get("files") or [],
        episode_key
    )

    EPISODES[episode_key] = row

    for file_info in row["files"]:

        type_code = file_info.get(
            "type_code"
        )

        if type_code:

            TYPE_INDEX[type_code] = (
                episode_key,
                file_info.get("file_type")
            )


def sync_cache():

    global SPONSORS
    global BOT_USERNAME
    global CACHE_READY

    try:

        episode_result = (
            supabase
            .table("episodes")
            .select("*")
            .execute()
        )

        sponsor_result = (
            supabase
            .table("sponsors")
            .select("*")
            .order("id")
            .execute()
        )

        me = get_me()

        username = (
            me
            .get("result", {})
            .get("username", "")
        )

        new_episodes = {}
        new_type_index = {}

        for row in episode_result.data or []:

            key = row.get("episode_key")

            if not key:
                continue

            row = dict(row)

            row["files"] = enrich_files(
                row.get("files") or [],
                key
            )

            new_episodes[key] = row

            for file_info in row["files"]:

                code = file_info.get(
                    "type_code"
                )

                if code:

                    new_type_index[code] = (
                        key,
                        file_info.get("file_type")
                    )

        with CACHE_LOCK:

            EPISODES.clear()
            EPISODES.update(
                new_episodes
            )

            TYPE_INDEX.clear()
            TYPE_INDEX.update(
                new_type_index
            )

            SPONSORS = (
                sponsor_result.data or []
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

    except Exception as e:

        print(
            "CACHE SYNC ERROR:",
            e
        )


def cache_loop():

    while True:

        time.sleep(
            CACHE_SYNC_SECONDS
        )

        sync_cache()


# ============================================================
# EPISODES
# ============================================================

def get_episode(
    episode_key
):

    with CACHE_LOCK:

        row = EPISODES.get(
            episode_key
        )

        if row:
            return row

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

        row = result.data[0]

        with CACHE_LOCK:
            add_episode_to_cache(row)

        return row

    except Exception as e:

        print(
            "get_episode error:",
            e
        )

        return None


def save_episode(
    episode_key,
    series_name,
    episode_number,
    files
):

    files = enrich_files(
        files,
        episode_key
    )

    payload = {
        "episode_key": episode_key,
        "series_name": series_name,
        "episode_number": episode_number,
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

            EPISODES[episode_key] = dict(
                payload
            )

            TYPE_INDEX_KEYS = [
                code
                for code, value in TYPE_INDEX.items()
                if value[0] == episode_key
            ]

            for code in TYPE_INDEX_KEYS:
                TYPE_INDEX.pop(
                    code,
                    None
                )

            for file_info in files:

                code = file_info.get(
                    "type_code"
                )

                if code:

                    TYPE_INDEX[code] = (
                        episode_key,
                        file_info.get(
                            "file_type"
                        )
                    )

        return result

    except Exception as e:

        print(
            "save_episode error:",
            e
        )

        return None


def delete_episode(
    episode_key
):

    try:

        result = (
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
# TYPE LINKS
# ============================================================

def find_type_target(
    type_code
):

    with CACHE_LOCK:

        value = TYPE_INDEX.get(
            type_code
        )

        if value:
            return value

    # آخرین تلاش؛ کش اگر هنوز سینک نشده باشد
    sync_cache()

    with CACHE_LOCK:

        return TYPE_INDEX.get(
            type_code,
            (None, None)
        )


# ============================================================
# PENDING
# ============================================================

def set_pending(
    user_id,
    value
):

    uid = int(user_id)

    PENDING[uid] = value

    EXECUTOR.submit(
        save_pending_db,
        uid,
        value
    )


def save_pending_db(
    user_id,
    episode_key
):

    try:

        return (
            supabase
            .table("pending")
            .upsert(
                {
                    "user_id": user_id,
                    "episode_key": episode_key
                },
                on_conflict="user_id"
            )
            .execute()
        )

    except Exception as e:

        print(
            "pending save error:",
            e
        )


def get_pending(
    user_id
):

    uid = int(user_id)

    value = PENDING.get(
        uid
    )

    if value:
        return value

    try:

        result = (
            supabase
            .table("pending")
            .select("episode_key")
            .eq(
                "user_id",
                uid
            )
            .limit(1)
            .execute()
        )

        if result.data:

            value = result.data[0].get(
                "episode_key"
            )

            if value:

                PENDING[uid] = value

                return value

    except Exception as e:

        print(
            "pending get error:",
            e
        )

    return None


def clear_pending(
    user_id
):

    uid = int(user_id)

    PENDING.pop(
        uid,
        None
    )

    EXECUTOR.submit(
        delete_pending_db,
        uid
    )


def delete_pending_db(
    user_id
):

    try:

        return (
            supabase
            .table("pending")
            .delete()
            .eq(
                "user_id",
                user_id
            )
            .execute()
        )

    except Exception as e:

        print(
            "pending delete error:",
            e
        )


def clear_all_pending():

    PENDING.clear()

    try:

        return (
            supabase
            .table("pending")
            .delete()
            .neq(
                "user_id",
                0
            )
            .execute()
        )

    except Exception as e:

        print(
            "clear pending error:",
            e
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

                SPONSORS = (
                    list(SPONSORS)
                    + [result.data[0]]
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
                sponsor_id
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

        return result

    except Exception as e:

        print(
            "remove sponsor error:",
            e
        )

        return None


# ============================================================
# USER STATISTICS
# ============================================================

def touch_user(
    user_id
):

    try:

        uid = int(user_id)

    except Exception:

        return

    today = (
        datetime
        .now(timezone.utc)
        .date()
        .isoformat()
    )

    key = (
        uid,
        today
    )

    with TOUCH_LOCK:

        if key in TOUCHED_TODAY:

            return

        TOUCHED_TODAY.add(
            key
        )

    EXECUTOR.submit(
        touch_user_db,
        uid,
        today
    )


def touch_user_db(
    user_id,
    today
):

    try:

        now = (
            datetime
            .now(timezone.utc)
            .isoformat()
        )

        # اگر کاربر جدید باشد first_seen ساخته می‌شود.
        # اگر قبلاً وجود داشته باشد first_seen دست‌نخورده می‌ماند.
        (
            supabase
            .table("bot_users")
            .upsert(
                {
                    "user_id": user_id,
                    "last_seen": now
                },
                on_conflict="user_id"
            )
            .execute()
        )

        (
            supabase
            .table("bot_user_activity")
            .upsert(
                {
                    "user_id": user_id,
                    "activity_date": today
                },
                on_conflict="user_id,activity_date"
            )
            .execute()
        )

    except Exception as e:

        print(
            "touch_user error:",
            e
        )


def count_query(
    table,
    column,
    operator,
    value
):

    try:

        query = (
            supabase
            .table(table)
            .select(
                column,
                count="exact",
                head=True
            )
        )

        if operator == "gte":
            query = query.gte(
                column,
                value
            )

        elif operator == "eq":
            query = query.eq(
                column,
                value
            )

        result = query.execute()

        return int(
            result.count or 0
        )

    except Exception as e:

        print(
            "count error:",
            table,
            e
        )

        return 0


def get_user_stats():

    now = datetime.now(
        timezone.utc
    )

    today = now.date().isoformat()

    month_start = (
        now
        .replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )
        .isoformat()
    )

    total_users = 0
    active_today = 0
    active_month = 0
    new_month = 0

    try:

        result = (
            supabase
            .table("bot_users")
            .select(
                "user_id",
                count="exact",
                head=True
            )
            .execute()
        )

        total_users = int(
            result.count or 0
        )

    except Exception as e:

        print(
            "total users stats error:",
            e
        )

    active_today = count_query(
        "bot_user_activity",
        "activity_date",
        "eq",
        today
    )

    active_month = count_query(
        "bot_user_activity",
        "activity_date",
        "gte",
        month_start
    )

    new_month = count_query(
        "bot_users",
        "first_seen",
        "gte",
        month_start
    )

    return {
        "total": total_users,
        "today": active_today,
        "month": active_month,
        "new_month": new_month,
        "month_name": (
            f"{now.year:04d}/"
            f"{now.month:02d}"
        )
    }


# ============================================================
# MEMBERSHIP
# ============================================================

def member_status(
    chat_id,
    user_id
):

    result = get_chat_member(
        chat_id,
        user_id
    )

    if not result.get("ok"):
        return False

    status = (
        result
        .get("result", {})
        .get("status", "")
    )

    return status in (
        "creator",
        "administrator",
        "member"
    )


def check_membership_fast(
    user_id
):

    sponsors = get_sponsors()

    targets = [
        (CHANNEL_ID, None)
    ]

    for sponsor in sponsors:

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

    futures = []

    for chat_id, sponsor in targets:

        futures.append(
            (
                sponsor,
                EXECUTOR.submit(
                    member_status,
                    chat_id,
                    user_id
                )
            )
        )

    main_ok = False
    missing = []

    for index, (
        sponsor,
        future
    ) in enumerate(futures):

        try:

            ok = bool(
                future.result()
            )

        except Exception:

            ok = False

        if index == 0:

            main_ok = ok

        elif not ok and sponsor:

            missing.append(
                sponsor
            )

    return (
        main_ok,
        missing
    )


# ============================================================
# KEYBOARDS
# ============================================================

def sponsor_keyboard(
    sponsors
):

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
                "text": "عضو شدم ✅",
                "callback_data":
                    "check_join"
            }
        ]
    )

    return {
        "inline_keyboard": rows
    }


def main_channel_keyboard():

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
                        "check_join"
                }
            ]
        ]
    }


def reaction_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "انجام شد ✅",
                    "callback_data":
                        "check_reactions"
                }
            ]
        ]
    }


# ============================================================
# USER PAGES
# ============================================================

def show_join_page(
    chat_id
):

    sponsors = get_sponsors()

    text = (
        "📣برای استفاده از ربات و دریافت فایل :\n\n"
        "1️⃣ابتدا عضو کانال های زیر بشید\n"
        "2️⃣سپس رو دکمه عضو شدم کلیک کنید"
    )

    if sponsors:

        send_message(
            chat_id,
            text,
            sponsor_keyboard(
                sponsors
            )
        )

    else:

        send_message(
            chat_id,
            text,
            main_channel_keyboard()
        )


def show_reaction_page(
    chat_id
):

    send_message(
        chat_id,
        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر کانال @altiustuistsnbol را ری اکت بزنید و سپس برگردید و دکمه انجام دادم را کلیک کنید ♥️",
        reaction_keyboard()
    )


# ============================================================
# SEND FILES
# ============================================================

def send_file(
    chat_id,
    file_info
):

    file_id = file_info.get(
        "file_id"
    )

    if not file_id:
        return {
            "ok": False
        }

    caption = file_info.get(
        "caption",
        ""
    )

    file_kind = file_info.get(
        "type",
        "video"
    )

    if file_kind == "document":

        return send_document(
            chat_id,
            file_id,
            caption
        )

    return send_video(
        chat_id,
        file_id,
        caption
    )


def delete_sent_messages_later(
    chat_id,
    message_ids
):

    time.sleep(
        DELETE_AFTER
    )

    futures = []

    for message_id in message_ids:

        futures.append(
            EXECUTOR.submit(
                delete_message,
                chat_id,
                message_id
            )
        )

    for future in futures:

        try:
            future.result()

        except Exception:
            pass

    send_message(
        chat_id,
        "فایل‌های ارسالی حذف شدند 🗑️\n"
        "برای دریافت دوباره، لینک قسمت رو دوباره باز کن."
    )


def send_episode_to_user(
    chat_id,
    user_id
):

    pending = get_pending(
        user_id
    )

    if not pending:

        send_message(
            chat_id,
            "❌ لینک قسمت پیدا نشد."
        )

        return

    selected_type = None
    episode_key = pending

    # TYPE::episode_key::file_type
    if pending.startswith(
        "TYPE::"
    ):

        parts = pending.split(
            "::",
            2
        )

        if len(parts) == 3:

            episode_key = parts[1]
            selected_type = parts[2]

    episode = get_episode(
        episode_key
    )

    if not episode:

        clear_pending(
            user_id
        )

        send_message(
            chat_id,
            "❌ این قسمت پیدا نشد یا حذف شده."
        )

        return

    files = enrich_files(
        episode.get("files") or [],
        episode_key
    )

    if selected_type:

        files = [
            file_info
            for file_info in files
            if file_info.get(
                "file_type"
            ) == selected_type
        ]

    if not files:

        send_message(
            chat_id,
            "❌ فایل این قسمت پیدا نشد."
        )

        return

    clear_pending(
        user_id
    )

    send_message(
        chat_id,
        f"⚠️ توجه:\n"
        f"فایل‌های ارسالی بعد از "
        f"{DELETE_AFTER} ثانیه حذف می‌شوند.\n"
        f"قبل از تمام شدن زمان، فایل‌ها را ذخیره کن."
    )

    futures = []

    for file_info in files:

        futures.append(
            EXECUTOR.submit(
                send_file,
                chat_id,
                file_info
            )
        )

    message_ids = []

    for future in futures:

        try:

            result = future.result()

            if result.get("ok"):

                message_id = (
                    result
                    .get("result", {})
                    .get("message_id")
                )

                if message_id:

                    message_ids.append(
                        message_id
                    )

        except Exception as e:

            print(
                "send file error:",
                e
            )

    if not message_ids:

        send_message(
            chat_id,
            "❌ ارسال فایل انجام نشد. دوباره امتحان کن."
        )

        return

    threading.Thread(
        target=delete_sent_messages_later,
        args=(
            chat_id,
            message_ids
        ),
        daemon=True
    ).start()


# ============================================================
# ADMIN FILE UPLOAD
# ============================================================

def extract_file(
    message
):

    if message.get("video"):

        video = message["video"]

        return {
            "type": "video",
            "file_id":
                video.get("file_id"),
            "caption":
                message.get(
                    "caption",
                    ""
                )
        }

    if message.get("document"):

        document = message["document"]

        return {
            "type": "document",
            "file_id":
                document.get("file_id"),
            "caption":
                message.get(
                    "caption",
                    ""
                )
        }

    return None


def handle_admin_file(
    message
):

    file_info = extract_file(
        message
    )

    if not file_info:

        return False

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
            "❌ کپشن فایل قابل تشخیص نیست.\n\n"
            "نمونه:\n"
            "🪴 سریال «عشق و تخت»\n"
            "🪷 قسمت : 2\n"
            "🫧 زبان اصلی\n"
            "🎍 کیفیت : 1080"
        )

        return True

    is_preview = is_preview_caption(
        caption
    )

    if is_preview:

        episode_key = make_preview_key(
            parsed["series_name"],
            parsed["episode_number"]
        )

    else:

        episode_key = parsed[
            "episode_key"
        ]

    existing = get_episode(
        episode_key
    )

    files = []

    if existing:

        files = list(
            existing.get(
                "files"
            ) or []
        )

    file_info["file_type"] = (
        normalize_file_type(
            caption
        )
    )

    file_info["type_code"] = (
        make_type_code(
            episode_key,
            file_info[
                "file_type"
            ]
        )
    )

    files.append(
        file_info
    )

    saved = save_episode(
        episode_key,
        parsed["series_name"],
        parsed["episode_number"],
        files
    )

    if saved is None:

        send_message(
            ADMIN_ID,
            "❌ ذخیره در Supabase انجام نشد."
        )

        return True

    # لینک ربات
    if not BOT_USERNAME:

        me = get_me()

        username = (
            me
            .get("result", {})
            .get("username", "")
        )

    else:

        username = BOT_USERNAME

    if not username:

        send_message(
            ADMIN_ID,
            "❌ نام کاربری ربات پیدا نشد."
        )

        return True

    base_link = (
        f"https://t.me/"
        f"{username}"
    )

    groups = {}

    for file_item in enrich_files(
        files,
        episode_key
    ):

        file_type = file_item[
            "file_type"
        ]

        groups.setdefault(
            file_type,
            file_item[
                "type_code"
            ]
        )

    label = (
        "پیش‌نمایش"
        if is_preview
        else "قسمت"
    )

    lines = [
        f"✅ {label} ذخیره شد.",
        "",
        f"🪴 سریال: "
        f"{parsed['series_name']}",
        f"🪷 قسمت: "
        f"{parsed['episode_number']}",
        ""
    ]

    for file_type, code in groups.items():

        lines.append(
            f"🎬 {file_type}"
        )

        lines.append(
            f"🔗 "
            f"{base_link}"
            f"?start={code}"
        )

        lines.append("")

    lines.append(
        f"🔗 لینک مستقیم کل {label}:"
    )

    lines.append(
        f"{base_link}"
        f"?start={episode_key}"
    )

    send_message(
        ADMIN_ID,
        "\n".join(lines)
    )

    return True


# ============================================================
# ADMIN COMMANDS
# ============================================================

def handle_admin_command(
    chat_id,
    user_id,
    text
):

    if int(user_id) != ADMIN_ID:
        return False

    text = (
        text or ""
    ).strip()

    # ------------------------
    # START
    # ------------------------

    if text == "/start":

        send_message(
            chat_id,
            "پنل مدیریت ربات فعال است ✅\n\n"
            "/sponsors\n"
            "/stats\n"
            "/episodes\n"
            "/sync\n"
            "/cache\n"
            "/delete_all\n"
            "/delete_episode KEY\n"
            "/delete_preview اسم سریال | شماره قسمت\n\n"
            "/add_sponsor @channel | نام کانال | https://t.me/channel\n"
            "/remove_sponsor ID"
        )

        return True

    # ------------------------
    # SPONSORS
    # ------------------------

    if text == "/sponsors":

        sponsors = get_sponsors()

        if not sponsors:

            send_message(
                chat_id,
                "هیچ اسپانسری ثبت نشده."
            )

            return True

        lines = [
            "📋 لیست اسپانسرها:",
            ""
        ]

        for sponsor in sponsors:

            lines.append(
                f"ID: {sponsor.get('id')}"
            )

            lines.append(
                f"کانال: "
                f"{sponsor.get('chat_id')}"
            )

            lines.append(
                f"نام: "
                f"{sponsor.get('title')}"
            )

            lines.append(
                f"لینک: "
                f"{sponsor.get('url')}"
            )

            lines.append("")

        send_message(
            chat_id,
            "\n".join(lines)
        )

        return True

    # ------------------------
    # ADD SPONSOR
    # ------------------------

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
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )

            return True

        sponsor_chat_id = parts[0]
        title = parts[1]
        url = parts[2]

        result = add_sponsor(
            sponsor_chat_id,
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

    # ------------------------
    # REMOVE SPONSOR
    # ------------------------

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

    # ------------------------
    # STATS
    # ------------------------

    if text == "/stats":

        stats = get_user_stats()

        send_message(
            chat_id,
            "📊 آمار ربات\n\n"
            f"👥 کل کاربران: "
            f"{stats['total']}\n\n"
            f"☀️ کاربران فعال امروز: "
            f"{stats['today']}\n\n"
            f"📅 کاربران فعال این ماه "
            f"({stats['month_name']}): "
            f"{stats['month']}\n\n"
            f"🆕 کاربران جدید این ماه: "
            f"{stats['new_month']}"
        )

        return True

    # ------------------------
    # SYNC
    # ------------------------

    if text == "/sync":

        sync_cache()

        send_message(
            chat_id,
            "✅ کش ربات با Supabase سینک شد."
        )

        return True

    # ------------------------
    # CACHE
    # ------------------------

    if text == "/cache":

        with CACHE_LOCK:

            send_message(
                chat_id,
                "⚡ وضعیت کش:\n\n"
                f"قسمت‌ها: "
                f"{len(EPISODES)}\n"
                f"لینک‌های نوع فایل: "
                f"{len(TYPE_INDEX)}\n"
                f"اسپانسرها: "
                f"{len(SPONSORS)}\n"
                f"وضعیت: "
                f"{'آماده ✅' if CACHE_READY else 'در حال آماده‌سازی'}"
            )

        return True

    # ------------------------
    # EPISODES
    # ------------------------

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
                row
                for row in (
                    result.data or []
                )
                if not str(
                    row.get(
                        "episode_key",
                        ""
                    )
                ).startswith(
                    "preview__"
                )
            ]

            if not rows:

                send_message(
                    chat_id,
                    "هیچ قسمتی ذخیره نشده."
                )

                return True

            lines = [
                "📺 قسمت‌های ذخیره‌شده:",
                ""
            ]

            for row in rows:

                lines.append(
                    f"{row.get('series_name')} "
                    f"- قسمت "
                    f"{row.get('episode_number')}"
                )

                lines.append(
                    row.get(
                        "episode_key",
                        ""
                    )
                )

                lines.append("")

            send_message(
                chat_id,
                "\n".join(lines)
            )

        except Exception as e:

            print(
                "episodes error:",
                e
            )

            send_message(
                chat_id,
                "❌ دریافت لیست قسمت‌ها انجام نشد."
            )

        return True

    # ------------------------
    # DELETE PREVIEW
    # ------------------------

    if text.startswith(
        "/delete_preview"
    ):

        raw = text[
            len("/delete_preview"):
        ].strip()

        parts = [
            x.strip()
            for x in raw.split("|")
        ]

        if (
            len(parts) != 2
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
                "❌ پیش‌نمایش پیدا نشد."
            )

            return True

        delete_episode(
            key
        )

        send_message(
            chat_id,
            "✅ پیش‌نمایش حذف شد.\n"
            "قسمت اصلی هیچ تغییری نکرد."
        )

        return True

    # ------------------------
    # DELETE EPISODE
    # ------------------------

    if text.startswith(
        "/delete_episode"
    ):

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

        delete_episode(
            parts[1].strip()
        )

        send_message(
            chat_id,
            "✅ اطلاعات قسمت حذف شد."
        )

        return True

    # ------------------------
    # DELETE ALL
    # ------------------------

    if text == "/delete_all":

        delete_all_episodes()

        clear_all_pending()

        send_message(
            chat_id,
            "✅ اطلاعات قسمت‌ها و لینک‌ها پاک شدند.\n\n"
            "⚠️ فایل‌های اصلی که قبلاً در تلگرام آپلود شده‌اند حذف نمی‌شوند."
        )

        return True

    return False


# ============================================================
# WEB
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

    return jsonify({
        "ok": True,
        "cache_ready": CACHE_READY,
        "episodes": len(EPISODES),
        "type_links": len(TYPE_INDEX)
    })


# ============================================================
# WEBHOOK
# ============================================================

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
    # CHANNEL POST
    # ========================================================

    channel_post = update.get(
        "channel_post"
    )

    if channel_post:

        chat = channel_post.get(
            "chat",
            {}
        )

        username = (
            chat.get("username")
            or ""
        ).lower()

        if username == CHANNEL_ID.replace(
            "@",
            ""
        ).lower():

            # فقط برای سازگاری با دیتابیس قدیمی
            message_id = channel_post.get(
                "message_id"
            )

            if message_id:

                try:

                    supabase.table(
                        "channel_posts"
                    ).upsert(
                        {
                            "message_id":
                                int(message_id)
                        },
                        on_conflict=
                            "message_id"
                    ).execute()

                except Exception as e:

                    print(
                        "channel post error:",
                        e
                    )

        return jsonify({
            "ok": True
        })

    # ========================================================
    # MESSAGE
    # ========================================================

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

        if user_id:

            touch_user(
                user_id
            )

        text = message.get(
            "text",
            ""
        )

        # --------------------
        # ADMIN FILE
        # --------------------

        if (
            user_id == ADMIN_ID
            and (
                message.get("video")
                or message.get("document")
            )
        ):

            handle_admin_file(
                message
            )

            return jsonify({
                "ok": True
            })

        # --------------------
        # ADMIN COMMAND
        # --------------------

        if (
            user_id == ADMIN_ID
            and text
        ):

            if handle_admin_command(
                chat_id,
                user_id,
                text
            ):

                return jsonify({
                    "ok": True
                })

        # --------------------
        # START
        # --------------------

        if text.startswith(
            "/start"
        ):

            parts = text.split(
                maxsplit=1
            )

            # normal /start
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
                        "لینک قسمت موردنظرت رو باز کن تا فایل برات ارسال بشه."
                    )

                return jsonify({
                    "ok": True
                })

            start_code = (
                parts[1]
                .strip()
            )

            # ----------------
            # TYPE LINK
            # ----------------

            if start_code.startswith(
                "t_"
            ):

                episode_key, file_type = (
                    find_type_target(
                        start_code
                    )
                )

                if not episode_key:

                    send_message(
                        chat_id,
                        "❌ لینک نوع فایل پیدا نشد یا حذف شده."
                    )

                    return jsonify({
                        "ok": True
                    })

                set_pending(
                    user_id,
                    "TYPE::"
                    + episode_key
                    + "::"
                    + file_type
                )

                show_join_page(
                    chat_id
                )

                return jsonify({
                    "ok": True
                })

            # ----------------
            # NORMAL EPISODE
            # ----------------

            episode = get_episode(
                start_code
            )

            if not episode:

                send_message(
                    chat_id,
                    "❌ این قسمت پیدا نشد یا حذف شده."
                )

                return jsonify({
                    "ok": True
                })

            set_pending(
                user_id,
                start_code
            )

            show_join_page(
                chat_id
            )

            return jsonify({
                "ok": True
            })

    # ========================================================
    # CALLBACK
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

        from_user = callback.get(
            "from",
            {}
        )

        user_id = from_user.get(
            "id"
        )

        if user_id:

            touch_user(
                user_id
            )

        callback_message = (
            callback.get(
                "message"
            )
            or {}
        )

        chat = (
            callback_message
            .get("chat")
            or {}
        )

        chat_id = chat.get(
            "id"
        )

        message_id = (
            callback_message
            .get("message_id")
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

                    edit_message(
                        chat_id,
                        message_id,
                        "❌ لینک قسمت پیدا نشد."
                    )

                return jsonify({
                    "ok": True
                })

            main_ok, missing = (
                check_membership_fast(
                    user_id
                )
            )

            # main channel
            if not main_ok:

                send_message(
                    chat_id,
                    "هنوز عضو کانال اصلی نیستی 👇",
                    main_channel_keyboard()
                )

                return jsonify({
                    "ok": True
                })

            # sponsors
            if missing:

                send_message(
                    chat_id,
                    "هنوز عضویت بعضی کانال‌ها تأیید نشده 👇",
                    sponsor_keyboard(
                        missing
                    )
                )

                return jsonify({
                    "ok": True
                })

            # delete membership message
            if message_id:

                delete_message(
                    chat_id,
                    message_id
                )

            # reaction page
            show_reaction_page(
                chat_id
            )

            return jsonify({
                "ok": True
            })

        # ====================================================
        # REACTION BUTTON
        # ====================================================

        if data == "check_reactions":

            # مهم:
            # اینجا هیچ بررسی ری‌اکشنی انجام نمی‌شود.

            answer_callback(
                callback_id,
                "در حال ارسال فایل..."
            )

            if not get_pending(
                user_id
            ):

                send_message(
                    chat_id,
                    "❌ لینک قسمت پیدا نشد."
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

    return jsonify({
        "ok": True
    })


# ============================================================
# WEBHOOK SETUP
# ============================================================

def setup_webhook():

    webhook_url = (
        RENDER_EXTERNAL_URL
        + "/webhook"
    )

    result = tg(
        "setWebhook",
        {
            "url": webhook_url,
            "allowed_updates": [
                "message",
                "callback_query",
                "channel_post"
            ],
            "drop_pending_updates": False
        }
    )

    print(
        "Webhook URL:",
        webhook_url
    )

    print(
        "Webhook result:",
        result
    )


# ============================================================
# STARTUP
# ============================================================

def startup():

    global BOT_USERNAME

    try:

        me = get_me()

        BOT_USERNAME = (
            me
            .get("result", {})
            .get("username", "")
        )

        print(
            "Bot username:",
            BOT_USERNAME
        )

    except Exception as e:

        print(
            "getMe error:",
            e
        )

    setup_webhook()

    # اولین سینک
    sync_cache()

    # سینک هر ۹۰ ثانیه
    threading.Thread(
        target=cache_loop,
        daemon=True
    ).start()


startup()


# ============================================================
# RUN
# ============================================================

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
