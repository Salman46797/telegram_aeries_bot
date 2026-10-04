import os
import re
import time
import threading
import hashlib
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from flask import Flask, request, jsonify
from supabase import create_client


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

BROADCAST_DELAY = float(
    os.getenv("BROADCAST_DELAY", "0.05")
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


CACHE_LOCK = threading.RLock()

EPISODES = {}
TYPE_INDEX = {}
SPONSORS = []

PENDING = {}
PENDING_LOCK = threading.RLock()

BOT_USERNAME = ""
CACHE_READY = False


KNOWN_USERS = set()
KNOWN_USERS_LOCK = threading.RLock()


DELIVERY_LOCK = threading.RLock()

DELIVERING = set()


ADMIN_STATE_LOCK = threading.RLock()

ADMIN_STATE = {}


BROADCAST_LOCK = threading.RLock()

BROADCAST_RUNNING = False


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

        print(
            "Telegram error:",
            method,
            e
        )

        return {
            "ok": False,
            "description": str(e)
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

    entities = file_info.get(
        "caption_entities"
    ) or []

    if file_info.get("type") == "document":

        return send_document(
            chat_id,
            file_info["file_id"],
            caption,
            entities
        )

    return send_video(
        chat_id,
        file_info["file_id"],
        caption,
        entities
    )


def copy_message(
    chat_id,
    from_chat_id,
    message_id
):

    return tg(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id
        },
        timeout=20
    )


def series_key(name):

    value = (
        name or ""
    ).strip().lower()

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
        + episode_key(
            name,
            number
        )
    )


def type_code(
    ep_key,
    file_type
):

    raw = (
        f"{ep_key}|{file_type}"
    )

    return (
        "t_"
        + hashlib.sha1(
            raw.encode("utf-8")
        ).hexdigest()[:12]
    )


def normalize_type(
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

    return "سایر"


def enrich_files(
    files,
    ep_key
):

    result = []

    for raw in files or []:

        file_info = dict(
            raw
        )

        file_type = (
            file_info.get(
                "file_type"
            )
            or normalize_type(
                file_info.get(
                    "caption",
                    ""
                )
            )
        )

        file_info["file_type"] = (
            file_type
        )

        file_info["type_code"] = (
            file_info.get(
                "type_code"
            )
            or type_code(
                ep_key,
                file_type
            )
        )

        if (
            "caption_entities"
            not in file_info
        ):
            file_info[
                "caption_entities"
            ] = []

        result.append(
            file_info
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

    name = (
        series_match
        .group(1)
        .strip()
    )

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


def is_preview(
    caption
):

    return bool(
        re.search(
            r"پیش[\s‌-]*نمایش|\bpreview\b",
            caption or "",
            re.IGNORECASE
        )
    )


def cache_episode(
    row
):

    if not row:
        return

    key = row.get(
        "episode_key"
    )

    if not key:
        return

    row = dict(row)

    row["files"] = enrich_files(
        row.get("files") or [],
        key
    )

    with CACHE_LOCK:

        EPISODES[key] = row

        for code, value in list(
            TYPE_INDEX.items()
        ):

            if value[0] == key:

                TYPE_INDEX.pop(
                    code,
                    None
                )

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

        for row in (
            episodes_result.data or []
        ):

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


def get_episode(
    key
):

    with CACHE_LOCK:

        row = EPISODES.get(
            key
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

            cache_episode(
                row
            )

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

        cache_episode(
            payload
        )

        return result

    except Exception as e:

        print(
            "save_episode error:",
            e
        )

        return None


def delete_episode(
    key
):

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


def set_pending(
    user_id,
    value
):

    user_id = int(
        user_id
    )

    with PENDING_LOCK:

        PENDING[user_id] = value

    def persist():

        try:

            (
                supabase
                .table("pending")
                .upsert(
                    {
                        "user_id": str(
                            user_id
                        ),
                        "episode_key": value
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

    EXEC.submit(
        persist
    )


def get_pending(
    user_id
):

    user_id = int(
        user_id
    )

    with PENDING_LOCK:

        value = PENDING.get(
            user_id
        )

    if value:
        return value

    try:

        result = (
            supabase
            .table("pending")
            .select(
                "episode_key"
            )
            .eq(
                "user_id",
                str(user_id)
            )
            .limit(1)
            .execute()
        )

        if result.data:

            value = (
                result
                .data[0]
                .get(
                    "episode_key"
                )
            )

            if value:

                with PENDING_LOCK:

                    PENDING[user_id] = value

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

    user_id = int(
        user_id
    )

    with PENDING_LOCK:

        value = PENDING.pop(
            user_id,
            None
        )

    def persist_delete():

        try:

            query = (
                supabase
                .table("pending")
                .delete()
                .eq(
                    "user_id",
                    str(user_id)
                )
            )

            if value:

                query = query.eq(
                    "episode_key",
                    value
                )

            query.execute()

        except Exception as e:

            print(
                "pending delete error:",
                e
            )

    EXEC.submit(
        persist_delete
    )


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
                ) != int(
                    sponsor_id
                )
            ]

        return result

    except Exception as e:

        print(
            "remove sponsor error:",
            e
        )

        return None


def register_user(
    user
):

    if (
        not user
        or not user.get("id")
    ):
        return

    user_id = int(
        user["id"]
    )

    with KNOWN_USERS_LOCK:

        if user_id in KNOWN_USERS:

            return

    try:

        payload = {
            "user_id": user_id,
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
            "is_blocked": False,
            "last_seen":
                datetime.now(
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

        with KNOWN_USERS_LOCK:

            KNOWN_USERS.add(
                user_id
            )

    except Exception as e:

        print(
            "register_user error:",
            e
        )


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


def mark_user_blocked(
    user_id
):

    with KNOWN_USERS_LOCK:

        KNOWN_USERS.discard(
            int(user_id)
        )

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


def safe_int(
    value
):

    try:

        return int(
            value or 0
        )

    except Exception:

        return 0


def fetch_all_rows(
    table_name,
    select_fields,
    filters=None,
    page_size=1000
):

    rows = []

    offset = 0

    while True:

        query = (
            supabase
            .table(table_name)
            .select(select_fields)
        )

        for field, operator, value in (
            filters or []
        ):

            if operator == "eq":

                query = query.eq(
                    field,
                    value
                )

            elif operator == "gte":

                query = query.gte(
                    field,
                    value
                )

            elif operator == "lt":

                query = query.lt(
                    field,
                    value
                )

        batch = (
            query
            .range(
                offset,
                offset + page_size - 1
            )
            .execute()
            .data
            or []
        )

        rows.extend(
            batch
        )

        if len(batch) < page_size:
            break

        offset += page_size

    return rows


def get_advanced_stats():

    now = datetime.now(
        timezone.utc
    )

    today_start = datetime(
        now.year,
        now.month,
        now.day,
        tzinfo=timezone.utc
    )

    month_start = datetime(
        now.year,
        now.month,
        1,
        tzinfo=timezone.utc
    )

    next_day = (
        today_start
        + timedelta(days=1)
    )

    if now.month == 12:

        next_month = datetime(
            now.year + 1,
            1,
            1,
            tzinfo=timezone.utc
        )

    else:

        next_month = datetime(
            now.year,
            now.month + 1,
            1,
            tzinfo=timezone.utc
        )

    users = (
        supabase
        .table("bot_users")
        .select(
            "user_id",
            count="exact"
        )
        .execute()
    )

    users_today = (
        supabase
        .table("bot_users")
        .select(
            "user_id",
            count="exact"
        )
        .gte(
            "created_at",
            today_start.isoformat()
        )
        .lt(
            "created_at",
            next_day.isoformat()
        )
        .execute()
    )

    users_month = (
        supabase
        .table("bot_users")
        .select(
            "user_id",
            count="exact"
        )
        .gte(
            "created_at",
            month_start.isoformat()
        )
        .lt(
            "created_at",
            next_month.isoformat()
        )
        .execute()
    )

    rows = fetch_all_rows(
        "bot_stats",
        "event_type,episode_key,file_type,file_count,created_at",
        [
            (
                "created_at",
                "gte",
                month_start.isoformat()
            )
        ]
    )

    downloads_today = 0
    downloads_month = 0

    files_today = 0
    files_month = 0

    starts_today = 0
    starts_month = 0

    uploads_month = 0

    episode_downloads = {}

    for row in rows:

        created = (
            row.get(
                "created_at"
            )
            or ""
        )

        event = row.get(
            "event_type"
        )

        count = safe_int(
            row.get(
                "file_count"
            )
        )

        is_today = (
            created
            >= today_start.isoformat()
            and
            created
            < next_day.isoformat()
        )

        if event == "download":

            downloads_month += 1

            files_month += count

            if is_today:

                downloads_today += 1

                files_today += count

            key = (
                row.get(
                    "episode_key"
                )
                or "نامشخص"
            )

            episode_downloads[key] = (
                episode_downloads.get(
                    key,
                    0
                )
                + 1
            )

        elif event == "start":

            starts_month += 1

            if is_today:

                starts_today += 1

        elif event == "upload":

            uploads_month += 1

    top = sorted(
        episode_downloads.items(),
        key=lambda x: x[1],
        reverse=True
    )[:5]

    return {
        "users":
            users.count or 0,

        "users_today":
            users_today.count or 0,

        "users_month":
            users_month.count or 0,

        "downloads_today":
            downloads_today,

        "downloads_month":
            downloads_month,

        "files_today":
            files_today,

        "files_month":
            files_month,

        "starts_today":
            starts_today,

        "starts_month":
            starts_month,

        "uploads_month":
            uploads_month,

        "top":
            top
    }


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

    sponsor_list = (
        get_sponsors()
    )

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


def admin_menu_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "🎬 مدیریت قسمت‌ها",
                    "callback_data":
                        "adm_episodes"
                },
                {
                    "text":
                        "📢 اسپانسرها",
                    "callback_data":
                        "adm_sponsors"
                }
            ],
            [
                {
                    "text":
                        "📋 لیست قسمت‌ها",
                    "callback_data":
                        "adm_list"
                },
                {
                    "text":
                        "🗑 حذف قسمت",
                    "callback_data":
                        "adm_delete"
                }
            ],
            [
                {
                    "text":
                        "🗑 حذف همه قسمت‌ها",
                    "callback_data":
                        "adm_delete_all"
                },
                {
                    "text":
                        "📊 آمار پیشرفته",
                    "callback_data":
                        "adm_stats"
                }
            ],
            [
                {
                    "text":
                        "📢 پیام همگانی",
                    "callback_data":
                        "adm_broadcast"
                },
                {
                    "text":
                        "🔄 سینک دیتابیس",
                    "callback_data":
                        "adm_sync"
                }
            ],
            [
                {
                    "text":
                        "⚡ وضعیت ربات",
                    "callback_data":
                        "adm_status"
                }
            ]
        ]
    }


def back_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "🔙 بازگشت به پنل",
                    "callback_data":
                        "adm_home"
                }
            ]
        ]
    }


def sponsor_menu_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "➕ افزودن اسپانسر",
                    "callback_data":
                        "s_add"
                },
                {
                    "text":
                        "➖ حذف اسپانسر",
                    "callback_data":
                        "s_remove"
                }
            ],
            [
                {
                    "text":
                        "📋 لیست اسپانسرها",
                    "callback_data":
                        "s_list"
                }
            ],
            [
                {
                    "text":
                        "🔙 پنل اصلی",
                    "callback_data":
                        "adm_home"
                }
            ]
        ]
    }


def join_keyboard(
    missing=None
):

    rows = []

    if missing is None:

        rows.append(
            [
                {
                    "text":
                        "عضویت در کانال 📺",
                    "url":
                        CHANNEL_URL
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
                    "text":
                        "دانلود مجدد ♻️",
                    "url":
                        f"https://t.me/"
                        f"{BOT_USERNAME}"
                        f"?start={target}"
                }
            ]
        ]
    }


def send_reaction_page(
    chat_id
):

    send_message(
        chat_id,

        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر "
        "کانال @altiustuistsnbol را ری‌اکت بزنید "
        "و سپس برگردید و دکمه انجام دادم را کلیک کنید ♥️",

        reaction_keyboard()
    )


def send_join_page(
    chat_id,
    user_id
):

    main_ok, missing = (
        check_membership(
            user_id
        )
    )

    if (
        main_ok
        and not missing
    ):

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

            join_keyboard(
                None
            )
        )

        return "join"

    send_message(
        chat_id,

        "📣 هنوز عضویت بعضی کانال‌ها کامل نیست:\n\n"
        "1️⃣ در کانال‌های زیر عضو شو\n"
        "2️⃣ سپس روی «عضو شدم» بزن",

        join_keyboard(
            missing
        )
    )

    return "join"


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
            episode.get(
                "files"
            ) or [],
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

        send_message(
            chat_id,
            warning_text,
            redownload_keyboard(
                redownload_target
            )
        )

        EXEC.submit(
            record_stat,
            user_id,
            "download",
            real_key,
            selected_type,
            len(
                sent_message_ids
            )
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


def extract_file(
    message
):

    caption = (
        message.get(
            "caption",
            ""
        )
        or ""
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

        video = message[
            "video"
        ]

        return {
            "type":
                "video",

            "file_id":
                video.get(
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

        document = message[
            "document"
        ]

        return {
            "type":
                "document",

            "file_id":
                document.get(
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

            admin_menu_keyboard()
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

    file_info[
        "file_type"
    ] = normalize_type(
        caption
    )

    file_info[
        "type_code"
    ] = type_code(
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
            "❌ ذخیره در Supabase انجام نشد.",
            admin_menu_keyboard()
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
            "❌ نام کاربری ربات پیدا نشد.",
            admin_menu_keyboard()
        )

        return

    base_link = (
        f"https://t.me/"
        f"{BOT_USERNAME}"
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
        f"🪴 سریال: "
        f"{parsed['series_name']}",
        f"🪷 قسمت: "
        f"{parsed['episode_number']}",
        ""
    ]

    for file_type, code in (
        groups.items()
    ):

        lines.append(
            f"🎬 {file_type}"
        )

        lines.append(
            f"🔗 {base_link}"
            f"?start={code}"
        )

        lines.append("")

    lines.append(
        f"🔗 لینک مستقیم کل "
        f"{label}: "
        f"{base_link}?start={key}"
    )

    send_message(
        ADMIN_ID,
        "\n".join(lines),
        admin_menu_keyboard()
    )

    record_stat(
        ADMIN_ID,
        "preview"
        if preview
        else "upload",
        key,
        None,
        1
    )


def admin_set_state(
    state,
    **data
):

    with ADMIN_STATE_LOCK:

        ADMIN_STATE[
            ADMIN_ID
        ] = {
            "state": state,
            **data
        }


def admin_get_state():

    with ADMIN_STATE_LOCK:

        value = ADMIN_STATE.get(
            ADMIN_ID
        )

        return (
            dict(value)
            if isinstance(
                value,
                dict
            )
            else None
        )


def admin_clear_state():

    with ADMIN_STATE_LOCK:

        ADMIN_STATE.pop(
            ADMIN_ID,
            None
        )


def remove_old_reply_keyboard(
    chat_id
):

    result = send_message(
        chat_id,
        "\u200b",
        {
            "remove_keyboard":
                True
        }
    )

    if result.get("ok"):

        message_id = (
            result.get(
                "result"
            ) or {}
        ).get(
            "message_id"
        )

        if message_id:

            delete_message(
                chat_id,
                message_id
            )


def admin_home(
    chat_id,
    message_id=None
):

    text = (
        "⚙️ پنل مدیریت ربات\n\n"
        "قابلیت موردنظر را انتخاب کن:"
    )

    if message_id:

        return edit_message(
            chat_id,
            message_id,
            text,
            admin_menu_keyboard()
        )

    return send_message(
        chat_id,
        text,
        admin_menu_keyboard()
    )


def get_user_count_for_broadcast():

    try:

        result = (
            supabase
            .table("bot_users")
            .select(
                "user_id",
                count="exact"
            )
            .eq(
                "is_blocked",
                False
            )
            .execute()
        )

        return (
            result.count
            or 0
        )

    except Exception as e:

        print(
            "broadcast count error:",
            e
        )

        return 0


def run_broadcast(
    admin_chat_id,
    source_message_id,
    prompt_message_id=None
):

    global BROADCAST_RUNNING

    with BROADCAST_LOCK:

        if BROADCAST_RUNNING:

            send_message(
                admin_chat_id,
                "⏳ یک پیام همگانی "
                "در حال ارسال است.",
                admin_menu_keyboard()
            )

            return

        BROADCAST_RUNNING = True

    success = 0
    failed = 0
    total = 0

    try:

        users = fetch_all_rows(
            "bot_users",
            "user_id",
            [
                (
                    "is_blocked",
                    "eq",
                    False
                )
            ]
        )

        total = len(
            users
        )

        for row in users:

            user_id = row.get(
                "user_id"
            )

            if not user_id:
                continue

            sent = copy_message(
                user_id,
                admin_chat_id,
                source_message_id
            )

            if sent.get("ok"):

                success += 1

            else:

                failed += 1

                desc = (
                    sent.get(
                        "description"
                    )
                    or ""
                ).lower()

                if (
                    "blocked" in desc
                    or "chat not found" in desc
                    or "user is deactivated"
                    in desc
                ):

                    mark_user_blocked(
                        user_id
                    )

            time.sleep(
                BROADCAST_DELAY
            )

        EXEC.submit(
            record_stat,
            ADMIN_ID,
            "broadcast",
            None,
            None,
            success
        )

        send_message(
            admin_chat_id,

            "📢 پیام همگانی تمام شد.\n\n"
            f"👥 کل کاربران: {total}\n"
            f"✅ موفق: {success}\n"
            f"❌ ناموفق: {failed}",

            admin_menu_keyboard()
        )

    except Exception as e:

        print(
            "broadcast error:",
            e
        )

        send_message(
            admin_chat_id,
            f"❌ ارسال همگانی "
            f"با خطا متوقف شد:\n{e}",
            admin_menu_keyboard()
        )

    finally:

        with BROADCAST_LOCK:

            BROADCAST_RUNNING = False


def handle_broadcast_confirmation(
    callback,
    confirm
):

    callback_id = callback.get(
        "id"
    )

    message = (
        callback.get(
            "message"
        )
        or {}
    )

    chat_id = (
        message.get(
            "chat"
        )
        or {}
    ).get(
        "id"
    )

    message_id = message.get(
        "message_id"
    )

    state = admin_get_state()

    if (
        not state
        or state.get(
            "state"
        )
        != "broadcast_confirm"
    ):

        answer_callback(
            callback_id,
            "این پیام منقضی شده.",
            True
        )

        return

    source_message_id = state.get(
        "source_message_id"
    )

    admin_clear_state()

    if not confirm:

        answer_callback(
            callback_id,
            "لغو شد."
        )

        if message_id:

            edit_message(
                chat_id,
                message_id,
                "❌ پیام همگانی لغو شد.",
                admin_menu_keyboard()
            )

        return

    answer_callback(
        callback_id,
        "ارسال شروع شد."
    )

    if message_id:

        edit_message(
            chat_id,
            message_id,
            "⏳ پیام همگانی "
            "در حال ارسال است...",
            None
        )

    MEDIA_EXEC.submit(
        run_broadcast,
        chat_id,
        source_message_id,
        message_id
    )


def handle_admin_callback(
    callback
):

    callback_id = callback.get(
        "id"
    )

    data = callback.get(
        "data",
        ""
    )

    message = (
        callback.get(
            "message"
        )
        or {}
    )

    chat_id = (
        message.get(
            "chat"
        )
        or {}
    ).get(
        "id"
    )

    message_id = message.get(
        "message_id"
    )

    from_user = (
        callback.get(
            "from"
        )
        or {}
    )

    if from_user.get(
        "id"
    ) != ADMIN_ID:

        answer_callback(
            callback_id,
            "دسترسی نداری.",
            True
        )

        return

    if data == "adm_home":

        admin_clear_state()

        answer_callback(
            callback_id
        )

        admin_home(
            chat_id,
            message_id
        )

        return

    if data == "adm_episodes":

        admin_clear_state()

        answer_callback(
            callback_id
        )

        edit_message(
            chat_id,
            message_id,

            "🎬 مدیریت قسمت‌ها\n\n"
            "ویدیو یا فایل قسمت را "
            "با کپشن قبلی بفرست.\n\n"
            "فرمت کپشن باید شامل "
            "نام سریال و شماره قسمت باشد.\n\n"
            "فرمت‌های پشتیبانی‌شده: "
            "ویدیو و فایل.",

            back_keyboard()
        )

        return

    if data == "adm_sponsors":

        admin_clear_state()

        answer_callback(
            callback_id
        )

        edit_message(
            chat_id,
            message_id,

            "📢 مدیریت اسپانسرها\n\n"
            "یک گزینه را انتخاب کن:",

            sponsor_menu_keyboard()
        )

        return

    if data == "s_list":

        answer_callback(
            callback_id
        )

        sponsors = get_sponsors()

        if not sponsors:

            text = (
                "📋 هیچ اسپانسری "
                "ثبت نشده."
            )

        else:

            lines = [
                "📋 لیست اسپانسرها:"
            ]

            for sponsor in sponsors:

                lines.append(
                    f"\n🆔 ID: "
                    f"{sponsor.get('id')}\n"
                    f"📢 کانال: "
                    f"{sponsor.get('chat_id')}\n"
                    f"📝 نام: "
                    f"{sponsor.get('title')}\n"
                    f"🔗 لینک: "
                    f"{sponsor.get('url')}"
                )

            text = "\n".join(
                lines
            )

        edit_message(
            chat_id,
            message_id,
            text,
            sponsor_menu_keyboard()
        )

        return

    if data == "s_add":

        admin_set_state(
            "add_sponsor"
        )

        answer_callback(
            callback_id
        )

        edit_message(
            chat_id,
            message_id,

            "➕ افزودن اسپانسر\n\n"
            "فرمت را دقیقاً این‌طور بفرست:\n\n"
            "@channel | نام کانال | "
            "https://t.me/channel",

            back_keyboard()
        )

        return

    if data == "s_remove":

        admin_set_state(
            "remove_sponsor"
        )

        answer_callback(
            callback_id
        )

        edit_message(
            chat_id,
            message_id,

            "➖ حذف اسپانسر\n\n"
            "ID اسپانسر را بفرست.\n"
            "مثال: 4",

            back_keyboard()
        )

        return

    if data == "adm_list":

        answer_callback(
            callback_id
        )

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

            text = (
                "📋 هیچ قسمتی "
                "ذخیره نشده."
            )

        else:

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

        edit_message(
            chat_id,
            message_id,
            text,
            back_keyboard()
        )

        return

    if data == "adm_delete":

        admin_set_state(
            "delete_episode"
        )

        answer_callback(
            callback_id
        )

        edit_message(
            chat_id,
            message_id,

            "🗑 حذف قسمت\n\n"
            "کلید قسمت را بفرست.\n"
            "مثال:\n"
            "ep_xxxxxxxxxx_1",

            back_keyboard()
        )

        return

    if data == "adm_delete_all":

        answer_callback(
            callback_id
        )

        edit_message(
            chat_id,
            message_id,

            "⚠️ مطمئنی می‌خواهی "
            "همه قسمت‌ها حذف شوند؟",

            {
                "inline_keyboard": [
                    [
                        {
                            "text":
                                "❌ بله، همه را حذف کن",
                            "callback_data":
                                "confirm_delete_all"
                        }
                    ],
                    [
                        {
                            "text":
                                "🔙 انصراف",
                            "callback_data":
                                "adm_home"
                        }
                    ]
                ]
            }
        )

        return

    if data == "confirm_delete_all":

        answer_callback(
            callback_id,
            "در حال حذف..."
        )

        result = delete_all_episodes()

        if result is None:

            edit_message(
                chat_id,
                message_id,
                "❌ حذف همه قسمت‌ها "
                "ناموفق بود.",
                admin_menu_keyboard()
            )

        else:

            edit_message(
                chat_id,
                message_id,
                "✅ همه قسمت‌ها حذف شدند.",
                admin_menu_keyboard()
            )

        return

    if data == "adm_sync":

        answer_callback(
            callback_id,
            "در حال سینک..."
        )

        ok = sync_cache()

        edit_message(
            chat_id,
            message_id,

            "✅ سینک دیتابیس انجام شد."
            if ok
            else
            "❌ سینک ناموفق بود.",

            admin_menu_keyboard()
        )

        return

    if data == "adm_status":

        answer_callback(
            callback_id
        )

        db_ok = False

        try:

            (
                supabase
                .table("episodes")
                .select(
                    "episode_key"
                )
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

        edit_message(
            chat_id,
            message_id,

            "⚡ وضعیت ربات\n\n"
            "🤖 Telegram: 🟢\n"
            f"🗄 Supabase: "
            f"{'🟢' if db_ok else '🔴'}\n"
            f"⚡ Cache: "
            f"{'🟢' if CACHE_READY else '🔴'}\n"
            f"🎬 قسمت‌ها: "
            f"{episode_count}\n"
            f"📢 اسپانسرها: "
            f"{sponsor_count}\n"
            f"🔄 سینک خودکار: "
            f"هر {CACHE_SYNC_INTERVAL} ثانیه",

            back_keyboard()
        )

        return

    if data == "adm_stats":

        answer_callback(
            callback_id,
            "در حال دریافت آمار..."
        )

        try:

            s = get_advanced_stats()

            lines = [
                "📊 آمار پیشرفته ربات",
                "",
                f"👥 کل کاربران: "
                f"{s['users']}",
                f"🆕 کاربران امروز: "
                f"{s['users_today']}",
                f"🆕 کاربران این ماه: "
                f"{s['users_month']}",
                "",
                f"🚀 /start امروز: "
                f"{s['starts_today']}",
                f"🚀 /start این ماه: "
                f"{s['starts_month']}",
                "",
                f"📥 دانلود امروز: "
                f"{s['downloads_today']}",
                f"📥 دانلود این ماه: "
                f"{s['downloads_month']}",
                f"📦 فایل‌های ارسال‌شده امروز: "
                f"{s['files_today']}",
                f"📦 فایل‌های ارسال‌شده این ماه: "
                f"{s['files_month']}",
                f"📤 قسمت‌های ثبت‌شده این ماه: "
                f"{s['uploads_month']}",
                "",
                "🏆 پربازدیدترین قسمت‌ها این ماه:"
            ]

            if s["top"]:

                for i, (
                    key,
                    count
                ) in enumerate(
                    s["top"],
                    1
                ):

                    lines.append(
                        f"{i}. {key} — "
                        f"{count} دانلود"
                    )

            else:

                lines.append(
                    "هنوز آماری "
                    "ثبت نشده."
                )

            edit_message(
                chat_id,
                message_id,
                "\n".join(lines),
                back_keyboard()
            )

        except Exception as e:

            edit_message(
                chat_id,
                message_id,
                f"❌ خطا در آمار:\n{e}",
                back_keyboard()
            )

        return

    if data == "adm_broadcast":

        admin_set_state(
            "broadcast_waiting"
        )

        answer_callback(
            callback_id
        )

        edit_message(
            chat_id,
            message_id,

            "📢 پیام همگانی\n\n"
            "هر چیزی که می‌خواهی برای "
            "کاربران ارسال شود بفرست؛ "
            "متن، عکس، ویدیو، فایل و...\n\n"
            "برای لغو، "
            "/cancel_broadcast را بفرست.",

            back_keyboard()
        )

        return

    if data == "broadcast_confirm":

        handle_broadcast_confirmation(
            callback,
            True
        )

        return

    if data == "broadcast_cancel":

        handle_broadcast_confirmation(
            callback,
            False
        )

        return

    answer_callback(
        callback_id
    )


def handle_admin_command(
    chat_id,
    text,
    message=None
):

    text = (
        text or ""
    ).strip()

    if text == "/start":

        admin_clear_state()

        remove_old_reply_keyboard(
            chat_id
        )

        admin_home(
            chat_id
        )

        return True

    if text == "/cancel_broadcast":

        state = admin_get_state()

        if (
            state
            and state.get(
                "state"
            ) in (
                "broadcast_waiting",
                "broadcast_confirm"
            )
        ):

            admin_clear_state()

            send_message(
                chat_id,
                "❌ پیام همگانی لغو شد.",
                admin_menu_keyboard()
            )

        else:

            send_message(
                chat_id,
                "ℹ️ پیام همگانی فعالی "
                "وجود ندارد.",
                admin_menu_keyboard()
            )

        return True

    state = admin_get_state()

    state_name = (
        state.get(
            "state"
        )
        if state
        else None
    )

    if state_name == "broadcast_waiting":

        if not message:

            send_message(
                chat_id,
                "❌ پیام نامعتبر است.",
                admin_menu_keyboard()
            )

            return True

        count = (
            get_user_count_for_broadcast()
        )

        admin_set_state(
            "broadcast_confirm",
            source_message_id=
                message.get(
                    "message_id"
                )
        )

        send_message(
            chat_id,

            f"📢 پیام آماده ارسال است.\n\n"
            f"👥 گیرنده‌های فعلی: "
            f"{count}\n\n"
            f"ارسال شود؟",

            {
                "inline_keyboard": [
                    [
                        {
                            "text":
                                "✅ ارسال برای همه",
                            "callback_data":
                                "broadcast_confirm"
                        },
                        {
                            "text":
                                "❌ لغو",
                            "callback_data":
                                "broadcast_cancel"
                        }
                    ]
                ]
            }
        )

        return True

    if state_name == "add_sponsor":

        parts = [
            x.strip()
            for x in text.split("|")
        ]

        if len(parts) != 3:

            send_message(
                chat_id,

                "❌ فرمت اشتباه است.\n\n"
                "@channel | نام کانال | "
                "https://t.me/channel",

                back_keyboard()
            )

            return True

        result = add_sponsor(
            parts[0],
            parts[1],
            parts[2]
        )

        admin_clear_state()

        send_message(
            chat_id,

            "✅ اسپانسر اضافه شد."
            if result is not None
            else
            "❌ ذخیره اسپانسر انجام نشد.",

            admin_menu_keyboard()
        )

        return True

    if state_name == "remove_sponsor":

        if not text.isdigit():

            send_message(
                chat_id,

                "❌ فقط ID اسپانسر را بفرست.\n"
                "مثال: 4",

                back_keyboard()
            )

            return True

        result = remove_sponsor(
            int(text)
        )

        admin_clear_state()

        send_message(
            chat_id,

            "✅ اسپانسر حذف شد."
            if result is not None
            else
            "❌ حذف اسپانسر انجام نشد.",

            admin_menu_keyboard()
        )

        return True

    if state_name == "delete_episode":

        key = text

        result = delete_episode(
            key
        )

        admin_clear_state()

        send_message(
            chat_id,

            "✅ اطلاعات قسمت حذف شد."
            if result is not None
            else
            "❌ حذف قسمت ناموفق بود.",

            admin_menu_keyboard()
        )

        return True

    if text == "/sponsors":

        sponsors = get_sponsors()

        if not sponsors:

            send_message(
                chat_id,
                "هیچ اسپانسری ثبت نشده.",
                admin_menu_keyboard()
            )

        else:

            lines = [
                "📋 لیست اسپانسرها:"
            ]

            for s in sponsors:

                lines.append(
                    f"\nID: {s.get('id')}\n"
                    f"کانال: {s.get('chat_id')}\n"
                    f"نام: {s.get('title')}\n"
                    f"لینک: {s.get('url')}"
                )

            send_message(
                chat_id,
                "\n".join(lines),
                admin_menu_keyboard()
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

                "فرمت درست:\n"
                "/add_sponsor @channel | "
                "نام کانال | "
                "https://t.me/channel",

                admin_menu_keyboard()
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

            admin_menu_keyboard()
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
                "/remove_sponsor ID",

                admin_menu_keyboard()
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
            "❌ حذف اسپانسر انجام نشد.",

            admin_menu_keyboard()
        )

        return True

    if text == "/broadcast":

        admin_set_state(
            "broadcast_waiting"
        )

        send_message(
            chat_id,

            "📢 پیام همگانی\n\n"
            "پیام موردنظر را بفرست.\n"
            "برای لغو "
            "/cancel_broadcast",

            back_keyboard()
        )

        return True

    if text == "/delete_all":

        result = (
            delete_all_episodes()
        )

        send_message(
            chat_id,

            "✅ همه قسمت‌ها حذف شدند."
            if result is not None
            else
            "❌ حذف همه قسمت‌ها "
            "ناموفق بود.",

            admin_menu_keyboard()
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
                "/delete_episode "
                "EPISODE_KEY",

                admin_menu_keyboard()
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
            "❌ حذف قسمت ناموفق بود.",

            admin_menu_keyboard()
        )

        return True

    if text:

        send_message(
            chat_id,

            "⚙️ پنل مدیریت\n\n"
            "قابلیت موردنظر را انتخاب کن:",

            admin_menu_keyboard()
        )

        return True

    return False


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

    message = update.get(
        "message"
    )

    if message:

        chat = (
            message.get(
                "chat"
            )
            or {}
        )

        chat_id = chat.get(
            "id"
        )

        sender = (
            message.get(
                "from"
            )
            or {}
        )

        user_id = sender.get(
            "id"
        )

        text = (
            message.get(
                "text",
                ""
            )
            or ""
        )

        if user_id:

            EXEC.submit(
                register_user,
                sender
            )

        if user_id == ADMIN_ID:

            state = admin_get_state()

            state_name = (
                state.get(
                    "state"
                )
                if state
                else None
            )

            if (
                state_name
                == "broadcast_waiting"
            ):

                if text == "/cancel_broadcast":

                    handle_admin_command(
                        chat_id,
                        text,
                        message
                    )

                else:

                    handle_admin_command(
                        chat_id,
                        "__broadcast_message__",
                        message
                    )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            if text:

                if handle_admin_command(
                    chat_id,
                    text,
                    message
                ):

                    return jsonify(
                        {
                            "ok": True
                        }
                    )

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

        if text.startswith(
            "/start"
        ):

            parts = text.split(
                maxsplit=1
            )

            if len(parts) == 1:

                if user_id == ADMIN_ID:

                    admin_home(
                        chat_id
                    )

                else:

                    send_message(
                        chat_id,

                        "سلام 👋\n"
                        "لینک قسمت موردنظرت "
                        "رو باز کن."
                    )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            token = parts[1].strip()

            if token.startswith(
                "t_"
            ):

                with CACHE_LOCK:

                    target = (
                        TYPE_INDEX.get(
                            token
                        )
                    )

                if not target:

                    sync_cache()

                    with CACHE_LOCK:

                        target = (
                            TYPE_INDEX.get(
                                token
                            )
                        )

                if not target:

                    send_message(
                        chat_id,
                        "❌ لینک نوع فایل "
                        "پیدا نشد یا حذف شده."
                    )

                    return jsonify(
                        {
                            "ok": True
                        }
                    )

                real_key, file_type = target

                set_pending(
                    user_id,
                    f"TYPE::{real_key}::{file_type}"
                )

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
                        {
                            "ok": True
                        }
                    )

                set_pending(
                    user_id,
                    token
                )

            EXEC.submit(
                record_stat,
                user_id,
                "start",
                token,
                None,
                0
            )

            send_join_page(
                chat_id,
                user_id
            )

            return jsonify(
                {
                    "ok": True
                }
            )

    callback = update.get(
        "callback_query"
    )

    if callback:

        from_user = (
            callback.get(
                "from"
            )
            or {}
        )

        user_id = from_user.get(
            "id"
        )

        if user_id:

            EXEC.submit(
                register_user,
                from_user
            )

        data = str(
            callback.get(
                "data",
                ""
            )
        )

        if (
            user_id == ADMIN_ID
            and data.startswith(
                (
                    "adm_",
                    "s_",
                    "confirm_",
                    "broadcast_"
                )
            )
        ):

            handle_admin_callback(
                callback
            )

            return jsonify(
                {
                    "ok": True
                }
            )

        callback_id = callback.get(
            "id"
        )

        callback_message = (
            callback.get(
                "message"
            )
            or {}
        )

        callback_chat = (
            callback_message.get(
                "chat"
            )
            or {}
        )

        chat_id = callback_chat.get(
            "id"
        )

        message_id = (
            callback_message.get(
                "message_id"
            )
        )

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
                    {
                        "ok": True
                    }
                )

            main_ok, missing = (
                check_membership(
                    user_id
                )
            )

            if (
                main_ok
                and not missing
            ):

                if message_id:

                    delete_message(
                        chat_id,
                        message_id
                    )

                send_reaction_page(
                    chat_id
                )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            if not main_ok:

                send_message(
                    chat_id,

                    "هنوز عضو کانال اصلی "
                    "نیستی 👇",

                    join_keyboard(
                        None
                    )
                )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            send_message(
                chat_id,

                "هنوز عضویت بعضی کانال‌ها "
                "تأیید نشده 👇",

                join_keyboard(
                    missing
                )
            )

            return jsonify(
                {
                    "ok": True
                }
            )

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
                    {
                        "ok": True
                    }
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
                {
                    "ok": True
                }
            )

    return jsonify(
        {
            "ok": True
        }
    )


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
                "callback_query"
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
