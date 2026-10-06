import os
import re
import time
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
SUPABASE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    os.getenv("SUPABASE_KEY", "")
).strip()

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

DELETE_AFTER = 30
CACHE_SYNC_INTERVAL = max(
    30,
    int(os.getenv("CACHE_SYNC_INTERVAL", "90"))
)

USER_FLUSH_INTERVAL = max(
    30,
    int(os.getenv("USER_FLUSH_INTERVAL", "90"))
)

MEMBERSHIP_CACHE_TTL = 60

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

FAST_EXECUTOR = ThreadPoolExecutor(
    max_workers=16
)

MEDIA_EXECUTOR = ThreadPoolExecutor(
    max_workers=8
)

DB_EXECUTOR = ThreadPoolExecutor(
    max_workers=2
)


# ============================================================
# RAM CACHE
# ============================================================

EPISODES = {}

# e_xxxxx -> episode_key
EPISODE_TOKEN_INDEX = {}

# f_xxxxx / old t_xxxxx -> (episode_key, file_type)
TYPE_INDEX = {}

SPONSORS = []

BOT_USERNAME = None

CACHE_READY = False

CACHE_LOCK = threading.RLock()


# user_id -> episode key / TYPE::...
PENDING = {}

PENDING_LOCK = threading.RLock()


# user_id -> {
#   first_seen,
#   last_seen,
#   username,
#   first_name,
#   last_name,
#   is_blocked
# }
BOT_USERS = {}

DIRTY_USERS = set()

USER_LOCK = threading.RLock()


# (chat_id, user_id) -> (is_member, timestamp)
MEMBERSHIP_CACHE = {}

MEMBERSHIP_LOCK = threading.RLock()


# Prevent double delivery
DELIVERING_USERS = set()

DELIVERY_LOCK = threading.RLock()


# Admin state
ADMIN_STATE = {}

ADMIN_STATE_LOCK = threading.RLock()


# Last channel posts in RAM
LAST_CHANNEL_POSTS = []

LAST_CHANNEL_POSTS_LOCK = threading.RLock()


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(
                str(value).replace(
                    "Z",
                    "+00:00"
                )
            )
        except Exception:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


def log_error(context, error):
    print(
        f"[ERROR] {context}: {error}"
    )


# ============================================================
# TELEGRAM API
# ============================================================

def tg(
    method,
    data=None,
    timeout=30
):
    try:
        response = HTTP.post(
            f"{TG_API}/{method}",
            json=data or {},
            timeout=(4, timeout)
        )

        return response.json()

    except Exception as e:
        log_error(
            f"telegram/{method}",
            e
        )

        return {
            "ok": False,
            "error": str(e)
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
    return tg("getMe")


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
    caption="",
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
    caption="",
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


def send_audio(
    chat_id,
    file_id,
    caption="",
    caption_entities=None
):
    data = {
        "chat_id": chat_id,
        "audio": file_id
    }

    if caption:
        data["caption"] = caption

    if caption_entities:
        data["caption_entities"] = caption_entities

    return tg(
        "sendAudio",
        data
    )


def send_photo(
    chat_id,
    file_id,
    caption="",
    caption_entities=None
):
    data = {
        "chat_id": chat_id,
        "photo": file_id
    }

    if caption:
        data["caption"] = caption

    if caption_entities:
        data["caption_entities"] = caption_entities

    return tg(
        "sendPhoto",
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

    if not file_id:
        return {
            "ok": False
        }

    caption = file_info.get(
        "caption",
        ""
    )

    entities = file_info.get(
        "caption_entities"
    )

    if file_type == "document":
        return send_document(
            chat_id,
            file_id,
            caption,
            entities
        )

    if file_type == "audio":
        return send_audio(
            chat_id,
            file_id,
            caption,
            entities
        )

    if file_type == "photo":
        return send_photo(
            chat_id,
            file_id,
            caption,
            entities
        )

    return send_video(
        chat_id,
        file_id,
        caption,
        entities
    )


# ============================================================
# STABLE DEEP LINKS
# ============================================================

def episode_token(
    episode_key
):
    return (
        "e_"
        + hashlib.sha256(
            str(
                episode_key
            ).encode("utf-8")
        ).hexdigest()[:24]
    )


def file_token(
    episode_key,
    file_type
):
    return (
        "f_"
        + hashlib.sha256(
            f"{episode_key}|{file_type}"
            .encode("utf-8")
        ).hexdigest()[:24]
    )


def legacy_type_token(
    episode_key,
    file_type
):
    return (
        "t_"
        + hashlib.sha1(
            f"{episode_key}|{file_type}"
            .encode("utf-8")
        ).hexdigest()[:12]
    )


def episode_link(
    episode_key
):
    if not BOT_USERNAME:
        return ""

    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start={episode_token(episode_key)}"
    )


def file_link(
    episode_key,
    file_type
):
    if not BOT_USERNAME:
        return ""

    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start={file_token(episode_key, file_type)}"
    )


# ============================================================
# CAPTION / EPISODE PARSER
# ============================================================

def make_series_key(
    series_name
):
    return (
        "s"
        + hashlib.sha1(
            series_name.strip()
            .lower()
            .encode("utf-8")
        ).hexdigest()[:10]
    )


def make_episode_key(
    series_name,
    episode_number
):
    return (
        "ep_"
        + make_series_key(
            series_name
        )
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


def is_preview_caption(
    caption
):
    text = caption or ""

    return (
        bool(
            re.search(
                r"پیش[\s‌-]*نمایش",
                text,
                re.IGNORECASE
            )
        )
        or
        bool(
            re.search(
                r"\bpreview\b",
                text,
                re.IGNORECASE
            )
        )
    )


def normalize_file_type(
    caption
):
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
    return legacy_type_token(
        episode_key,
        file_type
    )


def enrich_files(
    files,
    episode_key
):
    result = []

    for original in files or []:
        item = dict(original)

        file_type = (
            item.get(
                "file_type"
            )
            or normalize_file_type(
                item.get(
                    "caption",
                    ""
                )
            )
        )

        item["file_type"] = file_type

        item["type_code"] = (
            item.get(
                "type_code"
            )
            or make_type_code(
                episode_key,
                file_type
            )
        )

        result.append(
            item
        )

    return result


def parse_caption(
    caption
):
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
        "series_name":
            series_name,
        "episode_number":
            episode_number,
        "episode_key":
            make_episode_key(
                series_name,
                episode_number
            )
    }


# ============================================================
# CACHE
# ============================================================

def rebuild_indexes(
    rows
):
    local_episodes = {}
    local_episode_tokens = {}
    local_type_index = {}

    for row in rows or []:
        key = row.get(
            "episode_key"
        )

        if not key:
            continue

        item = dict(row)

        files = enrich_files(
            item.get(
                "files"
            ) or [],
            key
        )

        item["files"] = files

        local_episodes[key] = item

        # New stable episode link
        local_episode_tokens[
            episode_token(key)
        ] = key

        for f in files:
            file_type = f.get(
                "file_type"
            )

            # New stable file link
            local_type_index[
                file_token(
                    key,
                    file_type
                )
            ] = (
                key,
                file_type
            )

            # Old stable t_ links
            old_code = f.get(
                "type_code"
            )

            if old_code:
                local_type_index[
                    old_code
                ] = (
                    key,
                    file_type
                )

            # Recreate old stable t_ format
            local_type_index[
                legacy_type_token(
                    key,
                    file_type
                )
            ] = (
                key,
                file_type
            )

    with CACHE_LOCK:
        EPISODES.clear()
        EPISODES.update(
            local_episodes
        )

        EPISODE_TOKEN_INDEX.clear()
        EPISODE_TOKEN_INDEX.update(
            local_episode_tokens
        )

        TYPE_INDEX.clear()
        TYPE_INDEX.update(
            local_type_index
        )


def sync_cache():
    global SPONSORS
    global BOT_USERNAME
    global CACHE_READY

    try:
        episode_future = FAST_EXECUTOR.submit(
            lambda: (
                supabase
                .table("episodes")
                .select("*")
                .execute()
            )
        )

        sponsor_future = FAST_EXECUTOR.submit(
            lambda: (
                supabase
                .table("sponsors")
                .select("*")
                .order("id")
                .execute()
            )
        )

        episodes_result = (
            episode_future.result()
        )

        sponsors_result = (
            sponsor_future.result()
        )

        rebuild_indexes(
            episodes_result.data or []
        )

        username = BOT_USERNAME

        if not username:
            me = get_me()

            username = (
                me
                .get("result", {})
                .get("username")
            )

        with CACHE_LOCK:
            SPONSORS = (
                sponsors_result.data
                or []
            )

            BOT_USERNAME = username
            CACHE_READY = True

        print(
            "CACHE OK:",
            len(EPISODES),
            "episodes |",
            len(TYPE_INDEX),
            "file links |",
            len(SPONSORS),
            "sponsors"
        )

        return True

    except Exception as e:
        log_error(
            "sync_cache",
            e
        )
        return False


def cache_loop():
    while True:
        try:
            time.sleep(
                CACHE_SYNC_INTERVAL
            )

            flush_users()

            sync_cache()

        except Exception as e:
            log_error(
                "cache_loop",
                e
            )


# ============================================================
# EPISODE DATABASE
# ============================================================

def get_episode(
    episode_key
):
    # Normal user path = RAM only.
    with CACHE_LOCK:
        cached = EPISODES.get(
            episode_key
        )

    if cached:
        return cached

    # Fallback only if cache doesn't contain it.
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

        if result.data:
            row = result.data[0]

            rebuild_indexes(
                list(EPISODES.values())
                + [row]
            )

            return row

    except Exception as e:
        log_error(
            "get_episode",
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
        "episode_key":
            episode_key,
        "series_name":
            series_name,
        "episode_number":
            episode_number,
        "files":
            files
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
            EPISODES[
                episode_key
            ] = payload

            EPISODE_TOKEN_INDEX[
                episode_token(
                    episode_key
                )
            ] = episode_key

            for f in files:
                ftype = f.get(
                    "file_type"
                )

                TYPE_INDEX[
                    file_token(
                        episode_key,
                        ftype
                    )
                ] = (
                    episode_key,
                    ftype
                )

                TYPE_INDEX[
                    legacy_type_token(
                        episode_key,
                        ftype
                    )
                ] = (
                    episode_key,
                    ftype
                )

                if f.get(
                    "type_code"
                ):
                    TYPE_INDEX[
                        f["type_code"]
                    ] = (
                        episode_key,
                        ftype
                    )

        return result

    except Exception as e:
        log_error(
            "save_episode",
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

            EPISODE_TOKEN_INDEX.pop(
                episode_token(
                    episode_key
                ),
                None
            )

            for token, value in list(
                TYPE_INDEX.items()
            ):
                if (
                    value
                    and value[0]
                    == episode_key
                ):
                    TYPE_INDEX.pop(
                        token,
                        None
                    )

        return result

    except Exception as e:
        log_error(
            "delete_episode",
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
            EPISODE_TOKEN_INDEX.clear()
            TYPE_INDEX.clear()

        return result

    except Exception as e:
        log_error(
            "delete_all_episodes",
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
    with PENDING_LOCK:
        PENDING[
            int(user_id)
        ] = value


def get_pending(
    user_id
):
    with PENDING_LOCK:
        return PENDING.get(
            int(user_id)
        )


def clear_pending(
    user_id
):
    with PENDING_LOCK:
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
                    "chat_id":
                        chat_id,
                    "title":
                        title,
                    "url":
                        url
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
        log_error(
            "add_sponsor",
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
                x
                for x in SPONSORS
                if str(
                    x.get("id")
                ) != str(
                    sponsor_id
                )
            ]

        return result

    except Exception as e:
        log_error(
            "remove_sponsor",
            e
        )
        return None


# ============================================================
# USERS / USER STATISTICS
# ============================================================

def touch_user(
    user
):
    if not user:
        return

    try:
        uid = int(
            user.get("id")
        )
    except Exception:
        return

    if uid == ADMIN_ID:
        return

    current_time = now_utc()

    with USER_LOCK:
        existing = BOT_USERS.get(
            uid
        )

        if existing:
            existing["last_seen"] = (
                current_time
            )

            existing["username"] = (
                user.get("username")
                or ""
            )

            existing["first_name"] = (
                user.get("first_name")
                or ""
            )

            existing["last_name"] = (
                user.get("last_name")
                or ""
            )

            DIRTY_USERS.add(
                uid
            )

            return

        BOT_USERS[uid] = {
            "user_id":
                uid,
            "first_seen":
                current_time,
            "last_seen":
                current_time,
            "username":
                user.get("username")
                or "",
            "first_name":
                user.get("first_name")
                or "",
            "last_name":
                user.get("last_name")
                or "",
            "is_blocked":
                False
        }

        DIRTY_USERS.add(
            uid
        )

    # New user is persisted immediately in background.
    DB_EXECUTOR.submit(
        insert_user_if_new,
        uid
    )


def insert_user_if_new(
    user_id
):
    with USER_LOCK:
        user = BOT_USERS.get(
            user_id
        )

    if not user:
        return

    try:
        (
            supabase
            .table("bot_users")
            .upsert(
                {
                    "user_id":
                        user_id,
                    "first_name":
                        user.get(
                            "first_name",
                            ""
                        ),
                    "last_name":
                        user.get(
                            "last_name",
                            ""
                        ),
                    "username":
                        user.get(
                            "username",
                            ""
                        ),
                    "is_blocked":
                        False,
                    "created_at":
                        user[
                            "first_seen"
                        ].isoformat(),
                    "last_seen":
                        user[
                            "last_seen"
                        ].isoformat()
                },
                on_conflict="user_id"
            )
            .execute()
        )

        with USER_LOCK:
            DIRTY_USERS.discard(
                user_id
            )

    except Exception as e:
        log_error(
            "insert_user",
            e
        )


def flush_users():
    with USER_LOCK:
        ids = list(
            DIRTY_USERS
        )

    if not ids:
        return

    for uid in ids:
        with USER_LOCK:
            user = BOT_USERS.get(
                uid
            )

        if not user:
            continue

        try:
            (
                supabase
                .table("bot_users")
                .update(
                    {
                        "first_name":
                            user.get(
                                "first_name",
                                ""
                            ),
                        "last_name":
                            user.get(
                                "last_name",
                                ""
                            ),
                        "username":
                            user.get(
                                "username",
                                ""
                            ),
                        "last_seen":
                            user[
                                "last_seen"
                            ].isoformat()
                    }
                )
                .eq(
                    "user_id",
                    uid
                )
                .execute()
            )

            with USER_LOCK:
                DIRTY_USERS.discard(
                    uid
                )

        except Exception as e:
            log_error(
                "flush_user",
                e
            )


def sync_users_cache():
    try:
        result = (
            supabase
            .table("bot_users")
            .select("*")
            .execute()
        )

        rows = result.data or []

        with USER_LOCK:
            for row in rows:
                try:
                    uid = int(
                        row.get(
                            "user_id"
                        )
                    )
                except Exception:
                    continue

                created = (
                    parse_dt(
                        row.get(
                            "created_at"
                        )
                    )
                    or now_utc()
                )

                last_seen = (
                    parse_dt(
                        row.get(
                            "last_seen"
                        )
                    )
                    or created
                )

                local = BOT_USERS.get(
                    uid
                )

                if local:
                    local["first_seen"] = min(
                        local[
                            "first_seen"
                        ],
                        created
                    )

                    local["last_seen"] = max(
                        local[
                            "last_seen"
                        ],
                        last_seen
                    )

                else:
                    BOT_USERS[uid] = {
                        "user_id":
                            uid,
                        "first_seen":
                            created,
                        "last_seen":
                            last_seen,
                        "username":
                            row.get(
                                "username",
                                ""
                            ),
                        "first_name":
                            row.get(
                                "first_name",
                                ""
                            ),
                        "last_name":
                            row.get(
                                "last_name",
                                ""
                            ),
                        "is_blocked":
                            bool(
                                row.get(
                                    "is_blocked",
                                    False
                                )
                            )
                    }

    except Exception as e:
        log_error(
            "sync_users_cache",
            e
        )


def get_user_stats():
    """
    آمار اصلی دقیقاً از bot_users خوانده می‌شود.
    اگر Supabase لحظه‌ای مشکل داشت، از کش RAM استفاده می‌شود.
    """

    current = now_utc()

    today_start = current.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    month_start = current.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    rows = None

    try:
        result = (
            supabase
            .table("bot_users")
            .select(
                "user_id,created_at,last_seen,is_blocked"
            )
            .execute()
        )

        rows = result.data or []

    except Exception as e:
        log_error(
            "get_user_stats",
            e
        )

    total = 0
    active_today = 0
    active_month = 0
    new_month = 0

    if rows is not None:

        for row in rows:

            if row.get(
                "is_blocked",
                False
            ):
                continue

            total += 1

            last_seen = parse_dt(
                row.get(
                    "last_seen"
                )
            )

            created_at = parse_dt(
                row.get(
                    "created_at"
                )
            )

            if (
                last_seen
                and last_seen >= today_start
            ):
                active_today += 1

            if (
                last_seen
                and last_seen >= month_start
            ):
                active_month += 1

            if (
                created_at
                and created_at >= month_start
            ):
                new_month += 1

    else:

        with USER_LOCK:
            local_users = list(
                BOT_USERS.values()
            )

        for user in local_users:

            if user.get(
                "is_blocked",
                False
            ):
                continue

            total += 1

            if (
                user.get("last_seen")
                and user[
                    "last_seen"
                ] >= today_start
            ):
                active_today += 1

            if (
                user.get("last_seen")
                and user[
                    "last_seen"
                ] >= month_start
            ):
                active_month += 1

            if (
                user.get("first_seen")
                and user[
                    "first_seen"
                ] >= month_start
            ):
                new_month += 1

    return {
        "total":
            total,
        "active_today":
            active_today,
        "active_month":
            active_month,
        "new_month":
            new_month,
        "month":
            f"{current.year:04d}/{current.month:02d}"
    }


def stats_text():
    stats = get_user_stats()

    return (
        "📊 آمار ربات\n\n"
        f"👥 کل کاربران: "
        f"{stats['total']}\n\n"
        f"☀️ کاربران فعال امروز: "
        f"{stats['active_today']}\n\n"
        f"📅 کاربران فعال این ماه "
        f"({stats['month']}): "
        f"{stats['active_month']}\n\n"
        f"🆕 کاربران جدید این ماه: "
        f"{stats['new_month']}"
    )


# ============================================================
# BOT EVENTS / DOWNLOAD STATS
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
                    "user_id":
                        int(user_id),
                    "event_type":
                        event_type,
                    "episode_key":
                        episode_key,
                    "file_type":
                        file_type,
                    "file_count":
                        int(
                            file_count
                            or 0
                        )
                }
            )
            .execute()
        )

    except Exception as e:
        log_error(
            "record_stat",
            e
        )


def advanced_stats():
    now = now_utc()

    today_start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    month_start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    last_30 = (
        now
        - timedelta(
            days=30
        )
    )

    result = {
        "downloads_today": 0,
        "downloads_month": 0,
        "downloads_all": 0,
        "files_month": 0,
        "start_today": 0,
        "start_month": 0,
        "broadcast_ok": 0,
        "broadcast_fail": 0,
        "top": {}
    }

    try:
        rows = (
            supabase
            .table("bot_stats")
            .select("*")
            .execute()
            .data
            or []
        )

        for row in rows:

            event = row.get(
                "event_type"
            )

            created = parse_dt(
                row.get(
                    "created_at"
                )
            )

            count = int(
                row.get(
                    "file_count"
                )
                or 0
            )

            if event == "start":

                if (
                    created
                    and created >= today_start
                ):
                    result[
                        "start_today"
                    ] += 1

                if (
                    created
                    and created >= month_start
                ):
                    result[
                        "start_month"
                    ] += 1

            elif event == "download":

                result[
                    "downloads_all"
                ] += count

                if (
                    created
                    and created >= today_start
                ):
                    result[
                        "downloads_today"
                    ] += count

                if (
                    created
                    and created >= month_start
                ):
                    result[
                        "downloads_month"
                    ] += count

                    result[
                        "files_month"
                    ] += count

                if (
                    created
                    and created >= last_30
                ):
                    key = row.get(
                        "episode_key"
                    )

                    if key:
                        result[
                            "top"
                        ][key] = (
                            result[
                                "top"
                            ].get(
                                key,
                                0
                            )
                            + count
                        )

            elif event == "broadcast_success":
                result[
                    "broadcast_ok"
                ] += 1

            elif event == "broadcast_fail":
                result[
                    "broadcast_fail"
                ] += 1

    except Exception as e:
        log_error(
            "advanced_stats",
            e
        )

    result["top"] = sorted(
        result["top"].items(),
        key=lambda x: x[1],
        reverse=True
    )[:10]

    return result


# ============================================================
# MEMBERSHIP CACHE
# ============================================================

def member_status(
    chat_id,
    user_id,
    force=False
):
    key = (
        str(chat_id),
        int(user_id)
    )

    current_time = time.time()

    if not force:

        with MEMBERSHIP_LOCK:
            cached = MEMBERSHIP_CACHE.get(
                key
            )

        if (
            cached
            and current_time - cached[1]
            < MEMBERSHIP_CACHE_TTL
        ):
            return cached[0]

    result = get_chat_member(
        chat_id,
        user_id
    )

    status = (
        result
        .get(
            "result",
            {}
        )
        .get(
            "status",
            ""
        )
    )

    ok = (
        result.get("ok")
        and status in (
            "creator",
            "administrator",
            "member"
        )
    )

    with MEMBERSHIP_LOCK:
        MEMBERSHIP_CACHE[
            key
        ] = (
            bool(ok),
            current_time
        )

    return bool(ok)


def check_membership(
    user_id,
    force=False
):
    sponsors = get_sponsors()

    targets = [
        (
            CHANNEL_ID,
            None
        )
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
            FAST_EXECUTOR.submit(
                member_status,
                chat_id,
                user_id,
                force
            )
        )

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
        results[0]
        if results
        else False
    )

    missing = []

    for i in range(
        1,
        len(targets)
    ):
        if not results[i]:
            if targets[i][1]:
                missing.append(
                    targets[i][1]
                )

    return (
        main_ok,
        missing
    )


# ============================================================
# CHANNEL POSTS
# ============================================================

def save_channel_post(
    message_id
):
    try:
        (
            supabase
            .table("channel_posts")
            .upsert(
                {
                    "message_id":
                        int(message_id),
                    "created_at":
                        now_utc().isoformat()
                },
                on_conflict="message_id"
            )
            .execute()
        )

    except Exception as e:
        log_error(
            "save_channel_post",
            e
        )

    with LAST_CHANNEL_POSTS_LOCK:
        LAST_CHANNEL_POSTS.insert(
            0,
            int(message_id)
        )

        del LAST_CHANNEL_POSTS[5:]


def get_last_channel_posts():
    try:
        result = (
            supabase
            .table("channel_posts")
            .select(
                "message_id"
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(5)
            .execute()
        )

        posts = [
            int(
                x["message_id"]
            )
            for x in (
                result.data
                or []
            )
            if x.get(
                "message_id"
            )
        ]

        if posts:
            return posts

    except Exception:
        pass

    with LAST_CHANNEL_POSTS_LOCK:
        return list(
            LAST_CHANNEL_POSTS
        )


# ============================================================
# KEYBOARDS
# ============================================================

def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "🎬 مدیریت قسمت‌ها",
                    "callback_data":
                        "admin_episodes"
                },
                {
                    "text":
                        "📢 اسپانسرها",
                    "callback_data":
                        "admin_sponsors"
                }
            ],
            [
                {
                    "text":
                        "📢 پیام همگانی",
                    "callback_data":
                        "admin_broadcast"
                },
                {
                    "text":
                        "📊 آمار پیشرفته",
                    "callback_data":
                        "admin_stats"
                }
            ],
            [
                {
                    "text":
                        "📋 لیست قسمت‌ها",
                    "callback_data":
                        "admin_list"
                },
                {
                    "text":
                        "🗑 حذف قسمت",
                    "callback_data":
                        "admin_delete"
                }
            ],
            [
                {
                    "text":
                        "🗑 حذف همه قسمت‌ها",
                    "callback_data":
                        "admin_delete_all"
                }
            ],
            [
                {
                    "text":
                        "🔄 سینک دیتابیس",
                    "callback_data":
                        "admin_sync"
                },
                {
                    "text":
                        "⚡ وضعیت ربات",
                    "callback_data":
                        "admin_status"
                }
            ]
        ]
    }


def sponsor_keyboard(
    sponsors
):
    rows = []

    for sponsor in sponsors:

        title = (
            sponsor.get(
                "title"
            )
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
                        "url":
                            url
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
        "inline_keyboard":
            rows
    }


def main_channel_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "عضویت در کانال اصلی 📺",
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
    rows = []

    posts = get_last_channel_posts()

    if posts:

        for i, message_id in enumerate(
            posts[:5],
            1
        ):
            rows.append(
                [
                    {
                        "text":
                            f"📢 پست {i}",
                        "url":
                            f"{CHANNEL_URL}/{message_id}"
                    }
                ]
            )

    else:
        rows.append(
            [
                {
                    "text":
                        "📢 مشاهده ۵ پست آخر",
                    "url":
                        CHANNEL_URL
                }
            ]
        )

    rows.append(
        [
            {
                "text":
                    "انجام شد ✅",
                "callback_data":
                    "check_reactions"
            }
        ]
    )

    return {
        "inline_keyboard":
            rows
    }


# ============================================================
# JOIN / REACTION FLOW
# ============================================================

def show_join_page(
    chat_id
):
    sponsors = get_sponsors()

    if sponsors:

        send_message(
            chat_id,
            "📣 برای استفاده از ربات و دریافت فایل:\n\n"
            "1️⃣ ابتدا عضو کانال‌های زیر بشید\n"
            "2️⃣ بعد روی «عضو شدم ✅» بزنید",
            sponsor_keyboard(
                sponsors
            )
        )

    else:

        send_message(
            chat_id,
            "📣 برای دریافت فایل ابتدا عضو کانال اصلی بشید.",
            main_channel_keyboard()
        )


def show_reaction_page(
    chat_id
):
    send_message(
        chat_id,
        "لطفا جهت دریافت فایل ابتدا ۵ پست اخیر کانال "
        "@altiustuistsnbol را ری‌اکت بزنید و سپس برگردید "
        "و دکمه «انجام شد» را کلیک کنید ♥️",
        reaction_keyboard()
    )


# ============================================================
# DELIVERY
# ============================================================

def claim_delivery(
    user_id
):
    uid = int(
        user_id
    )

    with DELIVERY_LOCK:

        if uid in DELIVERING_USERS:
            return False

        DELIVERING_USERS.add(
            uid
        )

    return True


def release_delivery(
    user_id
):
    with DELIVERY_LOCK:
        DELIVERING_USERS.discard(
            int(user_id)
        )


def send_episode(
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
                "❌ لینک قسمت پیدا نشد."
            )
            return

        lookup_key = pending
        selected_type = None

        if pending.startswith(
            "TYPE::"
        ):

            parts = pending.split(
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
                "❌ این قسمت پیدا نشد یا حذف شده."
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
                )
                == selected_type
            ]

        if not files:
            clear_pending(
                user_id
            )

            send_message(
                chat_id,
                "❌ فایل این قسمت پیدا نشد."
            )
            return

        clear_pending(
            user_id
        )

        # ----------------------------------------------------
        # 1. FIRST SEND FILES
        # ----------------------------------------------------

        futures = []

        for file_info in files:

            futures.append(
                MEDIA_EXECUTOR.submit(
                    send_media_file,
                    chat_id,
                    file_info
                )
            )

        sent_ids = []

        for future in futures:

            try:

                result = future.result()

                if result.get(
                    "ok"
                ):

                    message_id = (
                        result
                        .get(
                            "result",
                            {}
                        )
                        .get(
                            "message_id"
                        )
                    )

                    if message_id:
                        sent_ids.append(
                            message_id
                        )

            except Exception as e:
                log_error(
                    "send_media",
                    e
                )

        if not sent_ids:

            send_message(
                chat_id,
                "❌ ارسال فایل انجام نشد. چند لحظه بعد دوباره امتحان کن."
            )

            return

        # ----------------------------------------------------
        # 2. THEN SEND WARNING
        # ----------------------------------------------------

        if selected_type:

            redownload = file_link(
                lookup_key,
                selected_type
            )

        else:

            redownload = episode_link(
                lookup_key
            )

        markup = None

        if redownload:

            markup = {
                "inline_keyboard": [
                    [
                        {
                            "text":
                                "🔄 دریافت مجدد",
                            "url":
                                redownload
                        }
                    ]
                ]
            }

        send_message(
            chat_id,
            f"⚠️ توجه:\n\n"
            f"فایل‌های ارسالی بعد از "
            f"{DELETE_AFTER} ثانیه حذف می‌شوند.\n"
            f"قبل از تمام شدن زمان، فایل‌ها را ذخیره کن.",
            markup
        )

        # Statistics never block delivery.
        FAST_EXECUTOR.submit(
            record_stat,
            user_id,
            "download",
            lookup_key,
            selected_type,
            len(sent_ids)
        )

        # ----------------------------------------------------
        # 3. DELETE ONLY FILE MESSAGES
        # ----------------------------------------------------

        threading.Thread(
            target=delete_files_later,
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


def delete_files_later(
    chat_id,
    message_ids
):
    time.sleep(
        DELETE_AFTER
    )

    futures = []

    for message_id in message_ids:

        futures.append(
            FAST_EXECUTOR.submit(
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

    # خیلی مهم:
    # بعد از حذف فایل هیچ پیام جدیدی ارسال نمی‌شود.


# ============================================================
# FILE UPLOAD / ADMIN
# ============================================================

def extract_file(
    message
):
    caption = (
        message.get(
            "caption",
            ""
        )
    )

    entities = (
        message.get(
            "caption_entities"
        )
        or []
    )

    if message.get(
        "video"
    ):

        return {
            "type":
                "video",
            "file_id":
                message[
                    "video"
                ].get(
                    "file_id"
                ),
            "caption":
                caption,
            "caption_entities":
                entities
        }

    if message.get(
        "document"
    ):

        return {
            "type":
                "document",
            "file_id":
                message[
                    "document"
                ].get(
                    "file_id"
                ),
            "caption":
                caption,
            "caption_entities":
                entities
        }

    if message.get(
        "audio"
    ):

        return {
            "type":
                "audio",
            "file_id":
                message[
                    "audio"
                ].get(
                    "file_id"
                ),
            "caption":
                caption,
            "caption_entities":
                entities
        }

    if message.get(
        "photo"
    ):

        return {
            "type":
                "photo",
            "file_id":
                message[
                    "photo"
                ][-1].get(
                    "file_id"
                ),
            "caption":
                caption,
            "caption_entities":
                entities
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

    preview = is_preview_caption(
        caption
    )

    if preview:

        episode_key = (
            make_preview_key(
                parsed[
                    "series_name"
                ],
                parsed[
                    "episode_number"
                ]
            )
        )

    else:

        episode_key = (
            parsed[
                "episode_key"
            ]
        )

    existing = get_episode(
        episode_key
    )

    files = (
        existing.get(
            "files"
        )
        if existing
        else []
    )

    files = list(
        files or []
    )

    file_type = normalize_file_type(
        caption
    )

    file_info[
        "file_type"
    ] = file_type

    file_info[
        "type_code"
    ] = make_type_code(
        episode_key,
        file_type
    )

    files.append(
        file_info
    )

    saved = save_episode(
        episode_key,
        parsed[
            "series_name"
        ],
        parsed[
            "episode_number"
        ],
        files
    )

    if saved is None:

        send_message(
            ADMIN_ID,
            "❌ ذخیره در Supabase انجام نشد."
        )

        return True

    label = (
        "پیش‌نمایش"
        if preview
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

    types_added = []

    for f in enrich_files(
        files,
        episode_key
    ):

        ftype = f.get(
            "file_type"
        )

        if ftype in types_added:
            continue

        types_added.append(
            ftype
        )

        lines.append(
            f"🎬 {ftype}"
        )

        lines.append(
            file_link(
                episode_key,
                ftype
            )
        )

        lines.append("")

    lines.append(
        f"🔗 لینک کل {label}:"
    )

    lines.append(
        episode_link(
            episode_key
        )
    )

    send_message(
        ADMIN_ID,
        "\n".join(
            lines
        )
    )

    FAST_EXECUTOR.submit(
        record_stat,
        ADMIN_ID,
        "preview"
        if preview
        else "upload",
        episode_key,
        None,
        1
    )

    return True


# ============================================================
# TYPE TOKEN LOOKUP
# ============================================================

def resolve_type_token(
    token
):
    with CACHE_LOCK:

        result = TYPE_INDEX.get(
            token
        )

        if result:
            return result

    # One fallback database scan only when token isn't in RAM.
    try:

        rows = (
            supabase
            .table("episodes")
            .select(
                "episode_key,files"
            )
            .execute()
            .data
            or []
        )

        rebuild_indexes(
            rows
        )

        with CACHE_LOCK:

            return TYPE_INDEX.get(
                token,
                (
                    None,
                    None
                )
            )

    except Exception as e:

        log_error(
            "resolve_type_token",
            e
        )

        return (
            None,
            None
        )


# ============================================================
# ADMIN COMMANDS
# ============================================================

def admin_status_text():
    with CACHE_LOCK:
        episodes = len(
            EPISODES
        )

        type_links = len(
            TYPE_INDEX
        )

        sponsors = len(
            SPONSORS
        )

        ready = CACHE_READY

    return (
        "⚡ وضعیت ربات\n\n"
        f"Cache: "
        f"{'🟢 آماده' if ready else '🔴 آماده نیست'}\n"
        f"🎬 قسمت‌ها: {episodes}\n"
        f"🔗 لینک‌ها: {type_links}\n"
        f"📢 اسپانسرها: {sponsors}\n"
        f"🔄 سینک: هر {CACHE_SYNC_INTERVAL} ثانیه"
    )


def list_episodes_text():
    with CACHE_LOCK:
        rows = list(
            EPISODES.values()
        )

    rows = [
        x
        for x in rows
        if not str(
            x.get(
                "episode_key",
                ""
            )
        ).startswith(
            "preview__"
        )
    ]

    rows.sort(
        key=lambda x: (
            str(
                x.get(
                    "series_name",
                    ""
                )
            ),
            int(
                x.get(
                    "episode_number",
                    0
                )
            )
        )
    )

    if not rows:
        return "📋 هیچ قسمتی ذخیره نشده."

    lines = [
        "📋 لیست قسمت‌ها:",
        ""
    ]

    for row in rows:

        key = row.get(
            "episode_key"
        )

        lines.append(
            f"🎬 {row.get('series_name')} "
            f"— قسمت {row.get('episode_number')}"
        )

        lines.append(
            f"🔑 {key}"
        )

        lines.append(
            f"🔗 {episode_link(key)}"
        )

        lines.append("")

    return "\n".join(
        lines
    )


def sponsors_text():
    sponsors = get_sponsors()

    if not sponsors:
        return "📢 هیچ اسپانسری ثبت نشده."

    lines = [
        "📢 اسپانسرها:",
        ""
    ]

    for sponsor in sponsors:

        lines.append(
            f"🆔 ID: {sponsor.get('id')}"
        )

        lines.append(
            f"📺 {sponsor.get('title')}"
        )

        lines.append(
            f"🔗 {sponsor.get('url')}"
        )

        lines.append("")

    return "\n".join(
        lines
    )


def handle_admin_text(
    chat_id,
    text
):
    text = (
        text or ""
    ).strip()

    if text == "/start":

        send_message(
            chat_id,
            "پنل مدیریت ربات فعال است ✅",
            admin_keyboard()
        )

        return True

    if text == "/sponsors":

        send_message(
            chat_id,
            sponsors_text(),
            admin_keyboard()
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
            for x in raw.split(
                "|"
            )
        ]

        if len(parts) != 3:

            send_message(
                chat_id,
                "فرمت درست:\n\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )

            return True

        result = add_sponsor(
            parts[0],
            parts[1],
            parts[2]
        )

        send_message(
            chat_id,
            "✅ اسپانسر اضافه شد."
            if result is not None
            else
            "❌ ذخیره اسپانسر انجام نشد.",
            admin_keyboard()
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
            int(
                parts[1]
            )
        )

        send_message(
            chat_id,
            "✅ اسپانسر حذف شد."
            if result is not None
            else
            "❌ حذف اسپانسر انجام نشد.",
            admin_keyboard()
        )

        return True

    if text == "/episodes":

        send_message(
            chat_id,
            list_episodes_text(),
            admin_keyboard()
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

        result = delete_episode(
            parts[1].strip()
        )

        send_message(
            chat_id,
            "✅ قسمت حذف شد."
            if result is not None
            else
            "❌ حذف قسمت انجام نشد.",
            admin_keyboard()
        )

        return True

    if text == "/delete_all":

        result = delete_all_episodes()

        with PENDING_LOCK:
            PENDING.clear()

        send_message(
            chat_id,
            "✅ همه قسمت‌ها از دیتابیس و کش حذف شدند."
            if result is not None
            else
            "❌ حذف همه قسمت‌ها انجام نشد.",
            admin_keyboard()
        )

        return True

    if text == "/sync":

        ok = sync_cache()

        send_message(
            chat_id,
            "✅ دیتابیس و کش سینک شدند."
            if ok
            else
            "❌ سینک انجام نشد.",
            admin_keyboard()
        )

        return True

    if text == "/status":

        send_message(
            chat_id,
            admin_status_text(),
            admin_keyboard()
        )

        return True

    if text == "/stats":

        send_message(
            chat_id,
            stats_text(),
            admin_keyboard()
        )

        return True

    if text == "/advanced_stats":

        stats = advanced_stats()

        lines = [
            "📊 آمار پیشرفته",
            "",
            f"📥 دانلود امروز: "
            f"{stats['downloads_today']}",
            f"📥 دانلود این ماه: "
            f"{stats['downloads_month']}",
            f"📥 دانلود کل: "
            f"{stats['downloads_all']}",
            f"📦 فایل‌های ارسال‌شده این ماه: "
            f"{stats['files_month']}",
            f"▶️ /start امروز: "
            f"{stats['start_today']}",
            f"▶️ /start این ماه: "
            f"{stats['start_month']}",
            f"📢 Broadcast موفق: "
            f"{stats['broadcast_ok']}",
            f"📢 Broadcast ناموفق: "
            f"{stats['broadcast_fail']}",
            "",
            "🔥 ۱۰ قسمت برتر ۳۰ روز اخیر:"
        ]

        if stats["top"]:

            for key, count in stats[
                "top"
            ]:

                episode = get_episode(
                    key
                )

                if episode:

                    lines.append(
                        f"• "
                        f"{episode.get('series_name')} "
                        f"— قسمت "
                        f"{episode.get('episode_number')}: "
                        f"{count}"
                    )

                else:

                    lines.append(
                        f"• {key}: {count}"
                    )

        else:

            lines.append(
                "• هنوز آماری ثبت نشده"
            )

        send_message(
            chat_id,
            "\n".join(
                lines
            ),
            admin_keyboard()
        )

        return True

    if text == "/broadcast":

        with ADMIN_STATE_LOCK:
            ADMIN_STATE[
                ADMIN_ID
            ] = "broadcast"

        send_message(
            chat_id,
            "📢 پیام همگانی فعال شد.\n\n"
            "حالا متن، عکس، ویدیو یا فایل موردنظر رو بفرست.\n\n"
            "برای لغو:\n"
            "/cancel_broadcast"
        )

        return True

    if text == "/cancel_broadcast":

        with ADMIN_STATE_LOCK:
            ADMIN_STATE.pop(
                ADMIN_ID,
                None
            )

        send_message(
            chat_id,
            "❌ پیام همگانی لغو شد.",
            admin_keyboard()
        )

        return True

    return False


# ============================================================
# BROADCAST
# ============================================================

def mark_blocked(
    user_id
):
    try:

        (
            supabase
            .table("bot_users")
            .update(
                {
                    "is_blocked":
                        True
                }
            )
            .eq(
                "user_id",
                int(user_id)
            )
            .execute()
        )

    except Exception as e:
        log_error(
            "mark_blocked",
            e
        )

    with USER_LOCK:

        if user_id in BOT_USERS:
            BOT_USERS[
                user_id
            ][
                "is_blocked"
            ] = True


def broadcast_worker(
    message
):
    try:

        result = (
            supabase
            .table("bot_users")
            .select(
                "user_id,is_blocked"
            )
            .eq(
                "is_blocked",
                False
            )
            .execute()
        )

        users = result.data or []

    except Exception as e:

        send_message(
            ADMIN_ID,
            "❌ دریافت کاربران برای پیام همگانی انجام نشد."
        )

        log_error(
            "broadcast_users",
            e
        )

        return

    success = 0
    failed = 0

    source_chat_id = (
        message
        .get(
            "chat",
            {}
        )
        .get(
            "id"
        )
    )

    message_id = message.get(
        "message_id"
    )

    for row in users:

        user_id = row.get(
            "user_id"
        )

        if not user_id:
            continue

        result = tg(
            "copyMessage",
            {
                "chat_id":
                    int(user_id),
                "from_chat_id":
                    int(
                        source_chat_id
                    ),
                "message_id":
                    int(
                        message_id
                    )
            },
            timeout=20
        )

        if result.get(
            "ok"
        ):

            success += 1

            FAST_EXECUTOR.submit(
                record_stat,
                user_id,
                "broadcast_success",
                None,
                None,
                1
            )

        else:

            failed += 1

            if result.get(
                "error_code"
            ) == 403:

                mark_blocked(
                    int(user_id)
                )

            FAST_EXECUTOR.submit(
                record_stat,
                user_id,
                "broadcast_fail",
                None,
                None,
                1
            )

        # Telegram rate-limit safety
        time.sleep(
            0.06
        )

    send_message(
        ADMIN_ID,
        "📢 پیام همگانی تمام شد.\n\n"
        f"✅ موفق: {success}\n"
        f"❌ ناموفق: {failed}",
        admin_keyboard()
    )


# ============================================================
# ADMIN CALLBACKS
# ============================================================

def handle_admin_callback(
    callback_id,
    chat_id,
    data
):
    answer_callback(
        callback_id
    )

    if data == "admin_episodes":

        send_message(
            chat_id,
            "🎬 مدیریت قسمت‌ها\n\n"
            "/episodes\n"
            "/delete_episode EPISODE_KEY\n"
            "/delete_all",
            admin_keyboard()
        )

        return

    if data == "admin_sponsors":

        send_message(
            chat_id,
            sponsors_text()
            + "\n\n"
            "برای افزودن:\n"
            "/add_sponsor @channel | نام کانال | https://t.me/channel\n\n"
            "برای حذف:\n"
            "/remove_sponsor ID",
            admin_keyboard()
        )

        return

    if data == "admin_broadcast":

        with ADMIN_STATE_LOCK:
            ADMIN_STATE[
                ADMIN_ID
            ] = "broadcast"

        send_message(
            chat_id,
            "📢 محتوای پیام همگانی رو بفرست.\n"
            "متن، عکس، ویدیو یا فایل.\n\n"
            "لغو:\n"
            "/cancel_broadcast"
        )

        return

    if data == "admin_stats":

        send_message(
            chat_id,
            stats_text(),
            admin_keyboard()
        )

        return

    if data == "admin_list":

        send_message(
            chat_id,
            list_episodes_text(),
            admin_keyboard()
        )

        return

    if data == "admin_delete":

        send_message(
            chat_id,
            "برای حذف یک قسمت بفرست:\n"
            "/delete_episode EPISODE_KEY",
            admin_keyboard()
        )

        return

    if data == "admin_delete_all":

        send_message(
            chat_id,
            "⚠️ مطمئنی می‌خوای همه قسمت‌ها حذف بشن؟",
            {
                "inline_keyboard": [
                    [
                        {
                            "text":
                                "بله، حذف همه 🗑",
                            "callback_data":
                                "admin_delete_all_yes"
                        },
                        {
                            "text":
                                "لغو",
                            "callback_data":
                                "admin_cancel"
                        }
                    ]
                ]
            }
        )

        return

    if data == "admin_delete_all_yes":

        delete_all_episodes()

        with PENDING_LOCK:
            PENDING.clear()

        send_message(
            chat_id,
            "✅ همه قسمت‌ها حذف شدند.",
            admin_keyboard()
        )

        return

    if data == "admin_sync":

        ok = sync_cache()

        send_message(
            chat_id,
            "✅ سینک انجام شد."
            if ok
            else
            "❌ سینک ناموفق بود.",
            admin_keyboard()
        )

        return

    if data == "admin_status":

        send_message(
            chat_id,
            admin_status_text(),
            admin_keyboard()
        )

        return

    if data == "admin_cancel":

        send_message(
            chat_id,
            "❌ لغو شد.",
            admin_keyboard()
        )

        return


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
            "ok":
                True,
            "cache":
                CACHE_READY,
            "episodes":
                len(EPISODES)
        }
    )


@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = (
        request.get_json(
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
            chat.get(
                "username"
            )
            or ""
        ).lower()

        expected = (
            CHANNEL_ID
            .replace(
                "@",
                ""
            )
            .lower()
        )

        if username == expected:

            message_id = (
                channel_post.get(
                    "message_id"
                )
            )

            if message_id:

                FAST_EXECUTOR.submit(
                    save_channel_post,
                    message_id
                )

        return jsonify(
            {
                "ok":
                    True
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

        user = message.get(
            "from",
            {}
        )

        user_id = user.get(
            "id"
        )

        text = message.get(
            "text",
            ""
        )

        # User statistics are updated without slowing down
        # the actual Telegram response.
        if (
            user_id
            and user_id != ADMIN_ID
        ):

            FAST_EXECUTOR.submit(
                touch_user,
                user
            )

        # ----------------------------------------------------
        # ADMIN BROADCAST
        # ----------------------------------------------------

        if user_id == ADMIN_ID:

            with ADMIN_STATE_LOCK:
                state = ADMIN_STATE.get(
                    ADMIN_ID
                )

            if (
                state == "broadcast"
                and not (
                    text
                    in (
                        "/cancel_broadcast",
                        "/cancel"
                    )
                )
            ):

                with ADMIN_STATE_LOCK:
                    ADMIN_STATE.pop(
                        ADMIN_ID,
                        None
                    )

                send_message(
                    chat_id,
                    "📢 ارسال پیام همگانی شروع شد.\n"
                    "نتیجه بعد از پایان اعلام می‌شود."
                )

                FAST_EXECUTOR.submit(
                    broadcast_worker,
                    dict(
                        message
                    )
                )

                return jsonify(
                    {
                        "ok":
                            True
                    }
                )

        # ----------------------------------------------------
        # ADMIN MEDIA
        # ----------------------------------------------------

        if user_id == ADMIN_ID:

            if (
                message.get(
                    "video"
                )
                or message.get(
                    "document"
                )
                or message.get(
                    "audio"
                )
                or message.get(
                    "photo"
                )
            ):

                handle_admin_file(
                    message
                )

                return jsonify(
                    {
                        "ok":
                            True
                    }
                )

            if text:

                if handle_admin_text(
                    chat_id,
                    text
                ):

                    return jsonify(
                        {
                            "ok":
                                True
                        }
                    )

        # ----------------------------------------------------
        # START / DEEP LINK
        # ----------------------------------------------------

        if text.startswith(
            "/start"
        ):

            parts = text.split(
                maxsplit=1
            )

            # Plain /start
            if len(parts) == 1:

                FAST_EXECUTOR.submit(
                    record_stat,
                    user_id,
                    "start",
                    None,
                    None,
                    0
                )

                if user_id == ADMIN_ID:

                    send_message(
                        chat_id,
                        "پنل مدیریت ربات فعال است ✅",
                        admin_keyboard()
                    )

                else:

                    send_message(
                        chat_id,
                        "سلام 👋\n"
                        "لینک قسمت موردنظرت رو باز کن "
                        "تا فایل برات ارسال بشه."
                    )

                return jsonify(
                    {
                        "ok":
                            True
                    }
                )

            token = parts[1].strip()

            episode_key = None
            selected_type = None

            # ------------------------------------------------
            # NEW FILE TOKEN
            # ------------------------------------------------

            if (
                token.startswith(
                    "f_"
                )
                or token.startswith(
                    "t_"
                )
            ):

                (
                    episode_key,
                    selected_type
                ) = resolve_type_token(
                    token
                )

                if not episode_key:

                    send_message(
                        chat_id,
                        "❌ لینک فایل پیدا نشد یا حذف شده."
                    )

                    return jsonify(
                        {
                            "ok":
                                True
                        }
                    )

                set_pending(
                    user_id,
                    "TYPE::"
                    + episode_key
                    + "::"
                    + selected_type
                )

            # ------------------------------------------------
            # NEW EPISODE TOKEN
            # ------------------------------------------------

            elif token.startswith(
                "e_"
            ):

                with CACHE_LOCK:
                    episode_key = (
                        EPISODE_TOKEN_INDEX.get(
                            token
                        )
                    )

                if not episode_key:

                    send_message(
                        chat_id,
                        "❌ لینک قسمت پیدا نشد یا حذف شده."
                    )

                    return jsonify(
                        {
                            "ok":
                                True
                        }
                    )

                set_pending(
                    user_id,
                    episode_key
                )

            # ------------------------------------------------
            # LEGACY / DIRECT KEY
            # ------------------------------------------------

            else:

                # اگر لینک قدیمی با کلید مستقیم وارد شده باشد
                episode_key = token

                if not get_episode(
                    episode_key
                ):

                    send_message(
                        chat_id,
                        "❌ این قسمت پیدا نشد یا حذف شده."
                    )

                    return jsonify(
                        {
                            "ok":
                                True
                        }
                    )

                set_pending(
                    user_id,
                    episode_key
                )

            FAST_EXECUTOR.submit(
                record_stat,
                user_id,
                "start",
                episode_key,
                selected_type,
                0
            )

            # ------------------------------------------------
            # IMPORTANT:
            # We DO NOT check membership here.
            # The user gets the join buttons immediately.
            # Membership APIs are only called after clicking.
            # This makes the first response much faster.
            # ------------------------------------------------

            if get_sponsors():

                show_join_page(
                    chat_id
                )

            else:

                # Main channel only
                send_message(
                    chat_id,
                    "📣 ابتدا عضو کانال اصلی شو و بعد «عضو شدم» رو بزن.",
                    main_channel_keyboard()
                )

            return jsonify(
                {
                    "ok":
                        True
                }
            )

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

        callback_user = callback.get(
            "from",
            {}
        )

        user_id = callback_user.get(
            "id"
        )

        callback_message = (
            callback.get(
                "message"
            )
            or {}
        )

        chat = (
            callback_message.get(
                "chat"
            )
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

        if (
            user_id
            and user_id != ADMIN_ID
        ):

            FAST_EXECUTOR.submit(
                touch_user,
                callback_user
            )

        # ----------------------------------------------------
        # ADMIN CALLBACK
        # ----------------------------------------------------

        if (
            user_id == ADMIN_ID
            and data.startswith(
                "admin_"
            )
        ):

            handle_admin_callback(
                callback_id,
                chat_id,
                data
            )

            return jsonify(
                {
                    "ok":
                        True
                }
            )

        # ----------------------------------------------------
        # CHECK JOIN
        # ----------------------------------------------------

        if data == "check_join":

            answer_callback(
                callback_id,
                "در حال بررسی عضویت..."
            )

            pending = get_pending(
                user_id
            )

            if not pending:

                send_message(
                    chat_id,
                    "❌ لینک قسمت پیدا نشد."
                )

                return jsonify(
                    {
                        "ok":
                            True
                    }
                )

            # Force refresh ONLY here.
            main_ok, missing = (
                check_membership(
                    user_id,
                    force=True
                )
            )

            if not main_ok:

                send_message(
                    chat_id,
                    "هنوز عضو کانال اصلی نیستی 👇",
                    main_channel_keyboard()
                )

                return jsonify(
                    {
                        "ok":
                            True
                    }
                )

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
                        "ok":
                            True
                    }
                )

            # Membership confirmed.
            if message_id:

                delete_message(
                    chat_id,
                    message_id
                )

            show_reaction_page(
                chat_id
            )

            return jsonify(
                {
                    "ok":
                        True
                }
            )

        # ----------------------------------------------------
        # REACTION CONFIRMATION
        # ----------------------------------------------------

        if data == "check_reactions":

            answer_callback(
                callback_id,
                "در حال ارسال فایل..."
            )

            pending = get_pending(
                user_id
            )

            if not pending:

                send_message(
                    chat_id,
                    "❌ لینک قسمت پیدا نشد."
                )

                return jsonify(
                    {
                        "ok":
                            True
                    }
                )

            # Symbolic reaction gate as requested.
            # The bot does not inspect real channel reactions.

            if message_id:

                delete_message(
                    chat_id,
                    message_id
                )

            # File first -> warning second -> delete only files.
            send_episode(
                chat_id,
                user_id
            )

            return jsonify(
                {
                    "ok":
                        True
                }
            )

        return jsonify(
            {
                "ok":
                    True
            }
        )

    return jsonify(
        {
            "ok":
                True
        }
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
            "url":
                webhook_url,
            "allowed_updates": [
                "message",
                "callback_query",
                "channel_post"
            ],
            "drop_pending_updates":
                False
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
# STARTUP
# ============================================================

print(
    "Starting bot..."
)

# Load everything into RAM before accepting traffic.
sync_cache()
sync_users_cache()

setup_webhook()

threading.Thread(
    target=cache_loop,
    daemon=True
).start()


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
        port=port,
        threaded=True
    )
