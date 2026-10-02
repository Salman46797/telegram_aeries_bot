import os
import re
import time
import hashlib
import threading
import secrets
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
CACHE_SYNC_INTERVAL = int(os.getenv("CACHE_SYNC_INTERVAL", "90"))
USER_FLUSH_INTERVAL = int(os.getenv("USER_FLUSH_INTERVAL", "90"))

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
# FAST IN-MEMORY CACHE
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
PENDING_WRITE_EXECUTOR = ThreadPoolExecutor(max_workers=1)

EPISODES = {}
TYPE_INDEX = {}
PENDING = {}
SPONSORS = []
BOT_USERNAME = None

CACHE_LOCK = threading.RLock()
CACHE_READY = False

# User statistics cache
BOT_USERS = {}
DIRTY_USERS = set()

USER_LOCK = threading.RLock()
USER_LAST_LOCAL_TOUCH = {}
USER_LAST_PERSISTED = {}

USERS_TABLE_READY = False

DELIVERY_LOCK = threading.RLock()
DELIVERING_USERS = set()

# Last successful download target for redownload button
LAST_DELIVERY_TARGET = {}


def _parse_dt(value):
    if not value:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except Exception:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _set_episode_cache(row):
    if not row:
        return

    key = row.get("episode_key")

    if not key:
        return

    files = enrich_files(
        row.get("files") or [],
        key
    )

    row = dict(row)
    row["files"] = files

    EPISODES[key] = row

    for f in files:
        code = f.get("type_code")

        if code:
            TYPE_INDEX[code] = (
                key,
                f.get("file_type")
            )


def _load_bot_users(rows):
    local = {}

    for row in rows or []:
        try:
            uid = int(row.get("user_id"))
        except Exception:
            continue

        first_seen = (
            _parse_dt(row.get("first_seen"))
            or datetime.now(timezone.utc)
        )

        last_seen = (
            _parse_dt(row.get("last_seen"))
            or first_seen
        )

        local[uid] = {
            "first_seen": first_seen,
            "last_seen": last_seen
        }

    return local


def sync_cache_from_supabase():
    global SPONSORS
    global BOT_USERNAME
    global CACHE_READY
    global BOT_USERS
    global USERS_TABLE_READY

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
            key = row.get("episode_key")

            if not key:
                continue

            row_copy = dict(row)

            files = enrich_files(
                row_copy.get("files") or [],
                key
            )

            row_copy["files"] = files

            local_episodes[key] = row_copy

            for f in files:
                code = f.get("type_code")

                if code:
                    local_type_index[code] = (
                        key,
                        f.get("file_type")
                    )

        try:
            users_result = (
                supabase
                .table("bot_users")
                .select(
                    "user_id,first_seen,last_seen"
                )
                .execute()
            )

            local_users = _load_bot_users(
                users_result.data or []
            )

            users_table_ready = True

        except Exception as e:
            local_users = None
            users_table_ready = False

            print(
                "bot_users sync unavailable:",
                e
            )

        bot_username = BOT_USERNAME

        if not bot_username:
            me = get_me()

            bot_username = (
                me.get("result") or {}
            ).get("username")

        with CACHE_LOCK:
            EPISODES.clear()
            EPISODES.update(local_episodes)

            TYPE_INDEX.clear()
            TYPE_INDEX.update(local_type_index)

            SPONSORS = list(
                sponsors_result.data or []
            )

            BOT_USERNAME = bot_username

            CACHE_READY = True

        if local_users is not None:
            with USER_LOCK:

                for uid, record in local_users.items():

                    current = BOT_USERS.get(uid)

                    if current:
                        first_seen = min(
                            current["first_seen"],
                            record["first_seen"]
                        )

                        last_seen = max(
                            current["last_seen"],
                            record["last_seen"]
                        )

                        BOT_USERS[uid] = {
                            "first_seen": first_seen,
                            "last_seen": last_seen
                        }

                    else:
                        BOT_USERS[uid] = record

                USERS_TABLE_READY = users_table_ready

        print(
            f"CACHE SYNC OK: "
            f"{len(EPISODES)} episodes / "
            f"{len(TYPE_INDEX)} type links / "
            f"{len(SPONSORS)} sponsors / "
            f"{len(BOT_USERS)} users"
        )

        return True

    except Exception as e:
        print(
            "cache sync error:",
            e
        )

        return False


def warm_cache():
    return sync_cache_from_supabase()


def cache_sync_loop():

    while True:

        try:
            time.sleep(
                max(
                    30,
                    CACHE_SYNC_INTERVAL
                )
            )

            flush_dirty_users()
            sync_cache_from_supabase()

        except Exception as e:
            print(
                "cache sync loop error:",
                e
            )


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def tg(
    method,
    data=None,
    timeout=30
):

    try:
        r = HTTP.post(
            f"{TG_API}/{method}",
            json=data or {},
            timeout=(4, timeout)
        )

        return r.json()

    except Exception as e:

        print(
            "Telegram error:",
            method,
            e
        )

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


def get_chat(chat_id):
    return tg(
        "getChat",
        {
            "chat_id": chat_id
        }
    )


def get_me():
    return tg("getMe")


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


def send_media_file(
    chat_id,
    file_info
):

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

    if file_type == "document":
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


# ============================================================
# KEY / CAPTION HELPERS
# ============================================================

def make_series_key(series_name):

    series_hash = hashlib.sha1(
        series_name
        .strip()
        .lower()
        .encode("utf-8")
    ).hexdigest()[:10]

    return "s" + series_hash


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


def is_preview_caption(caption):

    return (
        bool(
            re.search(
                r"پیش[\s‌-]*نمایش",
                caption or "",
                re.IGNORECASE
            )
        )
        or
        bool(
            re.search(
                r"\bpreview\b",
                caption or "",
                re.IGNORECASE
            )
        )
    )


def normalize_file_type(caption):

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

    for line in text.splitlines():

        line = line.strip()

        if (
            line
            and not line.startswith(
                (
                    "🪴",
                    "🪷",
                    "🎍"
                )
            )
            and "سریال" not in line
            and "قسمت" not in line
            and "کیفیت" not in line
        ):
            return line[:80]

    return "سایر"


def make_type_code(
    episode_key,
    file_type
):

    raw = (
        f"{episode_key}|{file_type}"
        .encode("utf-8")
    )

    return (
        "t_"
        + hashlib.sha1(raw)
        .hexdigest()[:12]
    )


def make_episode_code(
    episode_key
):

    return (
        "e_"
        + hashlib.sha1(
            str(episode_key)
            .encode("utf-8")
        ).hexdigest()[:12]
    )


def enrich_files(
    files,
    episode_key
):

    out = []

    for f in files or []:

        item = dict(f)

        ftype = (
            item.get("file_type")
            or normalize_file_type(
                item.get(
                    "caption",
                    ""
                )
            )
        )

        item["file_type"] = ftype

        item["type_code"] = (
            item.get("type_code")
            or make_type_code(
                episode_key,
                ftype
            )
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


# ============================================================
# SUPABASE - EPISODES
# ============================================================

def get_episode(episode_key):

    with CACHE_LOCK:

        row = EPISODES.get(
            episode_key
        )

        if row is not None:
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

        row = (
            result.data[0]
            if result.data
            else None
        )

        if row:

            with CACHE_LOCK:
                _set_episode_cache(row)

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
            _set_episode_cache(
                payload
            )

        return result

    except Exception as e:

        print(
            "save_episode error:",
            e
        )

        return None


def delete_episode_from_db(
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
            "delete_all_episodes error:",
            e
        )

        return None


# ============================================================
# SUPABASE - PENDING
# ============================================================

def set_pending(
    user_id,
    episode_key
):

    uid = int(user_id)

    PENDING[uid] = episode_key

    PENDING_WRITE_EXECUTOR.submit(
        _save_pending_db,
        uid,
        episode_key
    )

    return True


def _save_pending_db(
    user_id,
    episode_key
):

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

        print(
            "set_pending db error:",
            e
        )

        return None


def get_pending(user_id):

    uid = int(user_id)

    value = PENDING.get(uid)

    if value:
        return value

    try:

        result = (
            supabase
            .table("pending")
            .select("episode_key")
            .eq(
                "user_id",
                str(uid)
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
            "get_pending error:",
            e
        )

    return None


def clear_pending(user_id):

    uid = int(user_id)

    expected = PENDING.pop(
        uid,
        None
    )

    if expected:

        PENDING_WRITE_EXECUTOR.submit(
            _delete_pending_db,
            uid,
            expected
        )

    return True


def _delete_pending_db(
    user_id,
    expected_episode_key
):

    try:

        return (
            supabase
            .table("pending")
            .delete()
            .eq(
                "user_id",
                str(user_id)
            )
            .eq(
                "episode_key",
                expected_episode_key
            )
            .execute()
        )

    except Exception as e:

        print(
            "clear_pending db error:",
            e
        )

        return None


def clear_all_pending():

    PENDING.clear()

    try:

        return (
            supabase
            .table("pending")
            .delete()
            .neq(
                "user_id",
                ""
            )
            .execute()
        )

    except Exception as e:

        print(
            "clear_all_pending error:",
            e
        )

        return None


# ============================================================
# SUPABASE - SPONSORS
# ============================================================

def get_sponsors():

    with CACHE_LOCK:
        return list(SPONSORS)


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
            "add_sponsor error:",
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
            "remove_sponsor error:",
            e
        )

        return None


# ============================================================
# SUPABASE - CHANNEL POSTS / REACTIONS
# ============================================================

def save_channel_post(
    message_id
):

    try:

        return (
            supabase
            .table("channel_posts")
            .upsert(
                {
                    "message_id": int(
                        message_id
                    )
                },
                on_conflict="message_id"
            )
            .execute()
        )

    except Exception as e:

        print(
            "save_channel_post error:",
            e
        )

        return None


def get_last_posts(
    limit=5
):

    try:

        result = (
            supabase
            .table("channel_posts")
            .select("message_id,created_at")
            .order(
                "created_at",
                desc=True
            )
            .limit(limit)
            .execute()
        )

        return result.data or []

    except Exception as e:

        print(
            "get_last_posts error:",
            e
        )

        return []


def save_reaction(
    user_id,
    message_id,
    reacted=True
):

    try:

        return (
            supabase
            .table("reactions")
            .upsert(
                {
                    "user_id": int(
                        user_id
                    ),
                    "message_id": int(
                        message_id
                    ),
                    "reacted": bool(
                        reacted
                    )
                },
                on_conflict="user_id,message_id"
            )
            .execute()
        )

    except Exception as e:

        print(
            "save_reaction error:",
            e
        )

        return None


def has_reacted(
    user_id,
    message_id
):

    try:

        result = (
            supabase
            .table("reactions")
            .select("reacted")
            .eq(
                "user_id",
                int(user_id)
            )
            .eq(
                "message_id",
                int(message_id)
            )
            .limit(1)
            .execute()
        )

        if result.data:
            return bool(
                result.data[0].get(
                    "reacted"
                )
            )

    except Exception as e:

        print(
            "has_reacted error:",
            e
        )

    return False


def user_reacted_to_last_posts(
    user_id
):

    posts = get_last_posts(5)

    if len(posts) < 5:
        return False

    for post in posts:

        if not has_reacted(
            user_id,
            post["message_id"]
        ):
            return False

    return True


# ============================================================
# SUPABASE - USER STATS
# ============================================================

def touch_user(user_id):

    global USERS_TABLE_READY

    uid = int(user_id)

    now = datetime.now(
        timezone.utc
    )

    with USER_LOCK:

        current = BOT_USERS.get(uid)

        if current is None:

            BOT_USERS[uid] = {
                "first_seen": now,
                "last_seen": now
            }

        else:

            current["last_seen"] = now

        DIRTY_USERS.add(uid)

        USER_LAST_LOCAL_TOUCH[uid] = (
            time.time()
        )


def flush_dirty_users():

    with USER_LOCK:

        ids = list(DIRTY_USERS)

        if not ids:
            return

        rows = []

        for uid in ids:

            record = BOT_USERS.get(uid)

            if not record:
                continue

            rows.append(
                {
                    "user_id": uid,
                    "first_seen": (
                        record["first_seen"]
                        .isoformat()
                    ),
                    "last_seen": (
                        record["last_seen"]
                        .isoformat()
                    )
                }
            )

    if not rows:
        return

    try:

        (
            supabase
            .table("bot_users")
            .upsert(
                rows,
                on_conflict="user_id"
            )
            .execute()
        )

        now_ts = time.time()

        with USER_LOCK:

            for row in rows:

                uid = int(
                    row["user_id"]
                )

                DIRTY_USERS.discard(
                    uid
                )

                USER_LAST_PERSISTED[
                    uid
                ] = now_ts

    except Exception as e:

        print(
            "flush users error:",
            e
        )


def get_stats():

    now = datetime.now(
        timezone.utc
    )

    today = now.date()

    month_start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    with USER_LOCK:
        users = list(
            BOT_USERS.values()
        )

    total_users = len(users)

    active_today = sum(
        1
        for r in users
        if (
            r.get("last_seen")
            and r["last_seen"].date()
            == today
        )
    )

    active_month = sum(
        1
        for r in users
        if (
            r.get("last_seen")
            and r["last_seen"]
            >= month_start
        )
    )

    new_this_month = sum(
        1
        for r in users
        if (
            r.get("first_seen")
            and r["first_seen"]
            >= month_start
        )
    )

    return {
        "month": (
            f"{now.year:04d}/"
            f"{now.month:02d}"
        ),
        "total_users": total_users,
        "active_today": active_today,
        "active_month": active_month,
        "new_this_month": new_this_month
    }


# ============================================================
# SUPABASE - DOWNLOAD STATS
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
            "record_stat error:",
            e
        )

        return None


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

    info = result.get(
        "result",
        {}
    )

    status = info.get(
        "status",
        ""
    )

    if status in (
        "creator",
        "administrator",
        "member"
    ):
        return True

    if (
        status == "restricted"
        and info.get(
            "is_member"
        ) is True
    ):
        return True

    return False


def is_main_channel_member(
    user_id
):

    return member_status(
        CHANNEL_ID,
        user_id
    )


def check_all_sponsors(
    user_id
):

    sponsors = get_sponsors()

    if not sponsors:
        return []

    def check(sponsor):

        chat_id = sponsor.get(
            "chat_id"
        )

        return (
            sponsor
            if (
                chat_id
                and not member_status(
                    chat_id,
                    user_id
                )
            )
            else None
        )

    results = list(
        FastExecutor_map(
            check,
            sponsors
        )
    )

    return [
        x
        for x in results
        if x
    ]


def FastExecutor_map(
    fn,
    items
):

    futures = [
        FAST_EXECUTOR.submit(
            fn,
            item
        )
        for item in items
    ]

    out = []

    for f in futures:

        try:
            out.append(
                f.result()
            )

        except Exception as e:

            print(
                "membership worker error:",
                e
            )

            out.append(None)

    return out


def check_membership_fast(
    user_id
):

    sponsors = get_sponsors()

    targets = [
        (
            CHANNEL_ID,
            None
        )
    ] + [
        (
            s.get("chat_id"),
            s
        )
        for s in sponsors
        if s.get("chat_id")
    ]

    futures = [
        FAST_EXECUTOR.submit(
            member_status,
            chat_id,
            user_id
        )
        for chat_id, _ in targets
    ]

    results = []

    for f in futures:

        try:
            results.append(
                bool(
                    f.result()
                )
            )

        except Exception:
            results.append(False)

    main_ok = (
        results[0]
        if results
        else False
    )

    missing = [
        targets[i][1]
        for i in range(
            1,
            len(targets)
        )
        if (
            not results[i]
            and targets[i][1]
        )
    ]

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
                "text":
                    "عضو شدم ✅",
                "callback_data":
                    "check_join"
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
                    "text":
                        "انجام شد ✅",
                    "callback_data":
                        "check_reactions"
                }
            ]
        ]
    }


def main_channel_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "ورود به کانال 📺",
                    "url":
                        CHANNEL_URL
                }
            ]
        ]
    }


# ============================================================
# USER FLOW
# ============================================================

def show_join_page(
    chat_id,
    user_id
):

    sponsors = get_sponsors()

    if sponsors:

        send_message(
            chat_id,

            "📣برای استفاده از ربات و دریافت فایل :\n\n"
            "1️⃣ابتدا عضو کانال های زیر بشید\n"
            "2️⃣سپس رو دکمه عضو شدم کلیک کنید",

            sponsor_keyboard(
                sponsors
            )
        )

    else:

        send_message(
            chat_id,

            "📣برای استفاده از ربات و دریافت فایل :\n\n"
            "1️⃣ابتدا عضو کانال زیر بشید\n"
            "2️⃣سپس رو دکمه عضو شدم کلیک کنید",

            {
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
        )

    return True


def show_reaction_page(
    chat_id,
    user_id
):

    send_message(
        chat_id,

        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر کانال "
        "@altiustuistsnbol را ری اکت بزنید و سپس برگردید "
        "و دکمه انجام دادم را کلیک کنید ♥️",

        reaction_keyboard()
    )


def claim_delivery(
    user_id
):

    uid = int(user_id)

    with DELIVERY_LOCK:

        if uid in DELIVERING_USERS:
            return False

        DELIVERING_USERS.add(uid)

        return True


def release_delivery(
    user_id
):

    with DELIVERY_LOCK:
        DELIVERING_USERS.discard(
            int(user_id)
        )


def redownload_keyboard(
    target
):

    if not target:
        return None

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "دانلود مجدد ♻️",

                    "url":
                        (
                            f"https://t.me/"
                            f"{BOT_USERNAME}"
                            f"?start={target}"
                            if BOT_USERNAME
                            else CHANNEL_URL
                        )
                }
            ]
        ]
    }


def send_episode_to_user(
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

        episode_key = get_pending(
            user_id
        )

        if not episode_key:

            send_message(
                chat_id,
                "لینک قسمت پیدا نشد. دوباره لینک قسمت رو باز کن."
            )

            return

        selected_type = None
        lookup_key = episode_key

        if episode_key.startswith(
            "TYPE::"
        ):

            parts = episode_key.split(
                "::",
                2
            )

            if len(parts) == 3:

                lookup_key = parts[1]
                selected_type = parts[2]

        episode = get_episode(
            lookup_key
        )

        if not episode:

            clear_pending(
                user_id
            )

            send_message(
                chat_id,
                "این قسمت دیگه موجود نیست."
            )

            return

        files = enrich_files(
            episode.get(
                "files"
            ) or [],
            lookup_key
        )

        if selected_type:

            files = [
                f
                for f in files
                if f.get(
                    "file_type"
                ) == selected_type
            ]

        redownload_target = (
            make_type_code(
                lookup_key,
                selected_type
            )
            if selected_type
            else make_episode_code(
                lookup_key
            )
        )

        if not files:

            send_message(
                chat_id,
                "فایل این قسمت پیدا نشد."
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

        futures = [
            MEDIA_EXECUTOR.submit(
                send_media_file,
                chat_id,
                f
            )
            for f in files
        ]

        sent_ids = []

        for future in futures:

            try:

                result = future.result()

                if result.get("ok"):

                    mid = (
                        result.get(
                            "result"
                        ) or {}
                    ).get(
                        "message_id"
                    )

                    if mid:
                        sent_ids.append(
                            mid
                        )

            except Exception as e:

                print(
                    "send file worker error:",
                    e
                )

        if not sent_ids:

            send_message(
                chat_id,
                "ارسال فایل انجام نشد. چند لحظه بعد دوباره امتحان کن."
            )

            return

        LAST_DELIVERY_TARGET[
            int(user_id)
        ] = redownload_target

        FAST_EXECUTOR.submit(
            record_stat,
            user_id,
            "download",
            lookup_key,
            selected_type,
            len(sent_ids)
        )

        send_message(
            chat_id,
            "برای دریافت دوباره همین فایل‌ها روی دکمه زیر بزن 👇",
            redownload_keyboard(
                redownload_target
            )
        )

        threading.Thread(
            target=delete_sent_messages_later,
            args=(
                chat_id,
                sent_ids
            ),
            daemon=True
        ).start()

    finally:

        release_delivery(
            user_id
        )


def delete_sent_messages_later(
    chat_id,
    message_ids
):

    time.sleep(
        DELETE_AFTER
    )

    futures = [
        FAST_EXECUTOR.submit(
            delete_message,
            chat_id,
            mid
        )
        for mid in message_ids
    ]

    for f in futures:

        try:
            f.result()

        except Exception:
            pass

    target = LAST_DELIVERY_TARGET.get(
        int(chat_id)
    )

    send_message(
        chat_id,

        "فایل‌های ارسالی حذف شدند 🗑️\n"
        "برای دریافت دوباره روی دکمه زیر بزن 👇",

        redownload_keyboard(
            target
        )
    )


# ============================================================
# ADMIN FILE HANDLING
# ============================================================

def extract_file_from_message(
    message
):

    if message.get("video"):

        video = message["video"]

        return {
            "type":
                "video",

            "file_id":
                video.get(
                    "file_id"
                ),

            "caption":
                message.get(
                    "caption",
                    ""
                )
        }

    if message.get("document"):

        document = message["document"]

        return {
            "type":
                "document",

            "file_id":
                document.get(
                    "file_id"
                ),

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

    file_info = extract_file_from_message(
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

    episode_key = (
        make_preview_key(
            parsed["series_name"],
            parsed["episode_number"]
        )
        if is_preview
        else parsed["episode_key"]
    )

    episode = get_episode(
        episode_key
    )

    files = (
        episode.get("files") or []
        if episode
        else []
    )

    file_info["file_type"] = (
        normalize_file_type(
            caption
        )
    )

    file_info["type_code"] = (
        make_type_code(
            episode_key,
            file_info["file_type"]
        )
    )

    files.append(
        file_info
    )

    saved = save_episode(
        episode_key=episode_key,
        series_name=parsed[
            "series_name"
        ],
        episode_number=parsed[
            "episode_number"
        ],
        files=files
    )

    if saved is None:

        send_message(
            ADMIN_ID,
            "❌ ذخیره در Supabase انجام نشد."
        )

        return True

    bot_username = BOT_USERNAME

    if not bot_username:

        bot_username_result = get_me()

        bot_username = (
            bot_username_result.get(
                "result"
            ) or {}
        ).get(
            "username",
            ""
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

    for f in enrich_files(
        files,
        episode_key
    ):

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
        f"🪴 سریال: "
        f"{parsed['series_name']}"
    )

    lines.append(
        f"🪷 قسمت: "
        f"{parsed['episode_number']}"
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
        f"{base_link}?start="
        f"{make_episode_code(episode_key)}"
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


def find_episode_target(
    episode_code
):

    with CACHE_LOCK:

        for key, row in EPISODES.items():

            if (
                make_episode_code(key)
                == episode_code
            ):
                return key

    try:

        result = (
            supabase
            .table("episodes")
            .select("*")
            .execute()
        )

        with CACHE_LOCK:

            for row in result.data or []:

                _set_episode_cache(
                    row
                )

                key = row.get(
                    "episode_key"
                )

                if (
                    key
                    and make_episode_code(
                        key
                    ) == episode_code
                ):
                    return key

    except Exception as e:

        print(
            "find_episode_target error:",
            e
        )

    return None


def find_type_target(
    type_code
):

    with CACHE_LOCK:

        hit = TYPE_INDEX.get(
            type_code
        )

        if hit:
            return hit

    try:

        result = (
            supabase
            .table("episodes")
            .select(
                "episode_key,files"
            )
            .execute()
        )

        with CACHE_LOCK:

            for row in result.data or []:

                _set_episode_cache(
                    row
                )

            return TYPE_INDEX.get(
                type_code,
                (None, None)
            )

    except Exception as e:

        print(
            "find_type_target error:",
            e
        )

        return None, None


# ============================================================
# ADMIN COMMANDS
# ============================================================

def handle_admin_command(
    chat_id,
    user_id,
    text
):

    if user_id != ADMIN_ID:
        return False

    text = text.strip()

    if text == "/start":

        send_message(
            chat_id,

            "پنل مدیریت ربات فعال است ✅\n\n"
            "/sponsors\n"
            "/stats\n"
            "/episodes\n"
            "/delete_all\n"
            "/delete_episode KEY\n"
            "/add_sponsor @channel | نام کانال | https://t.me/channel\n"
            "/remove_sponsor ID\n"
            "/sync\n"
            "/cache"
        )

        return True

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
                "فرمت درست:\n/remove_sponsor ID"
            )

            return True

        sponsor_id = int(
            parts[1]
        )

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

    if text == "/stats":

        stats = get_stats()

        if (
            not USERS_TABLE_READY
            and stats["total_users"] == 0
        ):

            send_message(
                chat_id,

                "❌ آمار کاربران هنوز فعال نشده.\n"
                "یک بار جدول bot_users را طبق SQL همراه این فایل در Supabase بساز و ربات را ری‌استارت کن."
            )

            return True

        send_message(
            chat_id,

            "📊 آمار ربات\n\n"

            f"👥 کل کاربران: "
            f"{stats['total_users']}\n\n"

            f"☀️ کاربران فعال امروز: "
            f"{stats['active_today']}\n\n"

            f"📅 کاربران فعال این ماه "
            f"({stats['month']}): "
            f"{stats['active_month']}\n\n"

            f"🆕 کاربران جدید این ماه: "
            f"{stats['new_this_month']}"
        )

        return True

    if text == "/sync":

        ok = sync_cache_from_supabase()

        send_message(
            chat_id,

            (
                "✅ کش و اطلاعات ربات به‌روزرسانی شد."
                if ok
                else
                "❌ به‌روزرسانی کش انجام نشد."
            )
        )

        return True

    if text == "/cache":

        with CACHE_LOCK:

            episode_count = len(
                EPISODES
            )

            type_count = len(
                TYPE_INDEX
            )

            sponsor_count = len(
                SPONSORS
            )

        with USER_LOCK:

            user_count = len(
                BOT_USERS
            )

        send_message(
            chat_id,

            "⚡ کش ربات\n\n"

            f"🎬 قسمت‌ها: "
            f"{episode_count}\n"

            f"🔗 لینک نوع فایل: "
            f"{type_count}\n"

            f"📣 اسپانسرها: "
            f"{sponsor_count}\n"

            f"👥 کاربران: "
            f"{user_count}\n"

            f"🔄 Sync خودکار: "
            f"هر {CACHE_SYNC_INTERVAL} ثانیه"
        )

        return True

    if text.startswith(
        "/delete_preview"
    ):

        raw = text[
            len("/delete_preview"):
        ].strip()

        parts = [
            x.strip()
            for x in raw.split(
                "|",
                1
            )
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

        delete_episode_from_db(
            key
        )

        send_message(
            chat_id,

            f"✅ پیش‌نمایش قسمت "
            f"{parts[1]} از "
            f"«{parts[0]}» حذف شد.\n\n"
            "قسمت اصلی هیچ تغییری نکرد."
        )

        return True

    if text == "/episodes":

        try:

            with CACHE_LOCK:

                rows = [
                    dict(r)
                    for r in EPISODES.values()
                    if not str(
                        r.get(
                            "episode_key",
                            ""
                        )
                    ).startswith(
                        "preview__"
                    )
                ]

            rows.sort(
                key=lambda r: (
                    str(
                        r.get(
                            "series_name",
                            ""
                        )
                    ),
                    int(
                        r.get(
                            "episode_number",
                            0
                        )
                    )
                )
            )

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
                    f"{row.get('series_name')} "
                    f"- قسمت "
                    f"{row.get('episode_number')}\n"
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

    if text == "/delete_all":

        delete_all_episodes()
        clear_all_pending()

        send_message(
            chat_id,

            "✅ اطلاعات قسمت‌ها و لینک‌های ذخیره‌شده پاک شدند.\n\n"
            "⚠️ فایل‌های اصلی که قبلاً در تلگرام آپلود شده‌اند حذف نمی‌شوند."
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

            f"✅ اطلاعات "
            f"{episode_key} حذف شد."
        )

        return True

    return False


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
            "ok": True,
            "bot": "telegram"
        }
    )


@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = request.get_json(
        silent=True
    ) or {}

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

        chat_username = chat.get(
            "username"
        )

        if (
            chat_username
            and
            chat_username.lower()
            ==
            CHANNEL_ID
            .replace(
                "@",
                ""
            )
            .lower()
        ):

            message_id = channel_post.get(
                "message_id"
            )

            if message_id:

                FAST_EXECUTOR.submit(
                    save_channel_post,
                    message_id
                )

        return jsonify(
            {
                "ok": True
            }
        )

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

        text = message.get(
            "text",
            ""
        )

        if (
            user_id
            and user_id != ADMIN_ID
        ):

            touch_user(
                user_id
            )

        # ----------------------------------------------------
        # ADMIN FILE
        # ----------------------------------------------------

        if user_id == ADMIN_ID:

            if (
                message.get("video")
                or
                message.get("document")
            ):

                handle_admin_file(
                    message
                )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            if (
                text
                and
                handle_admin_command(
                    chat_id,
                    user_id,
                    text
                )
            ):

                return jsonify(
                    {
                        "ok": True
                    }
                )

        # ----------------------------------------------------
        # START
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
                        "لینک قسمت موردنظرت رو باز کن تا فایل برات ارسال بشه."
                    )

                return jsonify(
                    {
                        "ok": True
                    }
                )

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
                        "❌ لینک نوع فایل پیدا نشد یا حذف شده."
                    )

                    return jsonify(
                        {
                            "ok": True
                        }
                    )

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

                return jsonify(
                    {
                        "ok": True
                    }
                )

            # ------------------------------------------------
            # WHOLE EPISODE SAFE LINK
            # ------------------------------------------------

            if episode_key.startswith(
                "e_"
            ):

                real_key = find_episode_target(
                    episode_key
                )

                if not real_key:

                    send_message(
                        chat_id,
                        "❌ لینک قسمت پیدا نشد یا حذف شده."
                    )

                    return jsonify(
                        {
                            "ok": True
                        }
                    )

                episode_key = real_key

            # ------------------------------------------------
            # NORMAL EPISODE KEY
            # ------------------------------------------------

            episode = get_episode(
                episode_key
            )

            if not episode:

                send_message(
                    chat_id,
                    "❌ این قسمت پیدا نشد یا حذف شده."
                )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            set_pending(
                user_id,
                episode_key
            )

            show_join_page(
                chat_id,
                user_id
            )

            return jsonify(
                {
                    "ok": True
                }
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

        from_user = callback.get(
            "from",
            {}
        )

        user_id = from_user.get(
            "id"
        )

        if (
            user_id
            and
            user_id != ADMIN_ID
        ):

            touch_user(
                user_id
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

        message_id = callback_message.get(
            "message_id"
        )

        # ====================================================
        # CHECK MEMBERSHIP
        # ====================================================

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

                return jsonify(
                    {
                        "ok": True
                    }
                )

            main_ok, missing = (
                check_membership_fast(
                    user_id
                )
            )

            # Main channel not joined
            if not main_ok:

                send_message(
                    chat_id,

                    "هنوز عضو کانال اصلی نیستی 👇",

                    main_channel_keyboard()
                )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            # Sponsor channels not joined
            if missing:

                send_message(
                    chat_id,

                    "هنوز عضویت بعضی کانال‌ها تأیید نشده 👇",

                    sponsor_keyboard(
                        missing
                    )
                )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            # Membership confirmed
            if message_id:

                delete_message(
                    chat_id,
                    message_id
                )

            show_reaction_page(
                chat_id,
                user_id
            )

            return jsonify(
                {
                    "ok": True
                }
            )

        # ====================================================
        # REACTION BUTTON
        # ====================================================

        # IMPORTANT:
        # This button does NOT check reactions.
        # It is only a visual confirmation.
        # No Telegram reaction API is called here.
        # No reaction is saved or verified.

        if data == "check_reactions":

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

                return jsonify(
                    {
                        "ok": True
                    }
                )

            if message_id:

                delete_message(
                    chat_id,
                    message_id
                )

            send_episode_to_user(
                chat_id,
                user_id
            )

            return jsonify(
                {
                    "ok": True
                }
            )

        return jsonify(
            {
                "ok": True
            }
        )

    return jsonify(
        {
            "ok": True
        }
    )


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
                "channel_post"
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

warm_cache()

setup_webhook()

threading.Thread(
    target=cache_sync_loop,
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
