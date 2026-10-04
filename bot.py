import os
import re
import json
import time
import secrets
import string
import hashlib
import threading
from datetime import datetime, timezone, timedelta
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, request, jsonify
from supabase import create_client


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

# سینک کش هر 90 ثانیه
CACHE_SYNC_INTERVAL = max(
    30,
    int(os.getenv("CACHE_SYNC_INTERVAL", "90"))
)

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    "https://telegram-aeries-bot.onrender.com"
).rstrip("/")

WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/webhook"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is missing")


# =========================================================
# CLIENTS
# =========================================================

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

app = Flask(__name__)

SESSION = requests.Session()

EXEC = ThreadPoolExecutor(max_workers=16)
MEDIA_EXEC = ThreadPoolExecutor(max_workers=4)


# =========================================================
# CACHE
# =========================================================

EPISODES = {}
CODE_INDEX = {}
SPONSORS = {}

CACHE_READY = False
CACHE_LOCK = threading.RLock()

PENDING = {}
PENDING_LOCK = threading.RLock()

DELIVERING = set()
DELIVERING_LOCK = threading.Lock()

ADMIN_STATE = {}
ADMIN_STATE_LOCK = threading.RLock()

BROADCAST_RUNNING = False
BROADCAST_LOCK = threading.Lock()
BROADCAST_CANCEL = threading.Event()

EPISODE_DB_LOCK = threading.Lock()

BOT_USERNAME = ""


# =========================================================
# BASIC
# =========================================================

def now_utc():
    return datetime.now(timezone.utc)


def unique_code(length=6):
    chars = string.ascii_letters + string.digits

    while True:
        code = "".join(
            secrets.choice(chars)
            for _ in range(length)
        )

        with CACHE_LOCK:
            if code not in CODE_INDEX:
                return code


def tg(method, data=None, timeout=30):
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

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    return tg(
        "sendMessage",
        data
    )


def edit_message_text(
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
        "callback_query_id": callback_id
    }

    if text:
        data["text"] = text

    if show_alert:
        data["show_alert"] = True

    return tg(
        "answerCallbackQuery",
        data
    )


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
        },
        timeout=30
    )


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


def safe_db(call):
    try:
        return call()
    except Exception as e:
        print("SUPABASE ERROR:", repr(e))
        return None


# =========================================================
# ADMIN PANEL
# =========================================================

def admin_panel_keyboard():
    # فقط INLINE KEYBOARD
    # هیچ Reply Keyboard در این برنامه ساخته نمی‌شود.
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🎬 مدیریت قسمت‌ها",
                    "callback_data": "admin:episode"
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


def back_keyboard():
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


def delete_all_confirm_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "❌ لغو",
                    "callback_data": "admin:panel"
                },
                {
                    "text": "✅ حذف همه",
                    "callback_data": "admin:delete_all_confirm"
                }
            ]
        ]
    }


def send_admin_panel(chat_id):
    """
    مهم:
    Reply Keyboard قدیمی تلگرام را پاک می‌کند
    و بعد پنل Inline را می‌فرستد.
    """

    remove_result = send_message(
        chat_id,
        "⌛",
        reply_markup={
            "remove_keyboard": True
        }
    )

    result = send_message(
        chat_id,
        "⚙️ پنل مدیریت ربات\n\n"
        "قابلیت موردنظر رو انتخاب کن:",
        reply_markup=admin_panel_keyboard()
    )

    # پیام موقت را حذف می‌کنیم تا فقط پنل باقی بماند.
    if remove_result and remove_result.get("ok"):
        temp_message = (
            remove_result.get("result")
            or {}
        )

        temp_message_id = temp_message.get(
            "message_id"
        )

        if temp_message_id:
            delete_message(
                chat_id,
                temp_message_id
            )

    return result


# =========================================================
# USER STORAGE
# =========================================================

def register_user(user):
    if not user:
        return

    user_id = user.get("id")

    if not user_id:
        return

    if user_id == ADMIN_ID:
        return

    row = {
        "user_id": int(user_id),
        "first_name": user.get("first_name") or "",
        "last_name": user.get("last_name") or "",
        "username": user.get("username") or "",
        "last_seen": now_utc().isoformat()
    }

    def job():
        safe_db(
            lambda: supabase
            .table("bot_users")
            .upsert(
                row,
                on_conflict="user_id"
            )
            .execute()
        )

    EXEC.submit(job)


def mark_user_blocked(user_id):
    def job():
        safe_db(
            lambda: supabase
            .table("bot_users")
            .update({
                "is_blocked": True,
                "last_seen": now_utc().isoformat()
            })
            .eq(
                "user_id",
                int(user_id)
            )
            .execute()
        )

    EXEC.submit(job)


# =========================================================
# STATS
# =========================================================

def record_stat(
    user_id,
    event_type,
    episode_key=None,
    file_type=None,
    file_count=0
):
    if not user_id:
        return

    row = {
        "user_id": int(user_id),
        "event_type": event_type,
        "episode_key": episode_key,
        "file_type": file_type,
        "file_count": int(file_count or 0),
        "created_at": now_utc().isoformat()
    }

    def job():
        safe_db(
            lambda: supabase
            .table("bot_stats")
            .insert(row)
            .execute()
        )

    EXEC.submit(job)


def count_stats(
    event_type=None,
    start=None,
    end=None
):
    def run():
        q = (
            supabase
            .table("bot_stats")
            .select(
                "user_id",
                count="exact",
                head=True
            )
        )

        if event_type:
            q = q.eq(
                "event_type",
                event_type
            )

        if start:
            q = q.gte(
                "created_at",
                start.isoformat()
            )

        if end:
            q = q.lt(
                "created_at",
                end.isoformat()
            )

        return q.execute()

    result = safe_db(run)

    if not result:
        return 0

    return int(result.count or 0)


def fetch_rows(
    table,
    select="*",
    filters=None,
    page_size=1000,
    max_rows=50000
):
    rows = []
    offset = 0

    filters = filters or []

    while len(rows) < max_rows:

        def run():
            q = (
                supabase
                .table(table)
                .select(select)
            )

            for action, column, value in filters:

                if action == "eq":
                    q = q.eq(
                        column,
                        value
                    )

                elif action == "neq":
                    q = q.neq(
                        column,
                        value
                    )

                elif action == "gte":
                    q = q.gte(
                        column,
                        value
                    )

                elif action == "lt":
                    q = q.lt(
                        column,
                        value
                    )

            q = q.range(
                offset,
                offset + page_size - 1
            )

            return q.execute()

        result = safe_db(run)

        if not result:
            break

        batch = result.data or []

        if not batch:
            break

        rows.extend(batch)

        if len(batch) < page_size:
            break

        offset += page_size

    return rows[:max_rows]


def sum_stat_files(
    event_type,
    start=None,
    end=None
):
    filters = [
        (
            "eq",
            "event_type",
            event_type
        )
    ]

    if start:
        filters.append(
            (
                "gte",
                "created_at",
                start.isoformat()
            )
        )

    if end:
        filters.append(
            (
                "lt",
                "created_at",
                end.isoformat()
            )
        )

    rows = fetch_rows(
        "bot_stats",
        "file_count",
        filters=filters
    )

    return sum(
        int(row.get("file_count") or 0)
        for row in rows
    )


def get_user_count(blocked=None):
    def run():
        q = (
            supabase
            .table("bot_users")
            .select(
                "user_id",
                count="exact",
                head=True
            )
        )

        if blocked is not None:
            q = q.eq(
                "is_blocked",
                blocked
            )

        return q.execute()

    result = safe_db(run)

    if not result:
        return 0

    return int(result.count or 0)


def count_users_between(start, end):
    def run():
        return (
            supabase
            .table("bot_users")
            .select(
                "user_id",
                count="exact",
                head=True
            )
            .gte(
                "created_at",
                start.isoformat()
            )
            .lt(
                "created_at",
                end.isoformat()
            )
            .execute()
        )

    result = safe_db(run)

    if not result:
        return 0

    return int(result.count or 0)


def advanced_stats_text():
    now = now_utc()

    today_start = datetime(
        now.year,
        now.month,
        now.day,
        tzinfo=timezone.utc
    )

    tomorrow = (
        today_start +
        timedelta(days=1)
    )

    month_start = datetime(
        now.year,
        now.month,
        1,
        tzinfo=timezone.utc
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

    last_30 = now - timedelta(
        days=30
    )

    total_users = get_user_count()
    active_users = get_user_count(False)

    new_today = count_users_between(
        today_start,
        tomorrow
    )

    new_month = count_users_between(
        month_start,
        next_month
    )

    starts_today = count_stats(
        "start",
        today_start,
        tomorrow
    )

    starts_month = count_stats(
        "start",
        month_start,
        next_month
    )

    downloads_today = count_stats(
        "download",
        today_start,
        tomorrow
    )

    downloads_month = count_stats(
        "download",
        month_start,
        next_month
    )

    downloads_total = count_stats(
        "download"
    )

    files_today = sum_stat_files(
        "download",
        today_start,
        tomorrow
    )

    files_month = sum_stat_files(
        "download",
        month_start,
        next_month
    )

    broadcast_sent = sum_stat_files(
        "broadcast_sent"
    )

    broadcast_failed = sum_stat_files(
        "broadcast_failed"
    )

    rows = fetch_rows(
        "bot_stats",
        "episode_key",
        filters=[
            (
                "eq",
                "event_type",
                "download"
            ),
            (
                "gte",
                "created_at",
                last_30.isoformat()
            )
        ]
    )

    counter = Counter()

    for row in rows:
        key = row.get("episode_key")

        if key:
            counter[key] += 1

    top = counter.most_common(10)

    lines = [
        "📊 آمار پیشرفته ربات",
        "",
        f"👥 کل کاربران: {total_users:,}",
        f"🟢 کاربران فعال: {active_users:,}",
        f"🆕 کاربران جدید امروز: {new_today:,}",
        f"🆕 کاربران جدید این ماه: {new_month:,}",
        "",
        f"🚀 /start امروز: {starts_today:,}",
        f"🚀 /start این ماه: {starts_month:,}",
        "",
        f"📥 دانلود امروز: {downloads_today:,}",
        f"📥 دانلود این ماه: {downloads_month:,}",
        f"📥 دانلود کل: {downloads_total:,}",
        "",
        f"📦 فایل امروز: {files_today:,}",
        f"📦 فایل این ماه: {files_month:,}",
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
        for i, (episode_key_value, count) in enumerate(
            top,
            1
        ):
            episode = EPISODES.get(
                episode_key_value
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

                title = (
                    f"{name} - قسمت {number}"
                )

            else:
                title = episode_key_value

            lines.append(
                f"{i}. {title} — {count:,} دانلود"
            )

    return "\n".join(lines)


# =========================================================
# EPISODE HELPERS
# =========================================================

def normalize_name(value):
    value = str(
        value or ""
    ).strip()

    return re.sub(
        r"\s+",
        " ",
        value
    )


def series_key(series_name):
    return hashlib.sha1(
        normalize_name(
            series_name
        ).encode("utf-8")
    ).hexdigest()[:10]


def episode_key(
    series_name,
    episode_number
):
    return (
        f"ep_{series_key(series_name)}_"
        f"{int(episode_number)}"
    )


def normalize_type(value):
    value = normalize_name(
        value
    )

    if not value:
        return "نامشخص"

    if "زبان اصلی" in value:
        return "زبان اصلی"

    if "زیرنویس فوری" in value:
        return "زیرنویس فوری"

    if "زیرنویس مووی باز" in value:
        return "زیرنویس مووی باز"

    return value


def detect_file_type(caption):
    caption = caption or ""

    if "زبان اصلی" in caption:
        return "زبان اصلی"

    if "زیرنویس فوری" in caption:
        return "زیرنویس فوری"

    if "زیرنویس مووی باز" in caption:
        return "زیرنویس مووی باز"

    return "نامشخص"


def parse_caption(caption):
    caption = caption or ""

    match = re.search(
        r"(?:قسمت|episode)\s*[:：]?\s*(\d+)",
        caption,
        flags=re.IGNORECASE
    )

    if not match:
        return None

    episode_number = int(
        match.group(1)
    )

    series_name = None

    match = re.search(
        r"سریال\s*[«\"“]([^»\"”\n]+)[»\"”]",
        caption
    )

    if match:
        series_name = match.group(1).strip()

    if not series_name:
        for line in caption.splitlines():

            line = line.strip()

            if line.startswith("سریال"):
                line = re.sub(
                    r"^سریال\s*[:：]?\s*",
                    "",
                    line
                )

                line = line.strip(
                    " «»\"“”"
                )

                if line:
                    series_name = line
                    break

    if not series_name:
        return None

    return {
        "series_name": normalize_name(
            series_name
        ),
        "episode_number": episode_number,
        "file_type": detect_file_type(
            caption
        )
    }


def extract_file(message):
    caption = (
        message.get("caption")
        or ""
    )

    # بسیار مهم:
    # فرمت‌های Bold / Italic / Spoiler / Quote /
    # لینک و ... اینجا حفظ می‌شوند.
    caption_entities = (
        message.get("caption_entities")
        or []
    )

    if message.get("video"):
        video = message["video"]

        return {
            "type": "video",
            "file_id": video["file_id"],
            "caption": caption,
            "caption_entities": caption_entities,
            "has_media_spoiler": bool(
                video.get(
                    "has_media_spoiler",
                    False
                )
            )
        }

    if message.get("document"):
        document = message["document"]

        return {
            "type": "document",
            "file_id": document["file_id"],
            "caption": caption,
            "caption_entities": caption_entities
        }

    return None


def ensure_type_codes(
    episode,
    persist=True
):
    files = episode.get(
        "files"
    ) or []

    changed = False

    start_code = episode.get(
        "start_code"
    )

    if not start_code:
        start_code = unique_code()

        episode["start_code"] = (
            start_code
        )

        changed = True

    groups = {}

    for item in files:

        file_type = normalize_type(
            item.get("file_type")
            or detect_file_type(
                item.get("caption", "")
            )
        )

        item["file_type"] = file_type

        groups.setdefault(
            file_type,
            []
        ).append(item)

    for file_type, group in groups.items():

        existing_code = None

        for item in group:
            code = item.get(
                "type_code"
            )

            if code:
                existing_code = code
                break

        if not existing_code:
            existing_code = unique_code()
            changed = True

        for item in group:

            if item.get("type_code") != existing_code:
                item["type_code"] = existing_code
                changed = True

    for item in files:

        if not item.get("start_code"):
            item["start_code"] = start_code
            changed = True

        if "caption_entities" not in item:
            item["caption_entities"] = []
            changed = True

    if (
        changed
        and persist
        and episode.get("id")
    ):
        safe_db(
            lambda: supabase
            .table("episodes")
            .update({
                "start_code": start_code,
                "files": files
            })
            .eq(
                "id",
                episode["id"]
            )
            .execute()
        )

    return episode


# =========================================================
# CACHE
# =========================================================

def rebuild_code_index():
    CODE_INDEX.clear()

    for key, episode in EPISODES.items():

        start_code = episode.get(
            "start_code"
        )

        if start_code:
            CODE_INDEX[start_code] = (
                key,
                None
            )

        for item in (
            episode.get("files")
            or []
        ):
            code = item.get(
                "type_code"
            )

            if code:
                CODE_INDEX[code] = (
                    key,
                    item.get(
                        "file_type",
                        "نامشخص"
                    )
                )


def sync_cache():
    global CACHE_READY

    def load_episodes():
        return (
            supabase
            .table("episodes")
            .select("*")
            .order("id")
            .execute()
        )

    def load_sponsors():
        return (
            supabase
            .table("sponsors")
            .select("*")
            .order("id")
            .execute()
        )

    episode_result = safe_db(
        load_episodes
    )

    sponsor_result = safe_db(
        load_sponsors
    )

    new_episodes = {}
    new_sponsors = {}

    if episode_result:
        for row in (
            episode_result.data
            or []
        ):
            key = row.get(
                "episode_key"
            )

            if not key:
                continue

            files = row.get(
                "files"
            ) or []

            for item in files:

                item.setdefault(
                    "file_type",
                    normalize_type(
                        item.get(
                            "file_type"
                        )
                        or detect_file_type(
                            item.get(
                                "caption",
                                ""
                            )
                        )
                    )
                )

                item.setdefault(
                    "caption_entities",
                    []
                )

            row["files"] = files

            new_episodes[key] = row

    if sponsor_result:
        for row in (
            sponsor_result.data
            or []
        ):
            sponsor_id = row.get(
                "id"
            )

            if sponsor_id is not None:
                new_sponsors[
                    int(sponsor_id)
                ] = row

    with CACHE_LOCK:

        EPISODES.clear()
        EPISODES.update(
            new_episodes
        )

        SPONSORS.clear()
        SPONSORS.update(
            new_sponsors
        )

        rebuild_code_index()

        CACHE_READY = True

    print(
        f"CACHE SYNC: "
        f"{len(EPISODES)} episodes / "
        f"{len(SPONSORS)} sponsors"
    )


def cache_loop():
    while True:

        try:
            time.sleep(
                CACHE_SYNC_INTERVAL
            )

            sync_cache()

        except Exception as e:
            print(
                "CACHE LOOP ERROR:",
                repr(e)
            )


def get_episode(key):
    with CACHE_LOCK:
        return EPISODES.get(
            key
        )


def get_episode_by_code(code):
    code = str(
        code or ""
    ).strip()

    with CACHE_LOCK:

        result = CODE_INDEX.get(
            code
        )

        if not result:
            return None, None

        episode_key_value, file_type = result

        episode = EPISODES.get(
            episode_key_value
        )

        if not episode:
            return None, None

        return episode, file_type


# =========================================================
# SAVE / DELETE EPISODE
# =========================================================

def save_episode(
    series_name,
    episode_number,
    new_file
):
    key = episode_key(
        series_name,
        episode_number
    )

    with EPISODE_DB_LOCK:

        existing = get_episode(
            key
        )

        if existing:

            files = list(
                existing.get("files")
                or []
            )

            if not any(
                item.get("file_id")
                == new_file.get("file_id")
                for item in files
            ):
                files.append(
                    new_file
                )

            existing["files"] = files
            existing["series_name"] = (
                series_name
            )
            existing["episode_number"] = int(
                episode_number
            )

            ensure_type_codes(
                existing,
                persist=False
            )

            row = {
                "episode_key": key,
                "series_name": series_name,
                "episode_number": int(
                    episode_number
                ),
                "start_code": existing.get(
                    "start_code"
                ),
                "files": existing["files"]
            }

        else:

            new_episode = {
                "episode_key": key,
                "series_name": series_name,
                "episode_number": int(
                    episode_number
                ),
                "start_code": unique_code(),
                "files": [
                    new_file
                ]
            }

            ensure_type_codes(
                new_episode,
                persist=False
            )

            row = new_episode

        result = safe_db(
            lambda: supabase
            .table("episodes")
            .upsert(
                row,
                on_conflict="episode_key"
            )
            .execute()
        )

        if not result:
            return None

        saved = (
            result.data[0]
            if result.data
            else row
        )

        with CACHE_LOCK:

            EPISODES[key] = saved

            rebuild_code_index()

        return saved


def delete_episode(key):
    result = safe_db(
        lambda: supabase
        .table("episodes")
        .delete()
        .eq(
            "episode_key",
            key
        )
        .execute()
    )

    if result is None:
        return False

    with CACHE_LOCK:

        EPISODES.pop(
            key,
            None
        )

        rebuild_code_index()

    return True


def delete_all_episodes():
    result = safe_db(
        lambda: supabase
        .table("episodes")
        .delete()
        .neq(
            "episode_key",
            ""
        )
        .execute()
    )

    if result is None:
        return False

    with CACHE_LOCK:

        EPISODES.clear()

        rebuild_code_index()

    return True


# =========================================================
# PENDING
# =========================================================

def set_pending(
    user_id,
    episode_key_value,
    file_type
):
    with PENDING_LOCK:

        PENDING[int(user_id)] = {
            "episode_key": episode_key_value,
            "file_type": file_type,
            "created_at": time.time()
        }

    # برای باقی ماندن pending بعد از ری‌استارت
    def job():

        safe_db(
            lambda: supabase
            .table("pending")
            .upsert(
                {
                    "user_id": int(user_id),
                    "episode_key": episode_key_value,
                    "file_type": file_type
                },
                on_conflict="user_id"
            )
            .execute()
        )

    EXEC.submit(job)


def get_pending(user_id):
    with PENDING_LOCK:

        value = PENDING.get(
            int(user_id)
        )

    if value:
        return value

    result = safe_db(
        lambda: (
            supabase
            .table("pending")
            .select("*")
            .eq(
                "user_id",
                int(user_id)
            )
            .maybe_single()
            .execute()
        )
    )

    if result and result.data:

        value = {
            "episode_key": result.data.get(
                "episode_key"
            ),
            "file_type": result.data.get(
                "file_type"
            )
        }

        with PENDING_LOCK:
            PENDING[
                int(user_id)
            ] = value

        return value

    return None


def clear_pending(user_id):
    with PENDING_LOCK:

        PENDING.pop(
            int(user_id),
            None
        )

    def job():

        safe_db(
            lambda: supabase
            .table("pending")
            .delete()
            .eq(
                "user_id",
                int(user_id)
            )
            .execute()
        )

    EXEC.submit(job)


# =========================================================
# SPONSORS
# =========================================================

def get_sponsors():
    with CACHE_LOCK:
        return list(
            SPONSORS.values()
        )


def add_sponsor(
    channel,
    title,
    link
):
    channel = channel.strip()
    title = title.strip()
    link = link.strip()

    existing = safe_db(
        lambda: (
            supabase
            .table("sponsors")
            .select("*")
            .eq(
                "channel",
                channel
            )
            .maybe_single()
            .execute()
        )
    )

    if existing and existing.data:
        return (
            False,
            "این اسپانسر از قبل وجود دارد."
        )

    result = safe_db(
        lambda: (
            supabase
            .table("sponsors")
            .insert({
                "channel": channel,
                "title": title,
                "link": link
            })
            .execute()
        )
    )

    if not result:
        return (
            False,
            "ذخیره اسپانسر انجام نشد."
        )

    sync_cache()

    return (
        True,
        "اسپانسر اضافه شد."
    )


def remove_sponsor(sponsor_id):
    result = safe_db(
        lambda: (
            supabase
            .table("sponsors")
            .delete()
            .eq(
                "id",
                int(sponsor_id)
            )
            .execute()
        )
    )

    if result is None:
        return False

    sync_cache()

    return True


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

    member = (
        result.get("result")
        or {}
    )

    status = member.get(
        "status"
    )

    if status in (
        "member",
        "administrator",
        "creator"
    ):
        return True

    if status == "restricted":
        return bool(
            member.get(
                "is_member",
                False
            )
        )

    return False


def check_membership(user_id):

    if not member_ok(
        CHANNEL_ID,
        user_id
    ):
        return False

    for sponsor in get_sponsors():

        channel = sponsor.get(
            "channel"
        )

        if not channel:
            continue

        if not member_ok(
            channel,
            user_id
        ):
            return False

    return True


# =========================================================
# USER KEYBOARDS
# =========================================================

def join_keyboard():

    rows = [
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

        link = sponsor.get(
            "link"
        )

        if link:
            rows.append([
                {
                    "text": f"📢 {title}",
                    "url": link
                }
            ])

    rows.append([
        {
            "text": "✅ بررسی عضویت",
            "callback_data": "check_membership"
        }
    ])

    return {
        "inline_keyboard": rows
    }


def reaction_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "❤️ انجام شد",
                    "callback_data": "check_reactions"
                }
            ]
        ]
    }


def redownload_keyboard(code):
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔄 دانلود مجدد",
                    "callback_data": f"redownload:{code}"
                }
            ]
        ]
    }


# =========================================================
# USER FLOW
# =========================================================

def send_join_page(
    chat_id,
    user_id,
    message_id_to_delete=None
):
    if check_membership(
        user_id
    ):
        send_reaction_page(
            chat_id,
            user_id
        )
        return

    if message_id_to_delete:
        delete_message(
            chat_id,
            message_id_to_delete
        )

    send_message(
        chat_id,
        "برای دریافت فایل، اول در کانال‌های زیر عضو شو و بعد «بررسی عضویت» رو بزن:",
        reply_markup=join_keyboard()
    )


def send_reaction_page(
    chat_id,
    user_id
):
    send_message(
        chat_id,
        "عضویت تأیید شد ✅\n\n"
        "برای ادامه روی دکمه زیر بزن:",
        reply_markup=reaction_keyboard()
    )


# =========================================================
# DELIVERY
# =========================================================

def claim_delivery(user_id):
    with DELIVERING_LOCK:

        if user_id in DELIVERING:
            return False

        DELIVERING.add(
            user_id
        )

    return True


def release_delivery(user_id):
    with DELIVERING_LOCK:
        DELIVERING.discard(
            user_id
        )


def delete_file_later(
    chat_id,
    message_id
):
    def job():

        time.sleep(
            DELETE_AFTER
        )

        try:
            delete_message(
                chat_id,
                message_id
            )
        except Exception as e:
            print(
                "DELETE ERROR:",
                repr(e)
            )

    threading.Thread(
        target=job,
        daemon=True
    ).start()


def send_video(
    chat_id,
    file_id,
    caption=None,
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
        data["caption_entities"] = (
            caption_entities
        )

    if has_media_spoiler:
        data["has_media_spoiler"] = True

    return tg(
        "sendVideo",
        data,
        timeout=60
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
        data["caption_entities"] = (
            caption_entities
        )

    return tg(
        "sendDocument",
        data,
        timeout=60
    )


def send_file(
    chat_id,
    file_info
):
    file_type = file_info.get(
        "type"
    )

    file_id = file_info.get(
        "file_id"
    )

    caption = (
        file_info.get("caption")
        or ""
    )

    caption_entities = (
        file_info.get(
            "caption_entities"
        )
        or []
    )

    if file_type == "video":

        return send_video(
            chat_id,
            file_id,
            caption=caption,
            caption_entities=caption_entities,
            has_media_spoiler=bool(
                file_info.get(
                    "has_media_spoiler",
                    False
                )
            )
        )

    return send_document(
        chat_id,
        file_id,
        caption=caption,
        caption_entities=caption_entities
    )


def deliver_episode(
    chat_id,
    user_id,
    episode,
    selected_type=None,
    redownload_code=None
):
    if not episode:

        send_message(
            chat_id,
            "❌ قسمت پیدا نشد."
        )

        return

    if not claim_delivery(
        user_id
    ):

        send_message(
            chat_id,
            "⏳ ارسال قبلی هنوز در حال انجامه، چند لحظه صبر کن."
        )

        return

    try:

        files = (
            episode.get("files")
            or []
        )

        if selected_type:

            files = [
                item
                for item in files
                if normalize_type(
                    item.get("file_type")
                )
                ==
                normalize_type(
                    selected_type
                )
            ]

        if not files:

            send_message(
                chat_id,
                "❌ فایلی با این نوع پیدا نشد."
            )

            return

        sent_count = 0

        for item in files:

            result = send_file(
                chat_id,
                item
            )

            if (
                result
                and result.get("ok")
            ):

                sent_count += 1

                sent_message = (
                    result.get(
                        "result"
                    )
                    or {}
                )

                message_id = (
                    sent_message.get(
                        "message_id"
                    )
                )

                if message_id:
                    delete_file_later(
                        chat_id,
                        message_id
                    )

        if sent_count == 0:

            send_message(
                chat_id,
                "❌ ارسال فایل انجام نشد."
            )

            return

        record_stat(
            user_id,
            "download",
            episode_key=episode.get(
                "episode_key"
            ),
            file_type=selected_type or "همه",
            file_count=sent_count
        )

        code = redownload_code

        if not code:

            if selected_type:

                for item in (
                    episode.get("files")
                    or []
                ):

                    if (
                        normalize_type(
                            item.get("file_type")
                        )
                        ==
                        normalize_type(
                            selected_type
                        )
                    ):

                        code = item.get(
                            "type_code"
                        )

                        break

            if not code:
                code = episode.get(
                    "start_code"
                )

        send_message(
            chat_id,
            f"✅ {sent_count} فایل ارسال شد.\n\n"
            f"⚠️ فایل‌ها بعد از {DELETE_AFTER} ثانیه حذف می‌شن.",
            reply_markup=(
                redownload_keyboard(code)
                if code
                else None
            )
        )

    finally:

        release_delivery(
            user_id
        )

        clear_pending(
            user_id
        )


# =========================================================
# ADMIN STATE
# =========================================================

def admin_set_state(
    user_id,
    state
):
    with ADMIN_STATE_LOCK:
        ADMIN_STATE[
            int(user_id)
        ] = state


def admin_get_state(user_id):
    with ADMIN_STATE_LOCK:
        return ADMIN_STATE.get(
            int(user_id)
        )


def admin_clear_state(user_id):
    with ADMIN_STATE_LOCK:
        ADMIN_STATE.pop(
            int(user_id),
            None
        )


# =========================================================
# ADMIN INFO
# =========================================================

def status_text():

    with CACHE_LOCK:
        episode_count = len(
            EPISODES
        )

        sponsor_count = len(
            SPONSORS
        )

        code_count = len(
            CODE_INDEX
        )

        ready = CACHE_READY

    user_count = get_user_count()

    return (
        "⚡ وضعیت ربات\n\n"
        f"🟢 کش آماده: "
        f"{'بله' if ready else 'خیر'}\n"
        f"🎬 تعداد قسمت‌ها: "
        f"{episode_count:,}\n"
        f"🔗 تعداد لینک‌ها: "
        f"{code_count:,}\n"
        f"📢 اسپانسرها: "
        f"{sponsor_count:,}\n"
        f"👥 کاربران: "
        f"{user_count:,}\n"
        f"🔄 سینک کش: "
        f"هر {CACHE_SYNC_INTERVAL} ثانیه\n"
        f"🗑 حذف فایل: "
        f"{DELETE_AFTER} ثانیه"
    )


def episode_list_text():

    with CACHE_LOCK:
        episodes = list(
            EPISODES.values()
        )

    episodes.sort(
        key=lambda x: (
            x.get(
                "series_name",
                ""
            ),
            int(
                x.get(
                    "episode_number"
                )
                or 0
            )
        ),
        reverse=True
    )

    if not episodes:
        return "📋 هنوز قسمتی ثبت نشده."

    lines = [
        "📋 لیست قسمت‌ها:",
        ""
    ]

    for episode in episodes[:30]:

        series = episode.get(
            "series_name",
            "نامشخص"
        )

        number = episode.get(
            "episode_number",
            "?"
        )

        key = episode.get(
            "episode_key",
            ""
        )

        lines.append(
            f"🎬 {series} — قسمت {number}"
        )

        lines.append(
            f"🔑 {key}"
        )

        start_code = episode.get(
            "start_code"
        )

        if start_code:

            lines.append(
                f"🔗 https://t.me/{BOT_USERNAME}"
                f"?start={start_code}"
            )

        lines.append("")

    if len(episodes) > 30:

        lines.append(
            f"... و "
            f"{len(episodes) - 30:,} قسمت دیگر"
        )

    return "\n".join(
        lines
    )


def sponsor_text():

    sponsors = get_sponsors()

    lines = [
        "📢 مدیریت اسپانسرها",
        ""
    ]

    if not sponsors:

        lines.append(
            "هیچ اسپانسری ثبت نشده."
        )

    else:

        for sponsor in sponsors:

            lines.append(
                f"🆔 {sponsor.get('id')}"
            )

            lines.append(
                f"📢 {sponsor.get('title', 'بدون نام')}"
            )

            lines.append(
                f"🔗 {sponsor.get('link', '')}"
            )

            lines.append("")

    return "\n".join(
        lines
    )


# =========================================================
# ADMIN FILE
# =========================================================

def handle_admin_file(
    chat_id,
    message
):
    file_info = extract_file(
        message
    )

    if not file_info:
        return False

    caption = (
        message.get("caption")
        or ""
    )

    parsed = parse_caption(
        caption
    )

    if not parsed:

        send_message(
            chat_id,
            "❌ فرمت کپشن درست نیست.\n\n"
            "مثال:\n"
            "🪴 سریال «بالا پایین استانبول»\n"
            "🪷 قسمت : 14\n"
            "⚡ زیرنویس فوری\n"
            "🎍 کیفیت : 1080"
        )

        return True

    file_info["file_type"] = (
        parsed["file_type"]
    )

    episode = save_episode(
        parsed["series_name"],
        parsed["episode_number"],
        file_info
    )

    if not episode:

        send_message(
            chat_id,
            "❌ ذخیره قسمت انجام نشد."
        )

        return True

    record_stat(
        ADMIN_ID,
        "upload",
        episode_key=episode.get(
            "episode_key"
        ),
        file_type=parsed["file_type"],
        file_count=1
    )

    ensure_type_codes(
        episode,
        persist=True
    )

    with CACHE_LOCK:

        EPISODES[
            episode["episode_key"]
        ] = episode

        rebuild_code_index()

    lines = [
        "✅ فایل ذخیره شد.",
        "",
        f"🎬 سریال: "
        f"{episode.get('series_name')}",
        f"🪷 قسمت: "
        f"{episode.get('episode_number')}",
        f"📁 نوع: "
        f"{parsed['file_type']}",
        "",
        "🔗 لینک‌های دانلود:"
    ]

    grouped = {}

    for item in (
        episode.get("files")
        or []
    ):

        file_type = normalize_type(
            item.get("file_type")
        )

        code = item.get(
            "type_code"
        )

        if (
            code
            and file_type not in grouped
        ):
            grouped[
                file_type
            ] = code

    for file_type, code in (
        grouped.items()
    ):

        lines.append(
            f"• {file_type}: "
            f"https://t.me/{BOT_USERNAME}"
            f"?start={code}"
        )

    if episode.get(
        "start_code"
    ):

        lines.append("")

        lines.append(
            "🔗 همه فایل‌ها:"
        )

        lines.append(
            f"https://t.me/{BOT_USERNAME}"
            f"?start="
            f"{episode['start_code']}"
        )

    send_message(
        chat_id,
        "\n".join(lines)
    )

    return True


# =========================================================
# BROADCAST
# =========================================================

def get_broadcast_users():

    filters = [
        (
            "eq",
            "is_blocked",
            False
        ),
        (
            "neq",
            "user_id",
            ADMIN_ID
        )
    ]

    rows = fetch_rows(
        "bot_users",
        "user_id",
        filters=filters,
        page_size=1000,
        max_rows=100000
    )

    result = []

    for row in rows:

        try:
            result.append(
                int(
                    row["user_id"]
                )
            )

        except Exception:
            pass

    return result


def broadcast_worker(
    admin_chat_id,
    source_message_id
):
    global BROADCAST_RUNNING

    success = 0
    failed = 0
    total = 0

    try:

        users = get_broadcast_users()

        total = len(
            users
        )

        send_message(
            admin_chat_id,
            f"📢 پیام همگانی شروع شد.\n\n"
            f"👥 گیرنده‌ها: {total:,}"
        )

        for user_id in users:

            if BROADCAST_CANCEL.is_set():
                break

            result = copy_message(
                user_id,
                admin_chat_id,
                source_message_id
            )

            if (
                result
                and result.get("ok")
            ):

                success += 1

            else:

                failed += 1

                error_code = (
                    result.get(
                        "error_code"
                    )
                    if result
                    else None
                )

                if error_code == 403:
                    mark_user_blocked(
                        user_id
                    )

            time.sleep(
                0.05
            )

        cancelled = (
            BROADCAST_CANCEL.is_set()
        )

        record_stat(
            ADMIN_ID,
            "broadcast_sent",
            file_count=success
        )

        record_stat(
            ADMIN_ID,
            "broadcast_failed",
            file_count=failed
        )

        if cancelled:
            status = (
                "⛔ ارسال توسط ادمین متوقف شد."
            )
        else:
            status = (
                "✅ ارسال کامل شد."
            )

        send_message(
            admin_chat_id,
            f"📢 نتیجه پیام همگانی\n\n"
            f"{status}\n\n"
            f"👥 کل: {total:,}\n"
            f"✅ موفق: {success:,}\n"
            f"❌ ناموفق: {failed:,}"
        )

    except Exception as e:

        print(
            "BROADCAST ERROR:",
            repr(e)
        )

        send_message(
            admin_chat_id,
            "❌ پیام همگانی با خطای داخلی متوقف شد."
        )

    finally:

        BROADCAST_CANCEL.clear()

        with BROADCAST_LOCK:
            BROADCAST_RUNNING = False


def start_broadcast(
    admin_chat_id,
    source_message_id
):
    global BROADCAST_RUNNING

    with BROADCAST_LOCK:

        if BROADCAST_RUNNING:

            send_message(
                admin_chat_id,
                "⏳ یک پیام همگانی در حال ارسال است."
            )

            return False

        BROADCAST_RUNNING = True

    BROADCAST_CANCEL.clear()

    MEDIA_EXEC.submit(
        broadcast_worker,
        admin_chat_id,
        source_message_id
    )

    return True


# =========================================================
# RESOLVE EPISODE
# =========================================================

def resolve_episode_key(value):

    value = value.strip()

    with CACHE_LOCK:

        if value in EPISODES:
            return value

        result = CODE_INDEX.get(
            value
        )

        if result:
            return result[0]

    return None


# =========================================================
# ADMIN COMMANDS
# =========================================================

def handle_admin_command(
    chat_id,
    user_id,
    text
):
    text = text.strip()

    if text.startswith(
        "/start"
    ):

        admin_clear_state(
            user_id
        )

        # اینجا Reply Keyboard قدیمی هم پاک می‌شود.
        send_admin_panel(
            chat_id
        )

        return True

    if text == "/cancel":

        admin_clear_state(
            user_id
        )

        send_message(
            chat_id,
            "❌ عملیات لغو شد."
        )

        send_admin_panel(
            chat_id
        )

        return True

    if text == "/cancel_broadcast":

        admin_clear_state(
            user_id
        )

        with BROADCAST_LOCK:
            running = BROADCAST_RUNNING

        if running:

            BROADCAST_CANCEL.set()

            send_message(
                chat_id,
                "⛔ درخواست توقف پیام همگانی ثبت شد."
            )

        else:

            send_message(
                chat_id,
                "پیام همگانی فعالی وجود ندارد."
            )

        return True

    if text == "/sponsors":

        admin_clear_state(
            user_id
        )

        send_message(
            chat_id,
            sponsor_text(),
            reply_markup=sponsor_keyboard()
        )

        return True

    if text.startswith(
        "/add_sponsor"
    ):

        payload = text[
            len("/add_sponsor"):
        ].strip()

        parts = [
            x.strip()
            for x in payload.split("|")
        ]

        if len(parts) != 3:

            send_message(
                chat_id,
                "فرمت درست:\n\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )

            return True

        ok, message = add_sponsor(
            parts[0],
            parts[1],
            parts[2]
        )

        send_message(
            chat_id,
            (
                "✅ "
                if ok
                else
                "❌ "
            ) + message
        )

        return True

    if text.startswith(
        "/remove_sponsor"
    ):

        payload = text[
            len("/remove_sponsor"):
        ].strip()

        if not payload.isdigit():

            send_message(
                chat_id,
                "فرمت درست:\n\n"
                "/remove_sponsor ID"
            )

            return True

        ok = remove_sponsor(
            int(payload)
        )

        send_message(
            chat_id,
            "✅ اسپانسر حذف شد."
            if ok
            else
            "❌ حذف اسپانسر انجام نشد."
        )

        return True

    if text.startswith(
        "/delete_episode"
    ):

        payload = text[
            len("/delete_episode"):
        ].strip()

        if not payload:

            send_message(
                chat_id,
                "فرمت درست:\n\n"
                "/delete_episode ep_xxxxx"
            )

            return True

        key = resolve_episode_key(
            payload
        )

        if not key:

            send_message(
                chat_id,
                "❌ قسمت پیدا نشد."
            )

            return True

        ok = delete_episode(
            key
        )

        send_message(
            chat_id,
            "✅ قسمت حذف شد."
            if ok
            else
            "❌ حذف انجام نشد."
        )

        return True

    if text == "/delete_all":

        send_message(
            chat_id,
            "⚠️ مطمئنی می‌خوای همه قسمت‌ها حذف بشن؟",
            reply_markup=delete_all_confirm_keyboard()
        )

        return True

    if text == "/broadcast":

        with BROADCAST_LOCK:

            if BROADCAST_RUNNING:

                send_message(
                    chat_id,
                    "⏳ یک پیام همگانی در حال ارسال است."
                )

                return True

        admin_set_state(
            user_id,
            "broadcast_waiting"
        )

        send_message(
            chat_id,
            "📢 پیام همگانی\n\n"
            "پیامی که می‌خوای برای همه کاربران ارسال بشه رو همینجا بفرست.\n\n"
            "متن، عکس، ویدیو یا فایل قابل ارساله.\n"
            "برای لغو: /cancel_broadcast"
        )

        return True

    return False


# =========================================================
# ADMIN CALLBACK
# =========================================================

def handle_admin_callback(
    callback,
    user_id
):
    data = (
        callback.get("data")
        or ""
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

    callback_id = callback.get(
        "id"
    )

    if user_id != ADMIN_ID:

        answer_callback(
            callback_id,
            "⛔ دسترسی نداری.",
            show_alert=True
        )

        return

    answer_callback(
        callback_id
    )

    if data == "admin:panel":

        admin_clear_state(
            user_id
        )

        edit_message_text(
            chat_id,
            message_id,
            "⚙️ پنل مدیریت ربات\n\n"
            "قابلیت موردنظر رو انتخاب کن:",
            reply_markup=admin_panel_keyboard()
        )

        return

    if data == "admin:episode":

        admin_set_state(
            user_id,
            "episode"
        )

        edit_message_text(
            chat_id,
            message_id,
            "🎬 مدیریت قسمت‌ها\n\n"
            "ویدیو یا فایل رو با کپشن مخصوص قسمت بفرست.\n\n"
            "مثال:\n"
            "🪴 سریال «بالا پایین استانبول»\n"
            "🪷 قسمت : 14\n"
            "⚡ زیرنویس فوری\n"
            "🎍 کیفیت : 1080\n\n"
            "برای لغو /cancel",
            reply_markup=back_keyboard()
        )

        return

    if data == "admin:sponsors":

        admin_clear_state(
            user_id
        )

        edit_message_text(
            chat_id,
            message_id,
            sponsor_text(),
            reply_markup=sponsor_keyboard()
        )

        return

    if data == "admin:add_sponsor":

        admin_set_state(
            user_id,
            "add_sponsor"
        )

        edit_message_text(
            chat_id,
            message_id,
            "➕ افزودن اسپانسر\n\n"
            "فرمت:\n\n"
            "@channel | نام کانال | https://t.me/channel\n\n"
            "برای لغو /cancel",
            reply_markup=back_keyboard()
        )

        return

    if data == "admin:remove_sponsor":

        admin_set_state(
            user_id,
            "remove_sponsor"
        )

        edit_message_text(
            chat_id,
            message_id,
            sponsor_text()
            + "\n\n"
            "🗑️ حالا ID اسپانسر رو بفرست.\n"
            "برای لغو /cancel",
            reply_markup=back_keyboard()
        )

        return

    if data == "admin:broadcast":

        with BROADCAST_LOCK:
            running = BROADCAST_RUNNING

        if running:

            edit_message_text(
                chat_id,
                message_id,
                "⏳ یک پیام همگانی در حال ارسال است.",
                reply_markup=back_keyboard()
            )

            return

        admin_set_state(
            user_id,
            "broadcast_waiting"
        )

        edit_message_text(
            chat_id,
            message_id,
            "📢 پیام همگانی\n\n"
            "پیام، عکس، ویدیو یا فایل موردنظر رو بفرست.\n\n"
            "برای لغو /cancel_broadcast",
            reply_markup=back_keyboard()
        )

        return

    if data == "admin:stats":

        admin_clear_state(
            user_id
        )

        edit_message_text(
            chat_id,
            message_id,
            advanced_stats_text(),
            reply_markup=back_keyboard()
        )

        return

    if data == "admin:list":

        admin_clear_state(
            user_id
        )

        edit_message_text(
            chat_id,
            message_id,
            episode_list_text(),
            reply_markup=back_keyboard()
        )

        return

    if data == "admin:delete":

        admin_set_state(
            user_id,
            "delete_episode"
        )

        edit_message_text(
            chat_id,
            message_id,
            "🗑 حذف قسمت\n\n"
            "کلید قسمت یا کد start رو بفرست.\n\n"
            "مثال:\n"
            "ep_xxxxxxxxxx_14\n\n"
            "برای لغو /cancel",
            reply_markup=back_keyboard()
        )

        return

    if data == "admin:delete_all":

        edit_message_text(
            chat_id,
            message_id,
            "⚠️ این کار همه قسمت‌ها رو حذف می‌کنه.\n\n"
            "مطمئنی؟",
            reply_markup=delete_all_confirm_keyboard()
        )

        return

    if data == "admin:delete_all_confirm":

        admin_clear_state(
            user_id
        )

        ok = delete_all_episodes()

        edit_message_text(
            chat_id,
            message_id,
            "✅ همه قسمت‌ها حذف شدند."
            if ok
            else
            "❌ حذف همه قسمت‌ها انجام نشد.",
            reply_markup=back_keyboard()
        )

        return

    if data == "admin:sync":

        sync_cache()

        edit_message_text(
            chat_id,
            message_id,
            "🔄 سینک دیتابیس انجام شد.\n\n"
            f"🎬 قسمت‌ها: {len(EPISODES):,}\n"
            f"📢 اسپانسرها: {len(SPONSORS):,}",
            reply_markup=back_keyboard()
        )

        return

    if data == "admin:status":

        admin_clear_state(
            user_id
        )

        edit_message_text(
            chat_id,
            message_id,
            status_text(),
            reply_markup=back_keyboard()
        )

        return


# =========================================================
# ADMIN STATE HANDLER
# =========================================================

def handle_admin_state(
    chat_id,
    user_id,
    message
):
    state = admin_get_state(
        user_id
    )

    if not state:
        return False

    text = (
        message.get("text")
        or ""
    ).strip()

    # -----------------------------------------------------
    # BROADCAST
    # -----------------------------------------------------

    if state == "broadcast_waiting":

        if text in (
            "/cancel",
            "/cancel_broadcast"
        ):

            admin_clear_state(
                user_id
            )

            send_message(
                chat_id,
                "❌ پیام همگانی لغو شد."
            )

            send_admin_panel(
                chat_id
            )

            return True

        admin_clear_state(
            user_id
        )

        started = start_broadcast(
            chat_id,
            message.get(
                "message_id"
            )
        )

        if started:

            send_message(
                chat_id,
                "📤 پیام برای ارسال در صف قرار گرفت."
            )

        return True

    # -----------------------------------------------------
    # ADD SPONSOR
    # -----------------------------------------------------

    if state == "add_sponsor":

        if text == "/cancel":

            admin_clear_state(
                user_id
            )

            send_message(
                chat_id,
                "❌ لغو شد."
            )

            send_admin_panel(
                chat_id
            )

            return True

        parts = [
            x.strip()
            for x in text.split("|")
        ]

        if len(parts) != 3:

            send_message(
                chat_id,
                "❌ فرمت اشتباهه.\n\n"
                "@channel | نام کانال | https://t.me/channel"
            )

            return True

        ok, result = add_sponsor(
            parts[0],
            parts[1],
            parts[2]
        )

        admin_clear_state(
            user_id
        )

        send_message(
            chat_id,
            (
                "✅ "
                if ok
                else
                "❌ "
            ) + result
        )

        send_admin_panel(
            chat_id
        )

        return True

    # -----------------------------------------------------
    # REMOVE SPONSOR
    # -----------------------------------------------------

    if state == "remove_sponsor":

        if text == "/cancel":

            admin_clear_state(
                user_id
            )

            send_message(
                chat_id,
                "❌ لغو شد."
            )

            send_admin_panel(
                chat_id
            )

            return True

        if not text.isdigit():

            send_message(
                chat_id,
                "❌ فقط ID اسپانسر رو بفرست."
            )

            return True

        ok = remove_sponsor(
            int(text)
        )

        admin_clear_state(
            user_id
        )

        send_message(
            chat_id,
            "✅ اسپانسر حذف شد."
            if ok
            else
            "❌ حذف اسپانسر انجام نشد."
        )

        send_admin_panel(
            chat_id
        )

        return True

    # -----------------------------------------------------
    # DELETE EPISODE
    # -----------------------------------------------------

    if state == "delete_episode":

        if text == "/cancel":

            admin_clear_state(
                user_id
            )

            send_message(
                chat_id,
                "❌ لغو شد."
            )

            send_admin_panel(
                chat_id
            )

            return True

        key = resolve_episode_key(
            text
        )

        if not key:

            send_message(
                chat_id,
                "❌ قسمت پیدا نشد.\n"
                "کلید یا کد start رو درست بفرست."
            )

            return True

        ok = delete_episode(
            key
        )

        admin_clear_state(
            user_id
        )

        send_message(
            chat_id,
            "✅ قسمت حذف شد."
            if ok
            else
            "❌ حذف انجام نشد."
        )

        send_admin_panel(
            chat_id
        )

        return True

    # -----------------------------------------------------
    # EPISODE
    # -----------------------------------------------------

    if state == "episode":

        if text == "/cancel":

            admin_clear_state(
                user_id
            )

            send_message(
                chat_id,
                "❌ لغو شد."
            )

            send_admin_panel(
                chat_id
            )

            return True

        if (
            message.get("video")
            or message.get("document")
        ):

            return handle_admin_file(
                chat_id,
                message
            )

        send_message(
            chat_id,
            "🎬 الان باید ویدیو یا فایل قسمت رو بفرستی."
        )

        return True

    return False


# =========================================================
# USER START
# =========================================================

def process_start(
    chat_id,
    user_id,
    code
):
    episode, file_type = get_episode_by_code(
        code
    )

    if not episode:

        send_message(
            chat_id,
            "❌ لینک نامعتبره یا قسمت دیگه وجود نداره."
        )

        return

    set_pending(
        user_id,
        episode.get(
            "episode_key"
        ),
        file_type
    )

    record_stat(
        user_id,
        "start",
        episode_key=episode.get(
            "episode_key"
        ),
        file_type=file_type
    )

    if check_membership(
        user_id
    ):

        send_reaction_page(
            chat_id,
            user_id
        )

    else:

        send_join_page(
            chat_id,
            user_id
        )


# =========================================================
# MESSAGE HANDLER
# =========================================================

def handle_message(message):

    chat = (
        message.get("chat")
        or {}
    )

    user = (
        message.get("from")
        or {}
    )

    chat_id = chat.get(
        "id"
    )

    user_id = user.get(
        "id"
    )

    if not chat_id or not user_id:
        return

    # ثبت کاربر برای آمار و Broadcast
    register_user(
        user
    )

    text = (
        message.get("text")
        or ""
    ).strip()

    # =====================================================
    # ADMIN
    # =====================================================

    if user_id == ADMIN_ID:

        # /start همیشه اول پنل اصلی را باز می‌کند
        # و Reply Keyboard قدیمی را حذف می‌کند.
        if text.startswith(
            "/start"
        ):

            admin_clear_state(
                user_id
            )

            send_admin_panel(
                chat_id
            )

            return

        # اگر state فعال است
        if admin_get_state(
            user_id
        ):

            if text in (
                "/cancel",
                "/cancel_broadcast"
            ):

                handle_admin_command(
                    chat_id,
                    user_id,
                    text
                )

                return

            # Broadcast باید هر نوع پیام را قبول کند
            if (
                admin_get_state(
                    user_id
                )
                == "broadcast_waiting"
            ):

                handle_admin_state(
                    chat_id,
                    user_id,
                    message
                )

                return

            if (
                message.get("video")
                or message.get("document")
            ):

                handle_admin_state(
                    chat_id,
                    user_id,
                    message
                )

                return

            if text:

                handle_admin_state(
                    chat_id,
                    user_id,
                    message
                )

                return

        # فرمان‌های معمول ادمین
        if text.startswith("/"):

            handled = handle_admin_command(
                chat_id,
                user_id,
                text
            )

            if handled:
                return

        # اگر فایل بدون انتخاب مدیریت قسمت ارسال شد
        if (
            message.get("video")
            or message.get("document")
        ):

            send_message(
                chat_id,
                "اول از پنل «🎬 مدیریت قسمت‌ها» رو بزن."
            )

        return

    # =====================================================
    # NORMAL USERS
    # =====================================================

    if text.startswith(
        "/start"
    ):

        parts = text.split(
            maxsplit=1
        )

        if len(parts) == 1:

            send_message(
                chat_id,
                "سلام 👋\n"
                "برای دریافت فایل از لینک قسمت استفاده کن."
            )

            return

        code = parts[1].strip()

        process_start(
            chat_id,
            user_id,
            code
        )

        return


# =========================================================
# CALLBACK HANDLER
# =========================================================

def handle_callback(callback):

    user = (
        callback.get("from")
        or {}
    )

    user_id = user.get(
        "id"
    )

    if not user_id:
        return

    register_user(
        user
    )

    data = (
        callback.get("data")
        or ""
    )

    # =====================================================
    # ADMIN
    # =====================================================

    if (
        user_id == ADMIN_ID
        and data.startswith("admin:")
    ):

        handle_admin_callback(
            callback,
            user_id
        )

        return

    callback_id = callback.get(
        "id"
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

    # =====================================================
    # CHECK MEMBERSHIP
    # =====================================================

    if data == "check_membership":

        answer_callback(
            callback_id,
            "در حال بررسی عضویت..."
        )

        if check_membership(
            user_id
        ):

            delete_message(
                chat_id,
                message.get(
                    "message_id"
                )
            )

            send_reaction_page(
                chat_id,
                user_id
            )

        else:

            answer_callback(
                callback_id,
                "هنوز عضویت کامل نشده.",
                show_alert=True
            )

        return

    # =====================================================
    # SYMBOLIC REACTION
    # =====================================================

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
                "❌ درخواست منقضی شده. دوباره از لینک قسمت وارد شو."
            )

            return

        if not check_membership(
            user_id
        ):

            send_join_page(
                chat_id,
                user_id
            )

            return

        episode = get_episode(
            pending.get(
                "episode_key"
            )
        )

        if not episode:

            send_message(
                chat_id,
                "❌ قسمت پیدا نشد."
            )

            clear_pending(
                user_id
            )

            return

        delete_message(
            chat_id,
            message.get(
                "message_id"
            )
        )

        # ری‌اکشن نمادین است.
        deliver_episode(
            chat_id,
            user_id,
            episode,
            pending.get(
                "file_type"
            ),
            redownload_code=None
        )

        return

    # =====================================================
    # REDOWNLOAD
    # =====================================================

    if data.startswith(
        "redownload:"
    ):

        code = data.split(
            ":",
            1
        )[1].strip()

        answer_callback(
            callback_id,
            "در حال آماده‌سازی..."
        )

        episode, file_type = get_episode_by_code(
            code
        )

        if not episode:

            send_message(
                chat_id,
                "❌ لینک دانلود مجدد نامعتبره."
            )

            return

        set_pending(
            user_id,
            episode.get(
                "episode_key"
            ),
            file_type
        )

        if check_membership(
            user_id
        ):

            send_reaction_page(
                chat_id,
                user_id
            )

        else:

            send_join_page(
                chat_id,
                user_id
            )

        return

    answer_callback(
        callback_id
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

        update = request.get_json(
            silent=True
        ) or {}

        if update.get(
            "message"
        ):

            handle_message(
                update["message"]
            )

        elif update.get(
            "callback_query"
        ):

            handle_callback(
                update["callback_query"]
            )

        return jsonify({
            "ok": True
        })

    except Exception as e:

        print(
            "WEBHOOK ERROR:",
            repr(e)
        )

        return jsonify({
            "ok": True
        })


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
        "episodes": len(
            EPISODES
        ),
        "sponsors": len(
            SPONSORS
        )
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
            ],
            "drop_pending_updates": False
        }
    )

    print(
        "WEBHOOK:",
        result
    )


# =========================================================
# STARTUP
# =========================================================

def startup():

    global BOT_USERNAME

    me = get_me()

    if (
        me
        and me.get("ok")
    ):

        BOT_USERNAME = (
            me.get("result", {})
            .get("username")
            or "Seryyaltorki_bot"
        )

    print(
        "BOT USERNAME:",
        BOT_USERNAME
    )

    # کش اولیه
    sync_cache()

    # سینک دوره‌ای
    threading.Thread(
        target=cache_loop,
        daemon=True
    ).start()

    # وبهوک
    setup_webhook()

    print(
        "BOT STARTED"
    )


startup()


# =========================================================
# RUN
# =========================================================

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
