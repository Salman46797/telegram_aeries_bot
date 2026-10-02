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


def send_file(
    chat_id,
    file_info
):
    if not file_info.get("file_id"):
        return {
            "ok": False
        }

    if file_info.get("type") == "document":
        return send_document(
            chat_id,
            file_info["file_id"],
            file_info.get("caption", "")
        )

    return send_video(
        chat_id,
        file_info["file_id"],
        file_info.get("caption", "")
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

        result.append(file_info)

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
        code = file_info.get("type_code")

        if code:
            TYPE_INDEX[code] = (
                key,
                file_info.get("file_type")
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

            if not row.get("episode_key"):
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

        row = EPISODES.get(key)

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

        print(
            "pending save error:",
            e
        )


def get_pending(user_id):

    user_id = int(user_id)

    value = PENDING.get(
        user_id
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
                str(user_id)
            )
            .limit(1)
            .execute()
        )

        if result.data:

            value = result.data[0].get(
                "episode_key"
            )

            if value:

                PENDING[user_id] = value

                return value

    except Exception as e:

        print(
            "pending get error:",
            e
        )

    return None


def clear_pending(user_id):

    user_id = int(user_id)

    value = PENDING.pop(
        user_id,
        None
    )

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


# ============================================================
# SPONSORS
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

            results.append(False)

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
# KEYBOARDS
# ============================================================

def join_keyboard(
    missing=None
):

    rows = []

    # If missing is None:
    # show main + all sponsors.
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

    # If only sponsors are missing:
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
# USER FLOW
# ============================================================

def send_reaction_page(
    chat_id
):

    send_message(
        chat_id,

        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر "
        "کانال @altiustuistsnbol را ری اکت بزنید "
        "و سپس برگردید و دکمه انجام دادم را کلیک کنید ♥️",

        reaction_keyboard()
    )


def send_join_page(
    chat_id,
    user_id
):

    # ========================================================
    # مهم:
    # قبل از ارسال هر پیام عضویت، عضویت فعلی بررسی می‌شود.
    # ========================================================

    main_ok, missing = check_membership(
        user_id
    )

    # کاربر از قبل عضو همه کانال‌هاست
    if main_ok and not missing:

        send_reaction_page(
            chat_id
        )

        return "reaction"

    # عضو کانال اصلی نیست
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

    # عضو اصلی هست ولی اسپانسرها کامل نیست
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

        # لینک دانلود مجدد
        if selected_type:

            redownload_target = type_code(
                real_key,
                selected_type
            )

        else:

            redownload_target = real_key

        # فقط بعد از اینکه همه چیز معتبر شد
        # pending پاک می‌شود.
        clear_pending(
            user_id
        )

        # ====================================================
        # خیلی مهم:
        # فایل‌ها عمداً SEQUENTIAL ارسال می‌شوند.
        #
        # قبلاً با ThreadPool همزمان ارسال می‌شدند و
        # ممکن بود هشدار بالای فایل‌ها قرار بگیرد.
        # ====================================================

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

        # ====================================================
        # دقیقاً یک پیام زیر فایل‌ها
        # هشدار + دانلود مجدد
        # ====================================================

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

        # ثبت آمار بدون معطل کردن کاربر
        EXEC.submit(
            record_stat,
            user_id,
            "download",
            real_key,
            selected_type,
            len(sent_message_ids)
        )

        # فقط فایل‌ها بعد از ۳۰ ثانیه حذف می‌شوند.
        # پیام هشدار + دانلود مجدد حذف نمی‌شود.
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

    if message.get(
        "video"
    ):

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

    if message.get(
        "document"
    ):

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
            "🎍 کیفیت : 1080"
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
            f"🔗 {base_link}?start={code}"
        )

        lines.append("")

    lines.append(
        f"🔗 لینک مستقیم کل {label}: "
        f"{base_link}?start={key}"
    )

    send_message(
        ADMIN_ID,
        "\n".join(lines)
    )

    record_stat(
        ADMIN_ID,
        "preview" if preview else "upload",
        key,
        None,
        1
    )


# ============================================================
# ADMIN KEYBOARD
# ============================================================

def admin_keyboard():

    return {
        "keyboard": [
            [
                {
                    "text":
                        "🎬 مدیریت قسمت‌ها"
                },
                {
                    "text":
                        "📢 اسپانسرها"
                }
            ],
            [
                {
                    "text":
                        "📋 لیست قسمت‌ها"
                },
                {
                    "text":
                        "🗑 حذف قسمت"
                }
            ],
            [
                {
                    "text":
                        "🔄 سینک دیتابیس"
                },
                {
                    "text":
                        "⚡ وضعیت ربات"
                }
            ],
            [
                {
                    "text":
                        "📊 آمار"
                }
            ]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


# ============================================================
# ADMIN COMMANDS
# ============================================================

def handle_admin_command(
    chat_id,
    text
):

    if text == "/start":

        send_message(
            chat_id,
            "🛠 پنل مدیریت فعال است.",
            admin_keyboard()
        )

        return True

    if text == "🎬 مدیریت قسمت‌ها":

        send_message(
            chat_id,

            "🎬 مدیریت قسمت‌ها\n\n"
            "ویدیو یا فایل قسمت را با همان کپشن "
            "قبلی ارسال کن.\n\n"
            "برای حذف قسمت از گزینه «🗑 حذف قسمت» استفاده کن.",

            admin_keyboard()
        )

        return True

    if (
        text == "📢 اسپانسرها"
        or text == "/sponsors"
    ):

        sponsor_list = get_sponsors()

        if not sponsor_list:

            send_message(
                chat_id,
                "هیچ اسپانسری ثبت نشده.",
                admin_keyboard()
            )

            return True

        lines = [
            "📋 لیست اسپانسرها:"
        ]

        for sponsor in sponsor_list:

            lines.append(
                f"\nID: {sponsor.get('id')}\n"
                f"کانال: {sponsor.get('chat_id')}\n"
                f"نام: {sponsor.get('title')}\n"
                f"لینک: {sponsor.get('url')}"
            )

        send_message(
            chat_id,
            "\n".join(lines),
            admin_keyboard()
        )

        return True

    if text == "📋 لیست قسمت‌ها":

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
                admin_keyboard()
            )

            return True

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

        send_message(
            chat_id,
            "\n".join(lines),
            admin_keyboard()
        )

        return True

    if text == "🔄 سینک دیتابیس":

        ok = sync_cache()

        send_message(
            chat_id,

            "✅ سینک دیتابیس انجام شد."
            if ok
            else
            "❌ سینک ناموفق بود.",

            admin_keyboard()
        )

        return True

    if text == "⚡ وضعیت ربات":

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

        send_message(
            chat_id,

            "🟢 وضعیت ربات\n\n"
            "🤖 Telegram: 🟢\n"
            f"🗄 Supabase: "
            f"{'🟢' if db_ok else '🔴'}\n"
            f"⚡ Cache: "
            f"{'🟢' if CACHE_READY else '🔴'}\n"
            f"🎬 قسمت‌ها: {episode_count}\n"
            f"📢 اسپانسرها: {sponsor_count}",

            admin_keyboard()
        )

        return True

    if text == "📊 آمار":

        try:

            result = (
                supabase
                .table("bot_stats")
                .select(
                    "event_type,file_count"
                )
                .limit(10000)
                .execute()
            )

            rows = result.data or []

            downloads = sum(
                1
                for row in rows
                if row.get(
                    "event_type"
                ) == "download"
            )

            files_sent = sum(
                int(
                    row.get(
                        "file_count"
                    ) or 0
                )
                for row in rows
                if row.get(
                    "event_type"
                ) == "download"
            )

            uploads = sum(
                1
                for row in rows
                if row.get(
                    "event_type"
                ) == "upload"
            )

            send_message(
                chat_id,

                "📊 آمار ربات\n\n"
                f"📥 دانلودها: {downloads}\n"
                f"📦 فایل‌های ارسال‌شده: {files_sent}\n"
                f"📤 قسمت‌های ثبت‌شده: {uploads}",

                admin_keyboard()
            )

        except Exception as e:

            send_message(
                chat_id,
                f"❌ خطا در آمار:\n{e}"
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
                "https://t.me/channel"
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
                "✅ اسپانسر اضافه شد.",
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
                "✅ اسپانسر حذف شد.",
                admin_keyboard()
            )

        return True

    if text == "🗑 حذف قسمت":

        ADMIN_STATE[
            ADMIN_ID
        ] = "delete"

        send_message(
            chat_id,
            "🗑 کلید قسمت را بفرست.\n\n"
            "مثال:\n"
            "ep_xxxxxxxxxx_1",
            admin_keyboard()
        )

        return True

    if ADMIN_STATE.get(
        ADMIN_ID
    ) == "delete":

        delete_episode(
            text.strip()
        )

        ADMIN_STATE.pop(
            ADMIN_ID,
            None
        )

        send_message(
            chat_id,
            "✅ اطلاعات قسمت حذف شد.",
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
                "فرمت:\n"
                "/delete_episode EPISODE_KEY"
            )

            return True

        delete_episode(
            parts[1].strip()
        )

        send_message(
            chat_id,
            "✅ اطلاعات قسمت حذف شد.",
            admin_keyboard()
        )

        return True

    if text == "/delete_all":

        delete_all_episodes()

        send_message(
            chat_id,
            "✅ همه قسمت‌ها حذف شدند.",
            admin_keyboard()
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

        # ----------------------------------------------------
        # ADMIN
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

            if text:

                if handle_admin_command(
                    chat_id,
                    text
                ):

                    return jsonify(
                        {
                            "ok": True
                        }
                    )

        # ----------------------------------------------------
        # /start
        # ----------------------------------------------------

        if text.startswith(
            "/start"
        ):

            parts = text.split(
                maxsplit=1
            )

            # /start بدون لینک
            if len(parts) == 1:

                if user_id == ADMIN_ID:

                    handle_admin_command(
                        chat_id,
                        "/start"
                    )

                else:

                    send_message(
                        chat_id,

                        "سلام 👋\n"
                        "لینک قسمت موردنظرت رو باز کن."
                    )

                return jsonify(
                    {
                        "ok": True
                    }
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

                    # یک بار کش را تازه می‌کنیم
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
                        {
                            "ok": True
                        }
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
                        {
                            "ok": True
                        }
                    )

                set_pending(
                    user_id,
                    token
                )

            # =================================================
            # مهم‌ترین قسمت:
            #
            # اینجا قبل از نمایش پیام عضویت،
            # عضویت واقعی کاربر بررسی می‌شود.
            #
            # اگر از قبل عضو باشد:
            # مستقیماً reaction page می‌آید.
            # =================================================

            send_join_page(
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

        from_user = (
            callback.get("from")
            or {}
        )

        user_id = from_user.get(
            "id"
        )

        callback_message = (
            callback.get("message")
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
                    {
                        "ok": True
                    }
                )

            main_ok, missing = check_membership(
                user_id
            )

            # ------------------------------------------------
            # عضویت کامل است
            # ------------------------------------------------

            if main_ok and not missing:

                # پیام عضویت قبلی حذف می‌شود
                if message_id:

                    delete_message(
                        chat_id,
                        message_id
                    )

                # سپس فقط پیام ری‌اکشن
                send_reaction_page(
                    chat_id
                )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            # ------------------------------------------------
            # کانال اصلی هنوز عضو نیست
            # ------------------------------------------------

            if not main_ok:

                send_message(
                    chat_id,

                    "هنوز عضو کانال اصلی نیستی 👇",

                    join_keyboard(
                        None
                    )
                )

                return jsonify(
                    {
                        "ok": True
                    }
                )

            # ------------------------------------------------
            # اسپانسرها کامل نیستند
            # ------------------------------------------------

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

        # ====================================================
        # REACTION BUTTON
        #
        # هیچ بررسی واقعی ری‌اکشن انجام نمی‌شود.
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
                    {
                        "ok": True
                    }
                )

            # پیام ری‌اکشن حذف می‌شود
            if message_id:

                delete_message(
                    chat_id,
                    message_id
                )

            # ارسال فایل
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
