import os
import re
import random
import string
import threading
import time
import requests
import hashlib
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify
from supabase import create_client


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

WEBHOOK_URL = "https://telegram-aeries-bot.onrender.com/webhook"
BOT_USERNAME = "Seryyaltorki_bot"

DELETE_AFTER = 30
KEEP_ALIVE_INTERVAL = 5 * 60


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# ============================================================
# THREAD LOCAL SESSION
# ============================================================

_thread_local = threading.local()


def get_session():
    if not hasattr(_thread_local, "session"):
        session = requests.Session()

        session.headers.update({
            "Content-Type": "application/json"
        })

        _thread_local.session = session

    return _thread_local.session


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api(method, payload=None):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    try:
        session = get_session()

        response = session.post(
            url,
            json=payload or {},
            timeout=15
        )

        return response.json()

    except Exception as e:

        print(
            "telegram_api error:",
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

    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    return telegram_api(
        "sendMessage",
        payload
    )


def edit_message(
    chat_id,
    message_id,
    text,
    reply_markup=None
):

    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }

    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    return telegram_api(
        "editMessageText",
        payload
    )


def delete_message(
    chat_id,
    message_id
):

    return telegram_api(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


def answer_callback(
    callback_id,
    text=""
):

    return telegram_api(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text
        }
    )


def get_me():

    return telegram_api(
        "getMe"
    )


def send_video(
    chat_id,
    file_id,
    caption=""
):

    return telegram_api(
        "sendVideo",
        {
            "chat_id": chat_id,
            "video": file_id,
            "caption": caption
        }
    )


def send_document(
    chat_id,
    file_id,
    caption=""
):

    return telegram_api(
        "sendDocument",
        {
            "chat_id": chat_id,
            "document": file_id,
            "caption": caption
        }
    )


def send_media_file(
    chat_id,
    file_info
):

    file_type = file_info.get(
        "type"
    )

    file_id = file_info.get(
        "file_id"
    )

    caption = file_info.get(
        "caption",
        ""
    )

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
        series_name.strip().lower().encode("utf-8")
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

def get_episode(
    episode_key
):

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
            return result.data[0]

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

        return (
            supabase
            .table("episodes")
            .upsert(
                payload,
                on_conflict="episode_key"
            )
            .execute()
        )

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

        return (
            supabase
            .table("episodes")
            .delete()
            .eq(
                "episode_key",
                episode_key
            )
            .execute()
        )

    except Exception as e:

        print(
            "delete_episode error:",
            e
        )

        return None


def delete_all_episodes():

    try:

        return (
            supabase
            .table("episodes")
            .delete()
            .neq(
                "episode_key",
                ""
            )
            .execute()
        )

    except Exception as e:

        print(
            "delete_all_episodes error:",
            e
        )

        return None


# ============================================================
# PENDING
# ============================================================

def set_pending(
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
            "set_pending error:",
            e
        )

        return None


def get_pending(
    user_id
):

    try:

        result = (
            supabase
            .table("pending")
            .select("*")
            .eq(
                "user_id",
                user_id
            )
            .limit(1)
            .execute()
        )

        if result.data:

            return (
                result.data[0]
                .get("episode_key")
            )

    except Exception as e:

        print(
            "get_pending error:",
            e
        )

    return None


def clear_pending(
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
            "clear_pending error:",
            e
        )

        return None


def clear_all_pending():

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
            "clear_all_pending error:",
            e
        )

        return None


# ============================================================
# USERS
# ============================================================

def save_user(
    user_id
):

    try:

        supabase.table(
            "users"
        ).upsert(
            {
                "user_id": user_id,
                "last_seen": datetime.now(
                    timezone.utc
                ).isoformat()
            },
            on_conflict="user_id"
        ).execute()

    except Exception as e:

        print(
            "save_user error:",
            e
        )


# ============================================================
# SPONSORS
# ============================================================

def get_sponsors():

    try:

        result = (
            supabase
            .table("sponsors")
            .select("*")
            .order(
                "id"
            )
            .execute()
        )

        return result.data or []

    except Exception as e:

        print(
            "get_sponsors error:",
            e
        )

        return []


def add_sponsor(
    chat_id,
    title,
    url
):

    try:

        return (
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

        return (
            supabase
            .table("sponsors")
            .delete()
            .eq(
                "id",
                sponsor_id
            )
            .execute()
        )

    except Exception as e:

        print(
            "remove_sponsor error:",
            e
        )

        return None


# ============================================================
# MEMBERSHIP
# ============================================================

def check_channel_member(
    user_id,
    chat_id
):

    try:

        result = telegram_api(
            "getChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id
            }
        )

        if not result.get("ok"):
            return False

        status = (
            result
            .get("result", {})
            .get("status")
        )

        return status in (
            "creator",
            "administrator",
            "member"
        )

    except Exception as e:

        print(
            "check_channel_member error:",
            e
        )

        return False


def is_main_channel_member(
    user_id
):

    return check_channel_member(
        user_id,
        CHANNEL_ID
    )


def check_all_sponsors(
    user_id
):

    sponsors = get_sponsors()

    missing = []

    for sponsor in sponsors:

        chat_id = sponsor.get(
            "chat_id"
        )

        if not check_channel_member(
            user_id,
            chat_id
        ):

            missing.append(
                sponsor
            )

    return missing


# ============================================================
# KEYBOARDS
# ============================================================

def main_channel_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "📣 عضویت در کانال",
                    "url": CHANNEL_URL
                }
            ]
        ]
    }


def sponsor_keyboard(
    sponsors
):

    rows = []

    for sponsor in sponsors:

        rows.append(
            [
                {
                    "text": sponsor.get(
                        "title",
                        "کانال"
                    ),
                    "url": sponsor.get(
                        "url"
                    )
                }
            ]
        )

    return {
        "inline_keyboard": rows
    }


def join_keyboard():

    sponsors = get_sponsors()

    rows = [
        [
            {
                "text": "📣 کانال اصلی",
                "url": CHANNEL_URL
            }
        ]
    ]

    for sponsor in sponsors:

        rows.append(
            [
                {
                    "text": sponsor.get(
                        "title",
                        "کانال"
                    ),
                    "url": sponsor.get(
                        "url"
                    )
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
                    "text": "انجام دادم ✅",
                    "callback_data": "check_reactions"
                }
            ]
        ]
    }


# ============================================================
# CHANNEL POSTS
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
                    "message_id": message_id
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
    count=5
):

    try:

        result = (
            supabase
            .table("channel_posts")
            .select("*")
            .order(
                "created_at",
                desc=True
            )
            .limit(count)
            .execute()
        )

        return result.data or []

    except Exception as e:

        print(
            "get_last_posts error:",
            e
        )

        return []


# ============================================================
# REACTIONS
# ============================================================

def user_reacted_to_last_posts(
    user_id,
    count=5
):

    posts = get_last_posts(
        count
    )

    if len(posts) < count:
        return False, len(posts)

    for post in posts:

        try:

            result = (
                supabase
                .table("reactions")
                .select("*")
                .eq(
                    "user_id",
                    user_id
                )
                .eq(
                    "message_id",
                    post.get(
                        "message_id"
                    )
                )
                .eq(
                    "reacted",
                    True
                )
                .limit(1)
                .execute()
            )

            if not result.data:
                return False, len(posts)

        except Exception as e:

            print(
                "reaction check error:",
                e
            )

            return False, len(posts)

    return True, len(posts)


# ============================================================
# PAGES
# ============================================================

def show_join_page(
    chat_id,
    user_id
):

    send_message(
        chat_id,
        "📣برای استفاده از ربات و دریافت فایل :\n\n"
        "1️⃣ابتدا عضو کانال های زیر بشید\n"
        "2️⃣سپس رو دکمه عضو شدم کلیک کنید",
        join_keyboard()
    )


def show_reaction_page(
    chat_id,
    user_id
):

    ok, count = user_reacted_to_last_posts(
        user_id,
        5
    )

    if ok:

        send_episode_to_user(
            chat_id,
            user_id
        )

        return

    send_message(
        chat_id,
        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر کانال "
        "@altiustuistsnbol را ری اکت بزنید و سپس برگردید "
        "و دکمه انجام دادم را کلیک کنید ♥️",
        reaction_keyboard()
    )


# ============================================================
# SEND EPISODE
# ============================================================

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
        episode
        .get("files")
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
                result
                .get("result", {})
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

    send_message(
        chat_id,
        f"فایل‌ها ارسال شد ✅\n"
        f"تا {DELETE_AFTER} ثانیه فرصت داری ذخیره‌شون کنی."
    )

    threading.Thread(
        target=delete_sent_messages_later,
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

        video = message["video"]

        return {
            "type": "video",
            "file_id": video.get("file_id"),
            "caption": message.get(
                "caption",
                ""
            )
        }

    if message.get("document"):

        document = message["document"]

        return {
            "type": "document",
            "file_id": document.get("file_id"),
            "caption": message.get(
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
    # پیش‌نمایش کاملاً جدا از قسمت اصلی ذخیره می‌شود.
    # مثلاً:
    #
    # قسمت اصلی:
    # ep_sxxxxxxxxxx_16
    #
    # پیش‌نمایش:
    # preview__ep_sxxxxxxxxxx_16
    #
    # بنابراین هیچ‌وقت با فایل‌های قسمت اصلی قاطی نمی‌شود.

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
        episode_key=episode_key,
        series_name=parsed["series_name"],
        episode_number=parsed["episode_number"],
        files=files,
        is_preview=is_preview
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
            f"https://t.me/{bot_username}"
            f"?start={episode_key}"
        )

    else:

        link = (
            f"/start {episode_key}"
        )

    if is_preview:

        send_message(
            ADMIN_ID,
            "✅ پیش‌نمایش ذخیره شد.\n\n"
            f"سریال: {parsed['series_name']}\n"
            f"قسمت: {parsed['episode_number']}\n"
            f"تعداد فایل‌های پیش‌نمایش: {len(files)}\n\n"
            f"لینک پیش‌نمایش:\n{link}"
        )

    else:

        send_message(
            ADMIN_ID,
            "✅ فایل ذخیره شد.\n\n"
            f"سریال: {parsed['series_name']}\n"
            f"قسمت: {parsed['episode_number']}\n"
            f"تعداد فایل‌های این قسمت: {len(files)}\n\n"
            f"لینک قسمت:\n{link}"
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

        parts = text.split(
            " ",
            1
        )

        if len(parts) != 2:

            send_message(
                chat_id,
                "فرمت درست:\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )

            return True

        values = [
            x.strip()
            for x in parts[1].split("|")
        ]

        if len(values) != 3:

            send_message(
                chat_id,
                "فرمت درست:\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )

            return True

        sponsor_chat_id = values[0]
        sponsor_title = values[1]
        sponsor_url = values[2]

        result = add_sponsor(
            sponsor_chat_id,
            sponsor_title,
            sponsor_url
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

        parts = text.split(
            maxsplit=1
        )

        if len(parts) != 2:

            send_message(
                chat_id,
                "فرمت درست:\n"
                "/remove_sponsor ID"
            )

            return True

        try:

            sponsor_id = int(
                parts[1]
            )

        except:

            send_message(
                chat_id,
                "❌ ID نامعتبر است."
            )

            return True

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

            result = (
                supabase
                .table("episodes")
                .select(
                    "episode_key,series_name,episode_number"
                )
                .order(
                    "series_name"
                )
                .order(
                    "episode_number"
                )
                .eq(
                    "is_preview",
                    False
                )
                .execute()
            )

            rows = result.data or []

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
                    f"قسمت {row.get('episode_number')}\n"
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
                "فرمت درست:\n/delete_episode EPISODE_KEY"
            )

            return True

        episode_key = (
            parts[1].strip()
        )

        delete_episode_from_db(
            episode_key
        )

        send_message(
            chat_id,
            f"✅ اطلاعات {episode_key} حذف شد."
        )

        return True

    return False


# ============================================================
# START PROCESS
# ============================================================

def process_start(
    chat_id,
    user_id,
    code
):

    episode = get_episode(
        code
    )

    if not episode:

        send_message(
            chat_id,
            "❌ لینک قسمت پیدا نشد."
        )

        return

    set_pending(
        user_id,
        code
    )

    if not is_main_channel_member(
        user_id
    ):

        show_join_page(
            chat_id,
            user_id
        )

        return

    missing = check_all_sponsors(
        user_id
    )

    if missing:

        send_message(
            chat_id,
            "هنوز عضویت بعضی کانال‌ها تأیید نشده 👇",
            sponsor_keyboard(missing)
        )

        return

    show_reaction_page(
        chat_id,
        user_id
    )


# ============================================================
# MESSAGE HANDLER
# ============================================================

def handle_message(
    message
):

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    from_user = message.get(
        "from",
        {}
    )

    user_id = from_user.get(
        "id"
    )

    if user_id:
        save_user(
            user_id
        )

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

        if len(parts) == 1:

            if user_id == ADMIN_ID:

                handle_admin_command(
                    chat_id,
                    user_id,
                    text
                )

            return

        code = parts[1].strip()

        process_start(
            chat_id,
            user_id,
            code
        )

        return

    if text:

        if handle_admin_command(
            chat_id,
            user_id,
            text
        ):
            return

    if user_id == ADMIN_ID:

        if handle_admin_file(
            message
        ):
            return


# ============================================================
# CALLBACK HANDLER
# ============================================================

def handle_callback(
    callback
):

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
        callback_message.get("chat")
        or {}
    )

    chat_id = chat.get(
        "id"
    )

    message_id = callback_message.get(
        "message_id"
    )

    # ========================================================
    # CHECK JOIN
    # ========================================================

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

            return

        if not is_main_channel_member(
            user_id
        ):

            send_message(
                chat_id,
                "هنوز عضو کانال اصلی نیستی 👇",
                main_channel_keyboard()
            )

            return

        missing = check_all_sponsors(
            user_id
        )

        if missing:

            send_message(
                chat_id,
                "هنوز عضویت بعضی کانال‌ها تأیید نشده 👇",
                sponsor_keyboard(missing)
            )

            return

        if message_id:

            delete_message(
                chat_id,
                message_id
            )

        show_reaction_page(
            chat_id,
            user_id
        )

        return

    # ========================================================
    # CHECK REACTIONS
    # ========================================================

    if data == "check_reactions":

        answer_callback(
            callback_id,
            "در حال بررسی ری‌اکشن‌ها..."
        )

        episode_key = get_pending(
            user_id
        )

        if not episode_key:

            send_message(
                chat_id,
                "❌ لینک قسمت پیدا نشد."
            )

            return

        ok, count = user_reacted_to_last_posts(
            user_id,
            5
        )

        if not ok:

            send_message(
                chat_id,
                "هنوز ری‌اکشن ۵ پست آخر کامل نشده ❤️\n"
                f"تعداد پست‌هایی که ربات بررسی می‌کند: {count}"
            )

            return

        if message_id:

            delete_message(
                chat_id,
                message_id
            )

        send_episode_to_user(
            chat_id,
            user_id
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

    return jsonify({
        "ok": True,
        "bot": "telegram"
    })


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

    print(
        "UPDATE:",
        update
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

        chat_username = chat.get(
            "username"
        )

        if (
            chat_username
            and chat_username.lower()
            == CHANNEL_ID
            .replace("@", "")
            .lower()
        ):

            message_id = channel_post.get(
                "message_id"
            )

            if message_id:

                save_channel_post(
                    message_id
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

        threading.Thread(
            target=handle_message,
            args=(message,),
            daemon=True
        ).start()

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

        threading.Thread(
            target=handle_callback,
            args=(callback,),
            daemon=True
        ).start()

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

    result = telegram_api(
        "setWebhook",
        {
            "url": WEBHOOK_URL,
            "allowed_updates": [
                "message",
                "channel_post",
                "callback_query"
            ]
        }
    )

    print(
        "WEBHOOK:",
        result
    )


# ============================================================
# KEEP ALIVE
# ============================================================

def keep_alive():

    while True:

        try:

            requests.get(
                WEBHOOK_URL,
                timeout=10
            )

        except Exception as e:

            print(
                "keep alive error:",
                e
            )

        time.sleep(
            KEEP_ALIVE_INTERVAL
        )


# ============================================================
# STARTUP
# ============================================================

if __name__ == "__main__":

    print(
        "Starting bot..."
    )

    try:

        setup_webhook()

    except Exception as e:

        print(
            "setup webhook error:",
            e
        )

    threading.Thread(
        target=keep_alive,
        daemon=True
    ).start()

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
