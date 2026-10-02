import os
import re
import time
import hashlib
import threading
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
# FAST RAM CACHE
# ============================================================

CACHE_SYNC_SECONDS = 90

CACHE_LOCK = threading.RLock()

EPISODES_CACHE = {}
SPONSORS_CACHE = []
PENDING_CACHE = {}

BOT_USERNAME_CACHE = ""
LAST_CACHE_SYNC = 0

TG_SESSION = requests.Session()

FAST_EXECUTOR = ThreadPoolExecutor(max_workers=16)


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def tg(method, data=None, timeout=30):
    try:
        r = TG_SESSION.post(
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
    return tg(
        "getChat",
        {
            "chat_id": chat_id
        }
    )


def get_me():
    global BOT_USERNAME_CACHE

    if BOT_USERNAME_CACHE:
        return {
            "ok": True,
            "result": {
                "username": BOT_USERNAME_CACHE
            }
        }

    result = tg("getMe")

    if result.get("ok"):
        BOT_USERNAME_CACHE = (
            result.get("result") or {}
        ).get("username", "")

    return result


def send_video(chat_id, file_id, caption=None):
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


def send_document(chat_id, file_id, caption=None):
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
        series_name.strip()
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


def parse_caption(caption):
    """
    Expected examples:

    🪴 سریال «عشق و تخت»
    🪷 قسمت : 2
    🫧 زبان اصلی
    🎍 کیفیت : 1080

    or:

    🪴 سریال «عشق و تخت»
    🪷 قسمت : 2
    🫧 زیرنویس فوری
    🎍 کیفیت : 1080
    """

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
        cached = EPISODES_CACHE.get(
            episode_key
        )

        if cached is not None:
            return cached

    # فقط در صورت cache miss
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

            episode = result.data[0]

            with CACHE_LOCK:
                EPISODES_CACHE[
                    episode_key
                ] = episode

            return episode

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
    files,
    is_preview=False
):

    payload = {
        "episode_key": episode_key,
        "series_name": series_name,
        "episode_number": episode_number,
        "files": files,
        "is_preview": is_preview
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

        row = (
            result.data or
            [payload]
        )[0]

        with CACHE_LOCK:
            EPISODES_CACHE[
                episode_key
            ] = row

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
            EPISODES_CACHE.pop(
                episode_key,
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
            EPISODES_CACHE.clear()

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

    with CACHE_LOCK:
        PENDING_CACHE[
            int(user_id)
        ] = episode_key

    # ذخیره در DB بدون معطل کردن مسیر کاربر
    return FAST_EXECUTOR.submit(
        _set_pending_db,
        user_id,
        episode_key
    )


def _set_pending_db(
    user_id,
    episode_key
):

    try:

        return (
            supabase
            .table("pending")
            .upsert(
                {
                    "user_id": int(user_id),
                    "episode_key": episode_key
                },
                on_conflict="user_id"
            )
            .execute()
        )

    except Exception as e:

        print(
            "set_pending error:",
            e
        )

        return None


def get_pending(user_id):

    with CACHE_LOCK:

        cached = PENDING_CACHE.get(
            int(user_id)
        )

        if cached is not None:
            return cached

    try:

        result = (
            supabase
            .table("pending")
            .select("*")
            .eq(
                "user_id",
                int(user_id)
            )
            .limit(1)
            .execute()
        )

        if result.data:

            episode_key = (
                result.data[0]
                .get("episode_key")
            )

            with CACHE_LOCK:
                PENDING_CACHE[
                    int(user_id)
                ] = episode_key

            return episode_key

    except Exception as e:

        print(
            "get_pending error:",
            e
        )

    return None


def clear_pending(user_id):

    with CACHE_LOCK:
        PENDING_CACHE.pop(
            int(user_id),
            None
        )

    return FAST_EXECUTOR.submit(
        _clear_pending_db,
        user_id
    )


def _clear_pending_db(user_id):

    try:

        return (
            supabase
            .table("pending")
            .delete()
            .eq(
                "user_id",
                int(user_id)
            )
            .execute()
        )

    except Exception as e:

        print(
            "clear_pending error:",
            e
        )

        return None


def clear_all_pending():

    with CACHE_LOCK:
        PENDING_CACHE.clear()

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
# CACHE SYNC
# ============================================================

def sync_cache_from_supabase():

    global SPONSORS_CACHE
    global LAST_CACHE_SYNC

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

        pending_result = (
            supabase
            .table("pending")
            .select(
                "user_id,episode_key"
            )
            .execute()
        )

        new_episodes = {
            row.get(
                "episode_key"
            ): row
            for row in (
                episodes_result.data or []
            )
            if row.get(
                "episode_key"
            )
        }

        new_sponsors = (
            sponsors_result.data or []
        )

        new_pending = {
            int(row.get("user_id")):
            row.get("episode_key")
            for row in (
                pending_result.data or []
            )
            if (
                row.get("user_id")
                is not None
                and row.get("episode_key")
            )
        }

        with CACHE_LOCK:

            EPISODES_CACHE.clear()

            EPISODES_CACHE.update(
                new_episodes
            )

            SPONSORS_CACHE = (
                new_sponsors
            )

            PENDING_CACHE.clear()

            PENDING_CACHE.update(
                new_pending
            )

            LAST_CACHE_SYNC = (
                time.time()
            )

        print(
            f"Cache synced: "
            f"{len(new_episodes)} episodes, "
            f"{len(new_sponsors)} sponsors, "
            f"{len(new_pending)} pending"
        )

        return True

    except Exception as e:

        print(
            "Cache sync error:",
            e
        )

        return False


def cache_sync_loop():

    sync_cache_from_supabase()

    while True:

        time.sleep(
            CACHE_SYNC_SECONDS
        )

        sync_cache_from_supabase()


def start_cache_sync():

    threading.Thread(
        target=cache_sync_loop,
        daemon=True,
        name="supabase-cache-sync"
    ).start()


# ============================================================
# SUPABASE - SPONSORS
# ============================================================

def get_sponsors():

    with CACHE_LOCK:
        return list(
            SPONSORS_CACHE
        )


def add_sponsor(
    chat_id,
    title,
    url
):

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

        with CACHE_LOCK:

            SPONSORS_CACHE.extend(
                result.data or []
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

            SPONSORS_CACHE[:] = [
                x
                for x in SPONSORS_CACHE
                if int(
                    x.get("id", -1)
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

        (
            supabase
            .table("channel_posts")
            .upsert(
                {
                    "message_id":
                        int(message_id)
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


def get_last_posts(limit=5):

    try:

        result = (
            supabase
            .table("channel_posts")
            .select(
                "message_id,created_at"
            )
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


# این توابع عمداً دیگر در جریان دریافت فایل استفاده نمی‌شوند.
# ربات ری‌اکشن کاربر را بررسی یا ذخیره نمی‌کند.

def save_reaction(
    user_id,
    message_id,
    reacted=True
):

    return None


def has_reacted(
    user_id,
    message_id
):

    return False


def user_reacted_to_last_posts(
    user_id,
    limit=5
):

    return True, limit


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

    def check_one(sponsor):

        chat_id = sponsor.get(
            "chat_id"
        )

        if not chat_id:
            return None

        if not member_status(
            chat_id,
            user_id
        ):
            return sponsor

        return None

    results = FAST_EXECUTOR.map(
        check_one,
        sponsors
    )

    return [
        x
        for x in results
        if x is not None
    ]


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

    if not is_main_channel_member(
        user_id
    ):

        send_message(
            chat_id,
            "برای دریافت فایل اول باید عضو کانال اصلی بشی 👇",
            main_channel_keyboard()
        )

        return False

    missing = check_all_sponsors(
        user_id
    )

    if missing:

        send_message(
            chat_id,
            "برای دریافت فایل، اول عضو کانال‌های زیر شو 👇",
            sponsor_keyboard(
                missing
            )
        )

        return False

    show_reaction_page(
        chat_id,
        user_id
    )

    return True


def show_reaction_page(
    chat_id,
    user_id
):

    send_message(
        chat_id,
        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر کانال @altiustuistsnbol را ری اکت بزنید و سپس برگردید و دکمه انجام دادم را کلیک کنید ♥️",
        reaction_keyboard()
    )


def send_episode_to_user(
    chat_id,
    user_id
):

    episode_key = get_pending(
        user_id
    )

    if not episode_key:

        send_message(
            chat_id,
            "لینک قسمت پیدا نشد. دوباره لینک قسمت رو باز کن."
        )

        return

    episode = get_episode(
        episode_key
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

    files = (
        episode.get("files")
        or []
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

    sent_ids = []

    for file_info in files:

        result = send_media_file(
            chat_id,
            file_info
        )

        if result.get("ok"):

            sent_message = (
                result.get("result")
                or {}
            )

            message_id = (
                sent_message
                .get("message_id")
            )

            if message_id:
                sent_ids.append(
                    message_id
                )

    if not sent_ids:

        send_message(
            chat_id,
            "ارسال فایل انجام نشد. چند لحظه بعد دوباره امتحان کن."
        )

        return

    # ========================================================
    # DOWNLOAD AGAIN
    # ========================================================

    bot_username_result = get_me()

    bot_username = (
        bot_username_result
        .get("result", {})
        .get("username", "")
    )

    redownload_keyboard = None

    if bot_username:

        redownload_link = (
            f"https://t.me/"
            f"{bot_username}"
            f"?start={episode_key}"
        )

        redownload_keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text":
                            "دانلود مجدد ♻️",
                        "url":
                            redownload_link
                    }
                ]
            ]
        }

    send_message(
        chat_id,
        f"فایل‌ها ارسال شد ✅\n"
        f"تا {DELETE_AFTER} ثانیه فرصت داری ذخیره‌شون کنی.",
        redownload_keyboard
    )

    threading.Thread(
        target=
            delete_sent_messages_later,
        args=(
            chat_id,
            sent_ids
        ),
        daemon=True
    ).start()


def delete_sent_messages_later(
    chat_id,
    message_ids
):

    time.sleep(
        DELETE_AFTER
    )

    for message_id in message_ids:

        delete_message(
            chat_id,
            message_id
        )

    # فایل اصلی تلگرام حذف نمی‌شود.
    # فقط نسخه‌ای که برای کاربر ارسال شده حذف می‌شود.

    send_message(
        chat_id,
        "فایل‌های ارسالی حذف شدند 🗑️\n"
        "برای دریافت دوباره، لینک قسمت رو دوباره باز کن."
    )


# ============================================================
# ADMIN FILE HANDLING
# ============================================================

def extract_file_from_message(
    message
):

    if message.get("video"):

        video = message[
            "video"
        ]

        return {
            "type":
                "video",
            "file_id":
                video.get("file_id"),
            "caption":
                message.get(
                    "caption",
                    ""
                )
        }

    if message.get("document"):

        document = message[
            "document"
        ]

        return {
            "type":
                "document",
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

    # ========================================================
    # PREVIEW
    # ========================================================

    is_preview = bool(
        re.search(
            r"پیش[\s‌-]*نمایش",
            caption,
            re.IGNORECASE
        )
    )

    if is_preview:

        episode_key = (
            "preview__"
            + parsed["episode_key"]
        )

    else:

        episode_key = (
            parsed["episode_key"]
        )

    episode = get_episode(
        episode_key
    )

    if episode:

        files = (
            episode.get("files")
            or []
        )

    else:

        files = []

    files.append(
        file_info
    )

    saved = save_episode(
        episode_key=
            episode_key,
        series_name=
            parsed["series_name"],
        episode_number=
            parsed["episode_number"],
        files=
            files,
        is_preview=
            is_preview
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

    if bot_username:

        link = (
            f"https://t.me/"
            f"{bot_username}"
            f"?start={episode_key}"
        )

    else:

        link = (
            f"/start "
            f"{episode_key}"
        )

    if is_preview:

        send_message(
            ADMIN_ID,
            "✅ پیش‌نمایش ذخیره شد.\n\n"
            f"سریال: "
            f"{parsed['series_name']}\n"
            f"قسمت: "
            f"{parsed['episode_number']}\n"
            f"تعداد فایل‌های پیش‌نمایش: "
            f"{len(files)}\n\n"
            f"لینک پیش‌نمایش:\n"
            f"{link}"
        )

    else:

        send_message(
            ADMIN_ID,
            "✅ فایل ذخیره شد.\n\n"
            f"سریال: "
            f"{parsed['series_name']}\n"
            f"قسمت: "
            f"{parsed['episode_number']}\n"
            f"تعداد فایل‌های این قسمت: "
            f"{len(files)}\n\n"
            f"لینک قسمت:\n"
            f"{link}"
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

    if user_id != ADMIN_ID:
        return False

    text = text.strip()

    if text == "/start":

        send_message(
            chat_id,
            "پنل مدیریت ربات فعال است ✅\n\n"
            "/sponsors\n"
            "/episodes\n"
            "/delete_all\n"
            "/delete_episode KEY\n"
            "/add_sponsor @channel | نام کانال | https://t.me/channel\n"
            "/remove_sponsor ID"
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
                f"ID: "
                f"{sponsor.get('id')}\n"
                f"کانال: "
                f"{sponsor.get('chat_id')}\n"
                f"نام: "
                f"{sponsor.get('title')}\n"
                f"لینک: "
                f"{sponsor.get('url')}\n"
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
                "فرمت درست:\n"
                "/remove_sponsor ID"
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

    if text == "/episodes":

        try:

            with CACHE_LOCK:

                rows = [
                    {
                        "episode_key":
                            x.get(
                                "episode_key"
                            ),
                        "series_name":
                            x.get(
                                "series_name"
                            ),
                        "episode_number":
                            x.get(
                                "episode_number"
                            ),
                        "is_preview":
                            x.get(
                                "is_preview",
                                False
                            )
                    }

                    for x in
                    EPISODES_CACHE.values()

                    if not x.get(
                        "is_preview",
                        False
                    )
                ]

            rows.sort(
                key=lambda x: (
                    x.get(
                        "series_name"
                    ) or "",
                    x.get(
                        "episode_number"
                    ) or 0
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
                    f"{row.get('series_name')} - "
                    f"قسمت "
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

        episode_key = parts[
            1
        ].strip()

        delete_episode_from_db(
            episode_key
        )

        send_message(
            chat_id,
            f"✅ اطلاعات "
            f"{episode_key} "
            f"حذف شد."
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

    update = (
        request
        .get_json(
            silent=True
        )
        or {}
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
            and
            chat_username.lower()
            ==
            CHANNEL_ID
            .replace("@", "")
            .lower()
        ):

            message_id = (
                channel_post
                .get("message_id")
            )

            if message_id:

                save_channel_post(
                    message_id
                )

        return jsonify(
            {
                "ok": True
            }
        )

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

        # Admin uploaded video/document
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

        # User /start deep link
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

            episode_key = (
                parts[1].strip()
            )

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

        # Check sponsor/main membership
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

            if not is_main_channel_member(
                user_id
            ):

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

            missing = check_all_sponsors(
                user_id
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
                        "ok": True
                    }
                )

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
        #
        # فقط متن درخواست ری‌اکشن نمایش داده می‌شود.
        # هیچ ری‌اکشنی بررسی یا ذخیره نمی‌شود.
        #
        if data == "check_reactions":

            answer_callback(
                callback_id,
                "در حال ارسال فایل..."
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
# START
# ============================================================

start_cache_sync()

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
