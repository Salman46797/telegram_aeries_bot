import os
import time
import hashlib
import threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, request, jsonify
from supabase import create_client, Client


# =========================================================
# CONFIG
# =========================================================

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

PORT = int(os.getenv("PORT", "10000"))

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    "https://telegram-aeries-bot.onrender.com"
).rstrip("/")

WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/webhook"


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL تنظیم نشده")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY تنظیم نشده")


# =========================================================
# APP / CLIENTS
# =========================================================

app = Flask(__name__)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

SESSION = requests.Session()

try:
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=32,
        pool_maxsize=64,
        max_retries=0
    )

    SESSION.mount("https://", adapter)
    SESSION.mount("http://", adapter)

except Exception:
    pass


EXEC = ThreadPoolExecutor(max_workers=16)
MEDIA_EXEC = ThreadPoolExecutor(max_workers=4)


# =========================================================
# MEMORY CACHE
# =========================================================

EPISODES = {}
TYPE_INDEX = {}
SPONSORS = {}

CACHE_READY = False

CACHE_LOCK = threading.RLock()

PENDING = {}
PENDING_LOCK = threading.RLock()

DELIVERING = set()
DELIVERING_LOCK = threading.RLock()

ADMIN_STATE = {}
ADMIN_STATE_LOCK = threading.RLock()

BROADCAST_RUNNING = False
BROADCAST_LOCK = threading.Lock()
BROADCAST_CANCEL = threading.Event()

BOT_USERNAME = ""


# =========================================================
# TELEGRAM API
# =========================================================

def tg(method, data=None, timeout=35):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        response = SESSION.post(
            url,
            json=data or {},
            timeout=timeout
        )

        try:
            return response.json()

        except Exception:
            return {
                "ok": False,
                "description": response.text
            }

    except Exception as e:
        return {
            "ok": False,
            "description": str(e)
        }


def send_message(
    chat_id,
    text,
    reply_markup=None,
    disable_web_page_preview=True
):
    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return tg("sendMessage", data)


def edit_message_text(
    chat_id,
    message_id,
    text,
    reply_markup=None
):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return tg("editMessageText", data)


def delete_message(chat_id, message_id):
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
    return tg("getMe", {})


def copy_message(
    to_chat_id,
    from_chat_id,
    message_id
):
    return tg(
        "copyMessage",
        {
            "chat_id": to_chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id
        }
    )


# =========================================================
# USERS / STATS
# =========================================================

def register_user(user):
    if not user:
        return

    user_id = user.get("id")

    if not user_id:
        return

    if user_id == ADMIN_ID:
        return

    data = {
        "user_id": user_id,
        "first_name": user.get("first_name") or "",
        "last_name": user.get("last_name") or "",
        "username": user.get("username") or "",
        "last_seen": datetime.now(timezone.utc).isoformat()
    }

    try:
        supabase.table("bot_users").upsert(
            data,
            on_conflict="user_id"
        ).execute()

    except Exception as e:
        print("register_user:", e)


def mark_user_blocked(user_id):
    try:
        supabase.table("bot_users").update(
            {
                "is_blocked": True,
                "last_seen": datetime.now(timezone.utc).isoformat()
            }
        ).eq(
            "user_id",
            user_id
        ).execute()

    except Exception:
        pass


def record_stat(
    user_id,
    event_type,
    episode_key=None,
    file_type=None,
    file_count=0
):
    try:
        data = {
            "user_id": int(user_id),
            "event_type": event_type,
            "episode_key": episode_key,
            "file_type": file_type,
            "file_count": int(file_count or 0)
        }

        supabase.table("bot_stats").insert(
            data
        ).execute()

    except Exception as e:
        print("record_stat:", e)


def count_rows(
    table,
    column="user_id",
    equals=None,
    gte=None,
    lt=None
):
    try:
        query = supabase.table(table).select(
            column,
            count="exact",
            head=True
        )

        if equals:
            for key, value in equals.items():
                query = query.eq(key, value)

        if gte:
            query = query.gte("created_at", gte)

        if lt:
            query = query.lt("created_at", lt)

        result = query.execute()

        return int(result.count or 0)

    except Exception:
        return 0


def fetch_all_rows(
    table,
    select="*",
    equals=None,
    gte=None,
    lt=None,
    max_rows=50000
):
    rows = []

    start = 0
    page_size = 1000

    while start < max_rows:

        try:
            query = supabase.table(table).select(select)

            if equals:
                for key, value in equals.items():
                    query = query.eq(key, value)

            if gte:
                query = query.gte("created_at", gte)

            if lt:
                query = query.lt("created_at", lt)

            result = query.range(
                start,
                min(start + page_size - 1, max_rows - 1)
            ).execute()

            batch = result.data or []

            rows.extend(batch)

            if len(batch) < page_size:
                break

            start += page_size

        except Exception:
            break

    return rows


def utc_day_start():
    now = datetime.now(timezone.utc)

    return now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )


def utc_month_start():
    now = datetime.now(timezone.utc)

    return now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )


# =========================================================
# EPISODE KEYS
# =========================================================

def series_key(series_name):
    return "_".join(
        str(series_name or "").strip().lower().split()
    )


def episode_key(
    series_name,
    episode_number
):
    return f"{series_key(series_name)}_{episode_number}"


def preview_key(
    series_name,
    episode_number
):
    return f"preview_{episode_key(series_name, episode_number)}"


def is_preview_episode(key):
    return str(key or "").startswith("preview_")


# =========================================================
# FILE TYPES
# =========================================================

def normalize_type(value):
    value = str(value or "").lower().strip()

    if value in ("video", "ویدیو"):
        return "video"

    if value in ("document", "doc", "file", "فایل"):
        return "document"

    if value in ("audio", "صوت"):
        return "audio"

    if value in ("photo", "عکس"):
        return "photo"

    return value


def type_code(file_type):
    return {
        "video": "v",
        "document": "d",
        "audio": "a",
        "photo": "p"
    }.get(
        normalize_type(file_type),
        "f"
    )


# =========================================================
# STABLE FILE LINKS
# =========================================================

def stable_type_prefix(key):
    """
    مهم:
    از hash() پایتون استفاده نمی‌کنیم.
    SHA1 بعد از ری‌استارت Render هم ثابت می‌ماند.
    """

    return hashlib.sha1(
        str(key).encode("utf-8")
    ).hexdigest()[:10]


def make_type_code(
    key,
    file_type,
    index
):
    return (
        f"t_"
        f"{stable_type_prefix(key)}_"
        f"{type_code(file_type)}"
        f"{index}"
    )


# =========================================================
# FILE DATA
# =========================================================

def enrich_files(files):
    result = []

    for item in files or []:

        if not isinstance(item, dict):
            continue

        copy_item = dict(item)

        copy_item["type"] = normalize_type(
            copy_item.get("type")
        )

        if "caption" not in copy_item:
            copy_item["caption"] = ""

        if "caption_entities" not in copy_item:
            copy_item["caption_entities"] = []

        result.append(copy_item)

    return result


# =========================================================
# CAPTION PARSER
# =========================================================

def parse_caption(caption):
    caption = caption or ""

    series_name = ""
    episode_number = ""

    lines = caption.splitlines()

    for line in lines:

        low = line.lower().strip()

        if "سریال" in low:

            if "«" in line and "»" in line:
                try:
                    series_name = (
                        line
                        .split("«", 1)[1]
                        .split("»", 1)[0]
                        .strip()
                    )
                except Exception:
                    pass

            elif ":" in line:
                try:
                    series_name = (
                        line
                        .split(":", 1)[1]
                        .strip()
                    )
                except Exception:
                    pass

        if "قسمت" in low:

            try:
                after = (
                    line
                    .split("قسمت", 1)[1]
                    .replace(":", " ")
                    .strip()
                )

                number = ""

                for char in after:

                    if char.isdigit():
                        number += char

                    elif number:
                        break

                if number:
                    episode_number = number

            except Exception:
                pass

    return (
        series_name.strip(),
        episode_number.strip()
    )


# =========================================================
# CACHE
# =========================================================

def cache_episode(row):

    if not row:
        return

    key = row.get("episode_key")

    if not key:
        return

    row = dict(row)

    row["files"] = enrich_files(
        row.get("files") or []
    )

    with CACHE_LOCK:

        EPISODES[key] = row

        old_codes = [
            code
            for code, value in TYPE_INDEX.items()
            if (
                isinstance(value, (list, tuple))
                and value
                and value[0] == key
            )
        ]

        for code in old_codes:
            TYPE_INDEX.pop(code, None)

        for index, file_item in enumerate(
            row["files"]
        ):

            code = make_type_code(
                key,
                file_item.get("type"),
                index
            )

            TYPE_INDEX[code] = (
                key,
                index
            )


def sync_cache():

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
            .execute()
        )

        new_episodes = {}
        new_type_index = {}
        new_sponsors = {}

        for row in episodes_result.data or []:

            key = row.get("episode_key")

            if not key:
                continue

            row = dict(row)

            row["files"] = enrich_files(
                row.get("files") or []
            )

            new_episodes[key] = row

            for index, file_item in enumerate(
                row["files"]
            ):

                code = make_type_code(
                    key,
                    file_item.get("type"),
                    index
                )

                new_type_index[code] = (
                    key,
                    index
                )

        for row in sponsors_result.data or []:

            sponsor_id = row.get("id")

            if sponsor_id is not None:
                new_sponsors[str(sponsor_id)] = row

        with CACHE_LOCK:

            EPISODES.clear()
            EPISODES.update(new_episodes)

            TYPE_INDEX.clear()
            TYPE_INDEX.update(new_type_index)

            SPONSORS.clear()
            SPONSORS.update(new_sponsors)

            CACHE_READY = True

        print(
            f"CACHE SYNC OK | episodes={len(EPISODES)} "
            f"sponsors={len(SPONSORS)}"
        )

        return True

    except Exception as e:

        print("CACHE SYNC ERROR:", e)

        return False


def cache_loop():

    while True:

        try:
            time.sleep(CACHE_SYNC_INTERVAL)
            sync_cache()

        except Exception as e:
            print("cache_loop:", e)


def get_episode(key):

    with CACHE_LOCK:
        return EPISODES.get(key)


def load_episode_from_db(key):

    try:

        result = (
            supabase
            .table("episodes")
            .select("*")
            .eq("episode_key", key)
            .limit(1)
            .execute()
        )

        if result.data:

            row = result.data[0]

            cache_episode(row)

            return row

    except Exception as e:
        print("load_episode_from_db:", e)

    return None


# =========================================================
# SAVE / DELETE EPISODES
# =========================================================

def save_episode(
    series_name,
    episode_number,
    files,
    preview=False
):

    key = (
        preview_key(
            series_name,
            episode_number
        )
        if preview
        else episode_key(
            series_name,
            episode_number
        )
    )

    data = {
        "episode_key": key,
        "series_name": series_name,
        "episode_number": str(episode_number),
        "files": enrich_files(files)
    }

    try:

        result = (
            supabase
            .table("episodes")
            .upsert(
                data,
                on_conflict="episode_key"
            )
            .execute()
        )

        row = (
            result.data[0]
            if result.data
            else data
        )

        cache_episode(row)

        return True

    except Exception as e:

        print("save_episode:", e)

        return False


def delete_episode(key):

    try:

        supabase.table(
            "episodes"
        ).delete().eq(
            "episode_key",
            key
        ).execute()

        with CACHE_LOCK:

            EPISODES.pop(key, None)

            old_codes = [
                code
                for code, value in TYPE_INDEX.items()
                if (
                    isinstance(value, (list, tuple))
                    and value
                    and value[0] == key
                )
            ]

            for code in old_codes:
                TYPE_INDEX.pop(code, None)

        return True

    except Exception as e:

        print("delete_episode:", e)

        return False


def delete_all_episodes():

    try:

        supabase.table(
            "episodes"
        ).delete().neq(
            "episode_key",
            ""
        ).execute()

        with CACHE_LOCK:

            EPISODES.clear()
            TYPE_INDEX.clear()

        return True

    except Exception as e:

        print("delete_all_episodes:", e)

        return False


# =========================================================
# PENDING
# =========================================================

def set_pending(
    user_id,
    episode_key_value,
    selected_type=None
):

    with PENDING_LOCK:

        PENDING[user_id] = {
            "episode_key": episode_key_value,
            "selected_type": selected_type
        }


def get_pending(user_id):

    with PENDING_LOCK:

        value = PENDING.get(user_id)

        return dict(value) if value else None


def clear_pending(user_id):

    with PENDING_LOCK:
        PENDING.pop(user_id, None)


# =========================================================
# SPONSORS
# =========================================================

def get_sponsors():

    with CACHE_LOCK:
        return list(SPONSORS.values())


def add_sponsor(
    username,
    title,
    link
):

    try:

        data = {
            "username": username.strip(),
            "title": title.strip(),
            "link": link.strip()
        }

        result = (
            supabase
            .table("sponsors")
            .insert(data)
            .execute()
        )

        sync_cache()

        return (
            result.data[0]
            if result.data
            else None
        )

    except Exception as e:

        print("add_sponsor:", e)

        return None


def remove_sponsor(sponsor_id):

    try:

        result = (
            supabase
            .table("sponsors")
            .delete()
            .eq("id", int(sponsor_id))
            .execute()
        )

        sync_cache()

        return bool(result)

    except Exception as e:

        print("remove_sponsor:", e)

        return False


# =========================================================
# MEMBERSHIP
# =========================================================

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


def check_membership(user_id):

    targets = [
        CHANNEL_ID
    ]

    for sponsor in get_sponsors():

        username = sponsor.get("username")

        if username and username not in targets:
            targets.append(username)

    for target in targets:

        if not member_ok(
            target,
            user_id
        ):
            return False

    return True


# =========================================================
# USER KEYBOARDS
# =========================================================

def join_keyboard():

    buttons = [
        [
            {
                "text": "📢 عضویت در کانال اصلی",
                "url": CHANNEL_URL
            }
        ]
    ]

    for sponsor in get_sponsors():

        title = (
            sponsor.get("title")
            or "اسپانسر"
        )

        link = sponsor.get("link")

        if link:

            buttons.append(
                [
                    {
                        "text": f"📢 {title}",
                        "url": link
                    }
                ]
            )

    buttons.append(
        [
            {
                "text": "✅ انجام شد",
                "callback_data": "check_membership"
            }
        ]
    )

    return {
        "inline_keyboard": buttons
    }


def reaction_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "📢 مشاهده ۵ پست آخر",
                    "url": CHANNEL_URL
                }
            ],
            [
                {
                    "text": "انجام شد ✅",
                    "callback_data": "check_reactions"
                }
            ]
        ]
    }


def redownload_keyboard(
    episode_key_value,
    file_index
):

    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔄 دانلود مجدد",
                    "callback_data": (
                        f"redownload|"
                        f"{episode_key_value}|"
                        f"{file_index}"
                    )
                }
            ]
        ]
    }


# =========================================================
# USER FLOW
# =========================================================

def send_join_page(
    user_id,
    episode_key_value,
    selected_type=None
):

    if check_membership(user_id):

        send_reaction_page(
            user_id,
            episode_key_value,
            selected_type
        )

        return

    set_pending(
        user_id,
        episode_key_value,
        selected_type
    )

    send_message(
        user_id,
        "برای دریافت فایل، ابتدا در کانال‌های زیر عضو شو 👇",
        reply_markup=join_keyboard()
    )


def send_reaction_page(
    user_id,
    episode_key_value,
    selected_type=None
):

    set_pending(
        user_id,
        episode_key_value,
        selected_type
    )

    send_message(
        user_id,
        "برای دریافت فایل، به ۵ پست آخر کانال "
        "@altiustuistsnbol ری‌اکشن ❤️ بزن.\n\n"
        "بعد از انجام ری‌اکشن‌ها، روی «انجام شد ✅» "
        "بزن تا فایل برات ارسال بشه.",
        reply_markup=reaction_keyboard()
    )


# =========================================================
# FILE SENDING
# =========================================================

def claim_delivery(user_id):

    with DELIVERING_LOCK:

        if user_id in DELIVERING:
            return False

        DELIVERING.add(user_id)

        return True


def release_delivery(user_id):

    with DELIVERING_LOCK:
        DELIVERING.discard(user_id)


def send_video(
    chat_id,
    file_id,
    caption="",
    caption_entities=None,
    has_media_spoiler=False
):

    data = {
        "chat_id": chat_id,
        "video": file_id
    }

    if caption:
        data["caption"] = caption

    if caption_entities:
        data["caption_entities"] = caption_entities

    if has_media_spoiler:
        data["has_media_spoiler"] = True

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


def send_file(
    chat_id,
    file_item
):

    file_type = normalize_type(
        file_item.get("type")
    )

    file_id = file_item.get("file_id")

    caption = file_item.get("caption") or ""

    caption_entities = (
        file_item.get("caption_entities")
        or []
    )

    if not file_id:
        return None

    if file_type == "video":

        return send_video(
            chat_id,
            file_id,
            caption,
            caption_entities,
            bool(
                file_item.get(
                    "has_media_spoiler",
                    False
                )
            )
        )

    if file_type == "document":

        return send_document(
            chat_id,
            file_id,
            caption,
            caption_entities
        )

    if file_type == "audio":

        return send_audio(
            chat_id,
            file_id,
            caption,
            caption_entities
        )

    if file_type == "photo":

        return send_photo(
            chat_id,
            file_id,
            caption,
            caption_entities
        )

    return None


def delete_files_later(
    chat_id,
    message_ids
):

    time.sleep(DELETE_AFTER)

    for message_id in message_ids:

        try:
            delete_message(
                chat_id,
                message_id
            )

        except Exception:
            pass


# =========================================================
# DELIVERY
# =========================================================

def deliver_episode(
    user_id,
    episode_key_value,
    selected_type=None
):

    if not claim_delivery(user_id):
        return

    try:

        episode = get_episode(
            episode_key_value
        )

        if not episode:
            episode = load_episode_from_db(
                episode_key_value
            )

        if not episode:

            send_message(
                user_id,
                "❌ این قسمت پیدا نشد."
            )

            return

        files = episode.get("files") or []

        selected_files = []

        if selected_type:

            selected_type = normalize_type(
                selected_type
            )

            for index, file_item in enumerate(files):

                if (
                    normalize_type(
                        file_item.get("type")
                    )
                    == selected_type
                ):
                    selected_files.append(
                        (index, file_item)
                    )

        else:

            selected_files = list(
                enumerate(files)
            )

        if not selected_files:

            send_message(
                user_id,
                "❌ فایل موردنظر پیدا نشد."
            )

            return

        sent_ids = []
        successful_files = 0

        for index, file_item in selected_files:

            try:

                result = send_file(
                    user_id,
                    file_item
                )

                if result and result.get("ok"):

                    message_id = (
                        result
                        .get("result", {})
                        .get("message_id")
                    )

                    if message_id:
                        sent_ids.append(
                            message_id
                        )

                    successful_files += 1

            except Exception as e:

                print(
                    "send file error:",
                    e
                )

        if successful_files == 0:

            send_message(
                user_id,
                "❌ ارسال فایل انجام نشد."
            )

            return

        EXEC.submit(
            record_stat,
            user_id,
            "download",
            episode_key_value,
            selected_type,
            successful_files
        )

        first_index = selected_files[0][0]

        send_message(
            user_id,
            "⚠️ فایل‌های ارسال‌شده تا ۳۰ ثانیه دیگه حذف می‌شن.\n"
            "اگه دوباره لازم داشتی، از دکمه «دانلود مجدد» استفاده کن.",
            reply_markup=redownload_keyboard(
                episode_key_value,
                first_index
            )
        )

        if sent_ids:

            EXEC.submit(
                delete_files_later,
                user_id,
                sent_ids
            )

    finally:

        release_delivery(user_id)


# =========================================================
# EXTRACT ADMIN FILE
# =========================================================

def extract_file(message):

    caption = message.get("caption") or ""

    caption_entities = (
        message.get("caption_entities")
        or []
    )

    if message.get("video"):

        video = message["video"]

        return {
            "type": "video",
            "file_id": video.get("file_id"),
            "caption": caption,
            "caption_entities": caption_entities,
            "has_media_spoiler": bool(
                message.get(
                    "has_media_spoiler",
                    False
                )
            )
        }

    if message.get("document"):

        document = message["document"]

        return {
            "type": "document",
            "file_id": document.get("file_id"),
            "caption": caption,
            "caption_entities": caption_entities
        }

    if message.get("audio"):

        audio = message["audio"]

        return {
            "type": "audio",
            "file_id": audio.get("file_id"),
            "caption": caption,
            "caption_entities": caption_entities
        }

    if message.get("photo"):

        photo = message["photo"][-1]

        return {
            "type": "photo",
            "file_id": photo.get("file_id"),
            "caption": caption,
            "caption_entities": caption_entities
        }

    return None


def handle_admin_file(message):

    file_item = extract_file(message)

    if not file_item:

        send_message(
            ADMIN_ID,
            "❌ فقط ویدیو، فایل، عکس یا صوت بفرست."
        )

        return

    caption = message.get("caption") or ""

    series_name, episode_number = parse_caption(
        caption
    )

    if not series_name or not episode_number:

        send_message(
            ADMIN_ID,
            "❌ از داخل کپشن نتونستم نام سریال و "
            "شماره قسمت رو پیدا کنم.\n\n"
            "مثلاً:\n"
            "🪴 سریال « بالا پایین استانبول»\n"
            "🪷 قسمت : 14"
        )

        return

    key = episode_key(
        series_name,
        episode_number
    )

    old = get_episode(key)

    if not old:
        old = load_episode_from_db(key)

    files = []

    if old:
        files.extend(
            old.get("files") or []
        )

    files.append(file_item)

    ok = save_episode(
        series_name,
        episode_number,
        files,
        preview=False
    )

    if not ok:

        send_message(
            ADMIN_ID,
            "❌ ذخیره قسمت انجام نشد."
        )

        return

    EXEC.submit(
        record_stat,
        ADMIN_ID,
        "upload",
        key,
        file_item.get("type"),
        1
    )

    send_message(
        ADMIN_ID,
        "✅ فایل با موفقیت ذخیره شد.\n\n"
        f"🎬 سریال: {series_name}\n"
        f"🪷 قسمت: {episode_number}\n"
        f"📦 تعداد فایل: {len(files)}\n\n"
        f"🔗 لینک قسمت:\n"
        f"https://t.me/{BOT_USERNAME}?start=ep_{key}"
    )


# =========================================================
# ADMIN PANEL
# =========================================================

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
                },
                {
                    "text": "🔄 سینک دیتابیس",
                    "callback_data": "admin:sync"
                }
            ],

            [
                {
                    "text": "⚡ وضعیت ربات",
                    "callback_data": "admin:status"
                }
            ]
        ]
    }


def send_admin_panel(chat_id):

    return send_message(
        chat_id,
        "⚙️ پنل مدیریت ربات\n\n"
        "تمام قابلیت‌های مدیریت از همین پنل در دسترسه:",
        reply_markup=admin_panel_keyboard()
    )


def back_panel_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "↩️ پنل اصلی",
                    "callback_data": "admin:panel"
                }
            ]
        ]
    }


# =========================================================
# SPONSOR ADMIN
# =========================================================

def sponsors_text():

    sponsors = get_sponsors()

    if not sponsors:

        return (
            "📢 اسپانسرها\n\n"
            "فعلاً هیچ اسپانسری ثبت نشده."
        )

    lines = [
        "📢 اسپانسرهای فعلی:",
        ""
    ]

    for sponsor in sponsors:

        lines.append(
            f"🆔 {sponsor.get('id')}\n"
            f"📛 {sponsor.get('title') or '-'}\n"
            f"👤 {sponsor.get('username') or '-'}\n"
            f"🔗 {sponsor.get('link') or '-'}\n"
        )

    return "\n".join(lines)


def sponsor_keyboard():

    return {
        "inline_keyboard": [

            [
                {
                    "text": "➕ افزودن اسپانسر",
                    "callback_data": "admin:add_sponsor"
                },
                {
                    "text": "🗑 حذف اسپانسر",
                    "callback_data": "admin:remove_sponsor"
                }
            ],

            [
                {
                    "text": "↩️ پنل اصلی",
                    "callback_data": "admin:panel"
                }
            ]
        ]
    }


# =========================================================
# ADMIN STATES
# =========================================================

def set_admin_state(state):

    with ADMIN_STATE_LOCK:
        ADMIN_STATE[ADMIN_ID] = state


def get_admin_state():

    with ADMIN_STATE_LOCK:
        return ADMIN_STATE.get(ADMIN_ID)


def clear_admin_state():

    with ADMIN_STATE_LOCK:
        ADMIN_STATE.pop(ADMIN_ID, None)


# =========================================================
# BROADCAST
# =========================================================

def get_broadcast_users():

    rows = []

    start = 0
    page_size = 1000

    while True:

        try:

            result = (
                supabase
                .table("bot_users")
                .select("user_id")
                .eq("is_blocked", False)
                .neq("user_id", ADMIN_ID)
                .range(
                    start,
                    start + page_size - 1
                )
                .execute()
            )

            batch = result.data or []

            rows.extend(batch)

            if len(batch) < page_size:
                break

            start += page_size

            if start >= 100000:
                break

        except Exception:
            break

    return rows


def broadcast_worker(
    from_chat_id,
    message_id
):

    global BROADCAST_RUNNING

    success = 0
    failed = 0

    try:

        users = get_broadcast_users()

        for row in users:

            if BROADCAST_CANCEL.is_set():
                break

            user_id = row.get("user_id")

            if not user_id:
                continue

            result = copy_message(
                user_id,
                from_chat_id,
                message_id
            )

            if result.get("ok"):

                success += 1

            else:

                failed += 1

                if result.get("error_code") == 403:

                    EXEC.submit(
                        mark_user_blocked,
                        user_id
                    )

            time.sleep(0.05)

        cancelled = BROADCAST_CANCEL.is_set()

        EXEC.submit(
            record_stat,
            ADMIN_ID,
            "broadcast_sent",
            None,
            None,
            success
        )

        EXEC.submit(
            record_stat,
            ADMIN_ID,
            "broadcast_failed",
            None,
            None,
            failed
        )

        if cancelled:

            send_message(
                ADMIN_ID,
                "🛑 پیام همگانی متوقف شد.\n\n"
                f"✅ موفق: {success:,}\n"
                f"❌ ناموفق: {failed:,}"
            )

        else:

            send_message(
                ADMIN_ID,
                "✅ پیام همگانی تمام شد.\n\n"
                f"👥 گیرنده‌ها: {len(users):,}\n"
                f"✅ موفق: {success:,}\n"
                f"❌ ناموفق: {failed:,}"
            )

    except Exception as e:

        print("broadcast_worker:", e)

        send_message(
            ADMIN_ID,
            "❌ پیام همگانی با خطا متوقف شد."
        )

    finally:

        BROADCAST_CANCEL.clear()

        with BROADCAST_LOCK:
            BROADCAST_RUNNING = False


def start_broadcast(message):

    global BROADCAST_RUNNING

    with BROADCAST_LOCK:

        if BROADCAST_RUNNING:

            send_message(
                ADMIN_ID,
                "⏳ یک پیام همگانی در حال اجراست."
            )

            return

        BROADCAST_RUNNING = True

    clear_admin_state()

    BROADCAST_CANCEL.clear()

    send_message(
        ADMIN_ID,
        "📢 پیام همگانی شروع شد.\n"
        "ارسال در پس‌زمینه انجام می‌شه."
    )

    MEDIA_EXEC.submit(
        broadcast_worker,
        ADMIN_ID,
        message.get("message_id")
    )


# =========================================================
# ADVANCED STATS
# =========================================================

def build_advanced_stats():

    now = datetime.now(timezone.utc)

    today = utc_day_start()
    month = utc_month_start()

    today_s = today.isoformat()
    month_s = month.isoformat()
    now_s = now.isoformat()

    total_users = count_rows(
        "bot_users"
    )

    active_users = count_rows(
        "bot_users",
        equals={
            "is_blocked": False
        }
    )

    new_users_today = count_rows(
        "bot_users",
        gte=today_s,
        lt=now_s
    )

    new_users_month = count_rows(
        "bot_users",
        gte=month_s,
        lt=now_s
    )

    starts_today = count_rows(
        "bot_stats",
        equals={
            "event_type": "start"
        },
        gte=today_s,
        lt=now_s
    )

    starts_month = count_rows(
        "bot_stats",
        equals={
            "event_type": "start"
        },
        gte=month_s,
        lt=now_s
    )

    downloads_today = count_rows(
        "bot_stats",
        equals={
            "event_type": "download"
        },
        gte=today_s,
        lt=now_s
    )

    downloads_month = count_rows(
        "bot_stats",
        equals={
            "event_type": "download"
        },
        gte=month_s,
        lt=now_s
    )

    downloads_total = count_rows(
        "bot_stats",
        equals={
            "event_type": "download"
        }
    )

    download_rows_month = fetch_all_rows(
        "bot_stats",
        select="event_type,file_count,episode_key",
        equals={
            "event_type": "download"
        },
        gte=month_s,
        lt=now_s,
        max_rows=50000
    )

    files_month = sum(
        int(x.get("file_count") or 0)
        for x in download_rows_month
    )

    thirty_days_ago = (
        now - timedelta(days=30)
    )

    recent_downloads = fetch_all_rows(
        "bot_stats",
        select="episode_key",
        equals={
            "event_type": "download"
        },
        gte=thirty_days_ago.isoformat(),
        lt=now_s,
        max_rows=50000
    )

    episode_counts = {}

    for row in recent_downloads:

        key = row.get("episode_key")

        if key:

            episode_counts[key] = (
                episode_counts.get(key, 0) + 1
            )

    top = sorted(
        episode_counts.items(),
        key=lambda x: x[1],
        reverse=True
    )[:10]

    broadcast_sent_rows = fetch_all_rows(
        "bot_stats",
        select="file_count",
        equals={
            "event_type": "broadcast_sent"
        },
        max_rows=10000
    )

    broadcast_failed_rows = fetch_all_rows(
        "bot_stats",
        select="file_count",
        equals={
            "event_type": "broadcast_failed"
        },
        max_rows=10000
    )

    broadcast_sent = sum(
        int(x.get("file_count") or 0)
        for x in broadcast_sent_rows
    )

    broadcast_failed = sum(
        int(x.get("file_count") or 0)
        for x in broadcast_failed_rows
    )

    lines = [
        "📊 آمار پیشرفته",
        "",
        f"👥 کل کاربران: {total_users:,}",
        f"🟢 کاربران فعال: {active_users:,}",
        f"🆕 کاربران جدید امروز: {new_users_today:,}",
        f"🆕 کاربران جدید این ماه: {new_users_month:,}",
        "",
        f"🚀 /start امروز: {starts_today:,}",
        f"🚀 /start این ماه: {starts_month:,}",
        "",
        f"📥 دانلود امروز: {downloads_today:,}",
        f"📥 دانلود این ماه: {downloads_month:,}",
        f"📥 دانلود کل: {downloads_total:,}",
        f"📦 فایل‌های دانلودشده این ماه: {files_month:,}",
        "",
        f"📢 پیام همگانی موفق: {broadcast_sent:,}",
        f"❌ پیام همگانی ناموفق: {broadcast_failed:,}",
        "",
        "🏆 ۱۰ قسمت پربازدید ۳۰ روز اخیر:"
    ]

    if not top:

        lines.append(
            "— هنوز آماری ثبت نشده"
        )

    else:

        for i, (key, count) in enumerate(
            top,
            1
        ):

            episode = get_episode(key)

            if episode:

                title = (
                    f"{episode.get('series_name', '')} "
                    f"قسمت "
                    f"{episode.get('episode_number', '')}"
                ).strip()

            else:

                title = key

            lines.append(
                f"{i}. {title} — "
                f"{count:,} دانلود"
            )

    return "\n".join(lines)


# =========================================================
# ADMIN COMMANDS
# =========================================================

def handle_admin_command(message):

    text = (
        message.get("text")
        or message.get("caption")
        or ""
    ).strip()

    if not text.startswith("/"):
        return False

    command = (
        text
        .split()[0]
        .split("@")[0]
        .lower()
    )

    if command == "/start":

        clear_admin_state()

        send_admin_panel(
            ADMIN_ID
        )

        return True

    if command in (
        "/cancel",
        "/cancel_broadcast"
    ):

        clear_admin_state()

        if command == "/cancel_broadcast":

            if BROADCAST_RUNNING:

                BROADCAST_CANCEL.set()

                send_message(
                    ADMIN_ID,
                    "🛑 درخواست توقف پیام همگانی ثبت شد."
                )

            else:

                send_message(
                    ADMIN_ID,
                    "❌ پیام همگانی فعالی وجود نداره."
                )

        else:

            send_message(
                ADMIN_ID,
                "❌ عملیات لغو شد."
            )

        return True

    if command == "/sponsors":

        send_message(
            ADMIN_ID,
            sponsors_text(),
            reply_markup=sponsor_keyboard()
        )

        return True

    if command == "/add_sponsor":

        parts = text.split("|")

        if len(parts) != 3:

            send_message(
                ADMIN_ID,
                "فرمت درست:\n\n"
                "/add_sponsor @channel | نام کانال | "
                "https://t.me/channel"
            )

            return True

        username = (
            parts[0]
            .replace("/add_sponsor", "", 1)
            .strip()
        )

        title = parts[1].strip()
        link = parts[2].strip()

        if not username.startswith("@"):
            username = "@" + username

        result = add_sponsor(
            username,
            title,
            link
        )

        send_message(
            ADMIN_ID,
            (
                "✅ اسپانسر با موفقیت اضافه شد."
                if result
                else
                "❌ ذخیره اسپانسر انجام نشد."
            )
        )

        return True

    if command == "/remove_sponsor":

        parts = text.split(
            maxsplit=1
        )

        if len(parts) != 2:

            send_message(
                ADMIN_ID,
                "فرمت درست:\n/remove_sponsor ID"
            )

            return True

        try:
            sponsor_id = int(parts[1])

        except Exception:

            send_message(
                ADMIN_ID,
                "❌ ID باید عدد باشه."
            )

            return True

        result = remove_sponsor(
            sponsor_id
        )

        send_message(
            ADMIN_ID,
            (
                "✅ اسپانسر حذف شد."
                if result
                else
                "❌ حذف اسپانسر انجام نشد."
            )
        )

        return True

    if command == "/delete_episode":

        parts = text.split(
            maxsplit=1
        )

        if len(parts) != 2:

            send_message(
                ADMIN_ID,
                "فرمت:\n/delete_episode episode_key"
            )

            return True

        result = delete_episode(
            parts[1].strip()
        )

        send_message(
            ADMIN_ID,
            (
                "✅ قسمت حذف شد."
                if result
                else
                "❌ حذف قسمت انجام نشد."
            )
        )

        return True

    if command == "/delete_all":

        result = delete_all_episodes()

        send_message(
            ADMIN_ID,
            (
                "✅ تمام قسمت‌ها حذف شدند."
                if result
                else
                "❌ حذف همه قسمت‌ها انجام نشد."
            )
        )

        return True

    if command == "/broadcast":

        set_admin_state(
            "broadcast_waiting"
        )

        send_message(
            ADMIN_ID,
            "📢 پیام همگانی\n\n"
            "حالا همون پیامی که می‌خوای برای کاربران "
            "ارسال بشه رو بفرست.\n"
            "متن، عکس، ویدیو یا فایل هم می‌تونی بفرستی.\n\n"
            "برای لغو: /cancel_broadcast"
        )

        return True

    return False


# =========================================================
# ADMIN STATES
# =========================================================

def handle_admin_state(message):

    state = get_admin_state()

    if not state:
        return False

    text = (
        message.get("text")
        or message.get("caption")
        or ""
    ).strip()

    if text.startswith("/"):
        return False

    if state == "broadcast_waiting":

        start_broadcast(
            message
        )

        return True

    if state == "add_sponsor":

        parts = text.split("|")

        if len(parts) != 3:

            send_message(
                ADMIN_ID,
                "فرمت درست:\n\n"
                "@channel | نام کانال | "
                "https://t.me/channel"
            )

            return True

        username = parts[0].strip()
        title = parts[1].strip()
        link = parts[2].strip()

        if not username.startswith("@"):
            username = "@" + username

        result = add_sponsor(
            username,
            title,
            link
        )

        clear_admin_state()

        send_message(
            ADMIN_ID,
            (
                "✅ اسپانسر اضافه شد."
                if result
                else
                "❌ ذخیره اسپانسر انجام نشد."
            ),
            reply_markup=(
                sponsor_keyboard()
                if result
                else None
            )
        )

        return True

    if state == "remove_sponsor":

        try:
            sponsor_id = int(text)

        except Exception:

            send_message(
                ADMIN_ID,
                "❌ فقط ID اسپانسر رو بفرست."
            )

            return True

        result = remove_sponsor(
            sponsor_id
        )

        clear_admin_state()

        send_message(
            ADMIN_ID,
            (
                "✅ اسپانسر حذف شد."
                if result
                else
                "❌ حذف اسپانسر انجام نشد."
            ),
            reply_markup=(
                sponsor_keyboard()
                if result
                else None
            )
        )

        return True

    if state == "delete_episode":

        result = delete_episode(
            text
        )

        clear_admin_state()

        send_message(
            ADMIN_ID,
            (
                "✅ قسمت حذف شد."
                if result
                else
                "❌ حذف قسمت انجام نشد."
            )
        )

        return True

    if state == "episode":

        if any(
            key in message
            for key in (
                "video",
                "document",
                "audio",
                "photo"
            )
        ):

            handle_admin_file(
                message
            )

            return True

        send_message(
            ADMIN_ID,
            "📦 فایل قسمت رو بفرست و کپشنش هم همراهش باشه."
        )

        return True

    return False


# =========================================================
# EPISODE LIST
# =========================================================

def list_episodes_text():

    with CACHE_LOCK:
        episodes = list(
            EPISODES.values()
        )

    episodes = [
        x for x in episodes
        if not is_preview_episode(
            x.get("episode_key")
        )
    ]

    episodes.sort(
        key=lambda x: (
            str(x.get("series_name", "")),
            str(x.get("episode_number", ""))
        )
    )

    if not episodes:

        return "📋 هیچ قسمتی ذخیره نشده."

    lines = [
        "📋 لیست قسمت‌ها:",
        ""
    ]

    for index, episode in enumerate(
        episodes,
        1
    ):

        lines.append(
            f"{index}. "
            f"{episode.get('series_name', '')}"
            f" — قسمت "
            f"{episode.get('episode_number', '')}\n"
            f"🔑 {episode.get('episode_key', '')}\n"
            f"📦 {len(episode.get('files') or [])} فایل\n"
        )

    return "\n".join(lines)


# =========================================================
# ADMIN CALLBACKS
# =========================================================

def handle_admin_callback(callback):

    callback_id = callback.get("id")

    data = callback.get("data") or ""

    message = (
        callback.get("message")
        or {}
    )

    chat_id = (
        message
        .get("chat", {})
        .get("id")
    )

    message_id = message.get(
        "message_id"
    )

    answer_callback(
        callback_id
    )

    if data == "admin:panel":

        clear_admin_state()

        edit_message_text(
            chat_id,
            message_id,
            "⚙️ پنل مدیریت ربات\n\n"
            "تمام قابلیت‌های مدیریت از همین پنل در دسترسه:",
            reply_markup=admin_panel_keyboard()
        )

        return

    if data == "admin:episodes":

        set_admin_state(
            "episode"
        )

        edit_message_text(
            chat_id,
            message_id,
            "🎬 مدیریت قسمت‌ها\n\n"
            "ویدیو، فایل، عکس یا صوت قسمت رو با کپشن بفرست.\n\n"
            "نام سریال و شماره قسمت باید داخل کپشن مشخص باشه.",
            reply_markup=back_panel_keyboard()
        )

        return

    if data == "admin:sponsors":

        clear_admin_state()

        edit_message_text(
            chat_id,
            message_id,
            sponsors_text(),
            reply_markup=sponsor_keyboard()
        )

        return

    if data == "admin:add_sponsor":

        set_admin_state(
            "add_sponsor"
        )

        edit_message_text(
            chat_id,
            message_id,
            "➕ افزودن اسپانسر\n\n"
            "این فرمت رو بفرست:\n\n"
            "@channel | نام کانال | https://t.me/channel",
            reply_markup=back_panel_keyboard()
        )

        return

    if data == "admin:remove_sponsor":

        set_admin_state(
            "remove_sponsor"
        )

        edit_message_text(
            chat_id,
            message_id,
            "🗑 حذف اسپانسر\n\n"
            "ID اسپانسر رو بفرست.",
            reply_markup=back_panel_keyboard()
        )

        return

    if data == "admin:broadcast":

        if BROADCAST_RUNNING:

            edit_message_text(
                chat_id,
                message_id,
                "⏳ یک پیام همگانی در حال اجراست.",
                reply_markup=back_panel_keyboard()
            )

            return

        set_admin_state(
            "broadcast_waiting"
        )

        edit_message_text(
            chat_id,
            message_id,
            "📢 پیام همگانی\n\n"
            "حالا پیامی که می‌خوای برای کاربران ارسال بشه رو بفرست.\n"
            "متن، عکس، ویدیو یا فایل قابل ارساله.\n\n"
            "برای لغو: /cancel_broadcast",
            reply_markup=back_panel_keyboard()
        )

        return

    if data == "admin:stats":

        clear_admin_state()

        send_message(
            chat_id,
            build_advanced_stats(),
            reply_markup=back_panel_keyboard()
        )

        return

    if data == "admin:list":

        clear_admin_state()

        send_message(
            chat_id,
            list_episodes_text(),
            reply_markup=back_panel_keyboard()
        )

        return

    if data == "admin:delete":

        set_admin_state(
            "delete_episode"
        )

        edit_message_text(
            chat_id,
            message_id,
            "🗑 حذف قسمت\n\n"
            "episode_key قسمت رو بفرست.\n\n"
            "مثال:\n"
            "altiustu_istanbul_14",
            reply_markup=back_panel_keyboard()
        )

        return

    if data == "admin:delete_all":

        clear_admin_state()

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "⚠️ بله، همه حذف شوند",
                        "callback_data": "admin:delete_all_yes"
                    }
                ],
                [
                    {
                        "text": "❌ لغو",
                        "callback_data": "admin:panel"
                    }
                ]
            ]
        }

        edit_message_text(
            chat_id,
            message_id,
            "⚠️ مطمئنی می‌خوای تمام قسمت‌ها حذف بشن؟",
            reply_markup=keyboard
        )

        return

    if data == "admin:delete_all_yes":

        result = delete_all_episodes()

        edit_message_text(
            chat_id,
            message_id,
            (
                "✅ تمام قسمت‌ها حذف شدند."
                if result
                else
                "❌ حذف همه قسمت‌ها انجام نشد."
            ),
            reply_markup=back_panel_keyboard()
        )

        return

    if data == "admin:sync":

        ok = sync_cache()

        edit_message_text(
            chat_id,
            message_id,
            (
                f"✅ کش دیتابیس با موفقیت سینک شد.\n\n"
                f"🎬 قسمت‌ها: {len(EPISODES)}\n"
                f"📢 اسپانسرها: {len(SPONSORS)}"
                if ok
                else
                "❌ سینک دیتابیس ناموفق بود."
            ),
            reply_markup=back_panel_keyboard()
        )

        return

    if data == "admin:status":

        edit_message_text(
            chat_id,
            message_id,
            "⚡ وضعیت ربات\n\n"
            "🟢 ربات فعال است\n"
            f"🎬 قسمت‌های کش‌شده: {len(EPISODES)}\n"
            f"📢 اسپانسرها: {len(SPONSORS)}\n"
            f"💾 کاربران در انتظار: {len(PENDING)}\n"
            f"🚚 در حال ارسال: {len(DELIVERING)}\n"
            f"⏱ سینک کش: هر {CACHE_SYNC_INTERVAL} ثانیه\n"
            "🗑 حذف فایل‌ها: ۳۰ ثانیه",
            reply_markup=back_panel_keyboard()
        )

        return


# =========================================================
# USER CALLBACKS
# =========================================================

def handle_user_callback(callback):

    callback_id = callback.get("id")

    data = callback.get("data") or ""

    from_user = (
        callback.get("from")
        or {}
    )

    user_id = from_user.get("id")

    message = (
        callback.get("message")
        or {}
    )

    chat_id = (
        message
        .get("chat", {})
        .get("id")
    )

    message_id = message.get(
        "message_id"
    )

    EXEC.submit(
        register_user,
        from_user
    )

    # -----------------------------------------
    # MEMBERSHIP
    # -----------------------------------------

    if data == "check_membership":

        answer_callback(
            callback_id,
            "در حال بررسی عضویت..."
        )

        if check_membership(user_id):

            try:
                delete_message(
                    chat_id,
                    message_id
                )

            except Exception:
                pass

            pending = get_pending(
                user_id
            )

            if not pending:

                send_message(
                    user_id,
                    "❌ لینک دریافت فایل منقضی شده."
                )

                return

            send_reaction_page(
                user_id,
                pending["episode_key"],
                pending.get("selected_type")
            )

        else:

            answer_callback(
                callback_id,
                "❌ هنوز در همه کانال‌ها عضو نشدی.",
                True
            )

        return

    # -----------------------------------------
    # SYMBOLIC REACTION
    # -----------------------------------------

    if data == "check_reactions":

        answer_callback(
            callback_id,
            "در حال آماده‌سازی فایل..."
        )

        pending = get_pending(
            user_id
        )

        if not pending:

            send_message(
                user_id,
                "❌ لینک دریافت فایل منقضی شده."
            )

            return

        clear_pending(
            user_id
        )

        EXEC.submit(
            deliver_episode,
            user_id,
            pending["episode_key"],
            pending.get("selected_type")
        )

        return

    # -----------------------------------------
    # REDOWNLOAD
    # -----------------------------------------

    if data.startswith("redownload|"):

        parts = data.split("|")

        if len(parts) != 3:

            answer_callback(
                callback_id,
                "❌ لینک نامعتبر است.",
                True
            )

            return

        episode_key_value = parts[1]

        try:

            file_index = int(
                parts[2]
            )

        except Exception:

            answer_callback(
                callback_id,
                "❌ فایل نامعتبر است.",
                True
            )

            return

        episode = get_episode(
            episode_key_value
        )

        if not episode:

            episode = load_episode_from_db(
                episode_key_value
            )

        if not episode:

            answer_callback(
                callback_id,
                "❌ قسمت پیدا نشد.",
                True
            )

            return

        files = episode.get(
            "files"
        ) or []

        if (
            file_index < 0
            or file_index >= len(files)
        ):

            answer_callback(
                callback_id,
                "❌ فایل پیدا نشد.",
                True
            )

            return

        file_type = normalize_type(
            files[file_index].get("type")
        )

        if not check_membership(user_id):

            answer_callback(
                callback_id,
                "❌ ابتدا عضویت کانال‌ها را کامل کن.",
                True
            )

            send_join_page(
                user_id,
                episode_key_value,
                file_type
            )

            return

        answer_callback(
            callback_id,
            "در حال ارسال..."
        )

        EXEC.submit(
            deliver_episode,
            user_id,
            episode_key_value,
            file_type
        )

        return

    answer_callback(
        callback_id
    )


# =========================================================
# START LINK
# =========================================================

def handle_start(
    user_id,
    parameter
):

    if not parameter:

        send_message(
            user_id,
            "سلام 👋\n"
            "برای دریافت فایل، لینک قسمت رو باز کن."
        )

        return

    parameter = parameter.strip()

    selected_type = None
    episode_key_value = None

    # =========================================
    # TYPE LINK
    # =========================================

    if parameter.startswith("t_"):

        with CACHE_LOCK:

            target = TYPE_INDEX.get(
                parameter
            )

        if not target:

            # اول سینک
            sync_cache()

            with CACHE_LOCK:

                target = TYPE_INDEX.get(
                    parameter
                )

        if not target:

            send_message(
                user_id,
                "❌ لینک فایل پیدا نشد."
            )

            return

        episode_key_value, file_index = target

        episode = get_episode(
            episode_key_value
        )

        if not episode:

            episode = load_episode_from_db(
                episode_key_value
            )

        if not episode:

            send_message(
                user_id,
                "❌ قسمت پیدا نشد."
            )

            return

        files = episode.get(
            "files"
        ) or []

        if (
            file_index < 0
            or file_index >= len(files)
        ):

            send_message(
                user_id,
                "❌ فایل پیدا نشد."
            )

            return

        selected_type = normalize_type(
            files[file_index].get("type")
        )

    # =========================================
    # EPISODE LINK
    # =========================================

    else:

        if parameter.startswith("ep_"):
            parameter = parameter[3:]

        episode_key_value = parameter

        # اول کش
        episode = get_episode(
            episode_key_value
        )

        # اگر کش نبود، مستقیم دیتابیس
        if not episode:

            episode = load_episode_from_db(
                episode_key_value
            )

        # اگر باز نبود، سینک کامل
        if not episode:

            sync_cache()

            episode = get_episode(
                episode_key_value
            )

        if not episode:

            send_message(
                user_id,
                "❌ این قسمت پیدا نشد یا حذف شده."
            )

            return

    # =========================================
    # STATS
    # =========================================

    EXEC.submit(
        record_stat,
        user_id,
        "start",
        episode_key_value,
        selected_type,
        0
    )

    # =========================================
    # MEMBERSHIP
    # =========================================

    if not check_membership(user_id):

        send_join_page(
            user_id,
            episode_key_value,
            selected_type
        )

        return

    # =========================================
    # REACTION
    # =========================================

    send_reaction_page(
        user_id,
        episode_key_value,
        selected_type
    )


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = (
            request
            .get_json(
                force=True,
                silent=True
            )
            or {}
        )

        # =====================================
        # CALLBACK
        # =====================================

        if update.get("callback_query"):

            callback = update[
                "callback_query"
            ]

            from_user = (
                callback.get("from")
                or {}
            )

            user_id = from_user.get(
                "id"
            )

            if user_id == ADMIN_ID:

                handle_admin_callback(
                    callback
                )

            else:

                handle_user_callback(
                    callback
                )

            return jsonify({
                "ok": True
            })

        # =====================================
        # MESSAGE
        # =====================================

        message = update.get(
            "message"
        )

        if not message:

            return jsonify({
                "ok": True
            })

        from_user = (
            message.get("from")
            or {}
        )

        user_id = from_user.get(
            "id"
        )

        if not user_id:

            return jsonify({
                "ok": True
            })

        # ثبت کاربر در پس‌زمینه
        EXEC.submit(
            register_user,
            from_user
        )

        # =====================================
        # ADMIN
        # =====================================

        if user_id == ADMIN_ID:

            if handle_admin_command(
                message
            ):

                return jsonify({
                    "ok": True
                })

            if handle_admin_state(
                message
            ):

                return jsonify({
                    "ok": True
                })

            if any(
                key in message
                for key in (
                    "video",
                    "document",
                    "audio",
                    "photo"
                )
            ):

                handle_admin_file(
                    message
                )

                return jsonify({
                    "ok": True
                })

            return jsonify({
                "ok": True
            })

        # =====================================
        # USER /START
        # =====================================

        text = (
            message.get("text")
            or ""
        ).strip()

        if text.startswith("/start"):

            parts = text.split(
                maxsplit=1
            )

            parameter = (
                parts[1].strip()
                if len(parts) > 1
                else ""
            )

            handle_start(
                user_id,
                parameter
            )

            return jsonify({
                "ok": True
            })

        return jsonify({
            "ok": True
        })

    except Exception as e:

        print("WEBHOOK ERROR:", e)

        # تلگرام دوباره آپدیت را نفرستد
        return jsonify({
            "ok": True
        })


# =========================================================
# HOME / HEALTH
# =========================================================

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
        "sponsors": len(SPONSORS),
        "pending": len(PENDING),
        "delivering": len(DELIVERING)
    })


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():

    result = tg(
        "setWebhook",
        {
            "url": WEBHOOK_URL,
            "allowed_updates": [
                "message",
                "callback_query"
            ]
        }
    )

    print(
        "WEBHOOK:",
        result
    )

    return result


# =========================================================
# STARTUP
# =========================================================

def startup():

    global BOT_USERNAME

    me = get_me()

    if me.get("ok"):

        BOT_USERNAME = (
            me
            .get("result", {})
            .get("username")
            or ""
        )

        print(
            "BOT:",
            BOT_USERNAME
        )

    # اول دیتابیس
    sync_cache()

    # بعد وبهوک
    setup_webhook()

    # سینک خودکار هر ۹۰ ثانیه
    thread = threading.Thread(
        target=cache_loop,
        daemon=True
    )

    thread.start()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    startup()

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
