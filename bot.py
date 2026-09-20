import os
import re
import time
import hashlib
import threading
import requests

from flask import Flask, request, jsonify
from supabase import create_client


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

DELETE_AFTER = 30


# =========================================================
# CHECK ENVIRONMENT
# =========================================================

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


# =========================================================
# TELEGRAM API
# =========================================================

def tg(method, data=None):

    try:

        response = requests.post(
            f"{TG_API}/{method}",
            json=data or {},
            timeout=30
        )

        result = response.json()

        print(
            "TELEGRAM",
            method,
            result
        )

        return result

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            repr(e)
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

    if reply_markup:
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
    alert=False
):

    data = {
        "callback_query_id": callback_id,
        "show_alert": alert
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


# =========================================================
# EPISODE KEY
# =========================================================

def make_series_key(series_name):

    value = hashlib.sha1(
        series_name.strip()
        .lower()
        .encode("utf-8")
    ).hexdigest()[:10]

    return "s" + value


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


# =========================================================
# CAPTION
# =========================================================

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

    if not series_match:
        return None

    if not episode_match:
        return None

    series_name = series_match.group(1).strip()

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


# =========================================================
# SUPABASE - EPISODES
# =========================================================

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

        print(
            "GET EPISODE RESULT:",
            result
        )

        if result.data:
            return result.data[0]

        return None

    except Exception as e:

        print(
            "GET EPISODE ERROR:",
            repr(e)
        )

        return None


def save_episode(
    episode_key,
    series_name,
    episode_number,
    files
):

    try:

        data = {
            "episode_key": episode_key,
            "series_name": series_name,
            "episode_number": episode_number,
            "files": files
        }

        print(
            "=============================="
        )

        print(
            "SAVE EPISODE DATA:"
        )

        print(
            data
        )

        print(
            "=============================="
        )

        result = (
            supabase
            .table("episodes")
            .upsert(
                data,
                on_conflict="episode_key"
            )
            .execute()
        )

        print(
            "SAVE EPISODE RESULT:"
        )

        print(
            result
        )

        print(
            "=============================="
        )

        return result

    except Exception as e:

        print(
            "=============================="
        )

        print(
            "SAVE EPISODE ERROR:"
        )

        print(
            repr(e)
        )

        print(
            "=============================="
        )

        return None


# =========================================================
# PENDING
# =========================================================

def set_pending(
    user_id,
    episode_key
):

    try:

        result = (
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

        return result

    except Exception as e:

        print(
            "SET PENDING ERROR:",
            repr(e)
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
                int(user_id)
            )
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0].get(
                "episode_key"
            )

    except Exception as e:

        print(
            "GET PENDING ERROR:",
            repr(e)
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
                int(user_id)
            )
            .execute()
        )

    except Exception as e:

        print(
            "CLEAR PENDING ERROR:",
            repr(e)
        )

        return None


# =========================================================
# SPONSORS
# =========================================================

def get_sponsors():

    try:

        result = (
            supabase
            .table("sponsors")
            .select("*")
            .order("id")
            .execute()
        )

        return result.data or []

    except Exception as e:

        print(
            "GET SPONSORS ERROR:",
            repr(e)
        )

        return []


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

        print(
            "SPONSOR SAVED:",
            result
        )

        return result

    except Exception as e:

        print(
            "ADD SPONSOR ERROR:",
            repr(e)
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
            "REMOVE SPONSOR ERROR:",
            repr(e)
        )

        return None


# =========================================================
# CHANNEL POSTS
# =========================================================

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
            "SAVE CHANNEL POST ERROR:",
            repr(e)
        )

        return None


def get_last_posts(
    limit=5
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
            .limit(limit)
            .execute()
        )

        return result.data or []

    except Exception as e:

        print(
            "GET LAST POSTS ERROR:",
            repr(e)
        )

        return []


# =========================================================
# REACTIONS
# =========================================================

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
                    "user_id": int(user_id),
                    "message_id": int(message_id),
                    "reacted": reacted
                },
                on_conflict="user_id,message_id"
            )
            .execute()
        )

    except Exception as e:

        print(
            "SAVE REACTION ERROR:",
            repr(e)
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
            .select("*")
            .eq(
                "user_id",
                int(user_id)
            )
            .eq(
                "message_id",
                int(message_id)
            )
            .eq(
                "reacted",
                True
            )
            .limit(1)
            .execute()
        )

        return bool(
            result.data
        )

    except Exception as e:

        print(
            "HAS REACTED ERROR:",
            repr(e)
        )

        return False


def user_reacted_to_last_posts(
    user_id,
    limit=5
):

    posts = get_last_posts(
        limit
    )

    if len(posts) < limit:
        return False

    for post in posts:

        message_id = post.get(
            "message_id"
        )

        if not message_id:
            return False

        if not has_reacted(
            user_id,
            message_id
        ):
            return False

    return True


# =========================================================
# MEMBERSHIP
# =========================================================

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

    missing = []

    for sponsor in sponsors:

        chat_id = sponsor.get(
            "chat_id"
        )

        if not chat_id:
            continue

        if not member_status(
            chat_id,
            user_id
        ):

            missing.append(
                sponsor
            )

    return missing


# =========================================================
# KEYBOARDS
# =========================================================

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


def channel_keyboard():

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


# =========================================================
# USER FLOW
# =========================================================

def show_reaction_page(
    chat_id,
    user_id
):

    if user_reacted_to_last_posts(
        user_id,
        5
    ):

        send_episode_to_user(
            chat_id,
            user_id
        )

        return

    send_message(
        chat_id,
        "برای دریافت فایل موردنظرت، ۵ پست آخر این کانال رو ری‌اکشن (❤️) بزن 👇\n\n"
        + CHANNEL_URL,
        reaction_keyboard()
    )


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
            channel_keyboard()
        )

        return

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

        return

    show_reaction_page(
        chat_id,
        user_id
    )


# =========================================================
# SEND EPISODE
# =========================================================

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

    files = episode.get(
        "files"
    ) or []

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

        if not file_id:
            continue

        if file_type == "video":

            result = send_video(
                chat_id,
                file_id,
                caption
            )

        else:

            result = send_document(
                chat_id,
                file_id,
                caption
            )

        if result.get("ok"):

            message_id = (
                result
                .get("result", {})
                .get("message_id")
            )

            if message_id:

                sent_ids.append(
                    message_id
                )

    if not sent_ids:

        send_message(
            chat_id,
            "ارسال فایل انجام نشد."
        )

        return

    send_message(
        chat_id,
        f"فایل ارسال شد ✅\n\n"
        f"تا {DELETE_AFTER} ثانیه فرصت داری ذخیره‌ش کنی."
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


# =========================================================
# FILE HANDLING
# =========================================================

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
            "❌ کپشن قابل تشخیص نیست.\n\n"
            "فرمت:\n"
            "🪴 سریال «اسم سریال»\n"
            "🪷 قسمت : 2\n"
            "🫧 زیرنویس فارسی\n"
            "🎍 کیفیت : 1080"
        )

        return True

    episode_key = parsed[
        "episode_key"
    ]

    episode = get_episode(
        episode_key
    )

    if episode:

        files = episode.get(
            "files"
        ) or []

    else:

        files = []

    files.append(
        file_info
    )

    result = save_episode(
        episode_key,
        parsed["series_name"],
        parsed["episode_number"],
        files
    )

    if result is None:

        send_message(
            ADMIN_ID,
            "❌ ذخیره ویدیو انجام نشد.\n\n"
            "جزئیات خطا داخل Logs رندر ثبت شده."
        )

        return True

    send_message(
        ADMIN_ID,
        "⏳ فایل در حال بررسی ذخیره‌سازی است..."
    )

    bot = get_me()

    username = (
        bot
        .get("result", {})
        .get("username")
    )

    if not username:

        send_message(
            ADMIN_ID,
            "❌ فایل ذخیره شد ولی نام کاربری ربات پیدا نشد."
        )

        return True

    link = (
        f"https://t.me/"
        f"{username}"
        f"?start={episode_key}"
    )

    send_message(
        ADMIN_ID,
        "✅ فایل با موفقیت ذخیره شد!\n\n"
        f"🎬 سریال: "
        f"{parsed['series_name']}\n"
        f"🪷 قسمت: "
        f"{parsed['episode_number']}\n"
        f"📁 تعداد فایل: "
        f"{len(files)}\n\n"
        f"🔗 لینک قسمت:\n"
        f"{link}"
    )

    return True


# =========================================================
# ADMIN COMMANDS
# =========================================================

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
            "🛠 پنل مدیریت ربات فعال است ✅\n\n"
            "/sponsors\n"
            "/add_sponsor @channel | نام کانال | https://t.me/channel\n"
            "/remove_sponsor ID"
        )

        return True


    if text == "/sponsors":

        sponsors = get_sponsors()

        if not sponsors:

            send_message(
                chat_id,
                "📭 هیچ اسپانسری ثبت نشده."
            )

            return True

        lines = [
            "📋 لیست اسپانسرها:\n"
        ]

        for sponsor in sponsors:

            lines.append(
                f"🆔 ID: {sponsor.get('id')}\n"
                f"📢 کانال: {sponsor.get('chat_id')}\n"
                f"📝 نام: {sponsor.get('title')}\n"
                f"🔗 لینک: {sponsor.get('url')}\n"
            )

        send_message(
            chat_id,
            "\n".join(lines)
        )

        return True


    if text.startswith(
        "/add_sponsor"
    ):

        value = text[
            len("/add_sponsor"):
        ].strip()

        parts = [
            x.strip()
            for x in value.split("|")
        ]

        if len(parts) != 3:

            send_message(
                chat_id,
                "❌ فرمت اشتباهه.\n\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )

            return True

        sponsor_chat_id = parts[0]
        title = parts[1]
        url = parts[2]

        if not sponsor_chat_id.startswith("@"):

            send_message(
                chat_id,
                "❌ آیدی کانال باید با @ شروع بشه."
            )

            return True

        if not url.startswith(
            "https://t.me/"
        ):

            send_message(
                chat_id,
                "❌ لینک باید با https://t.me/ شروع بشه."
            )

            return True

        saved = add_sponsor(
            sponsor_chat_id,
            title,
            url
        )

        if saved is None:

            send_message(
                chat_id,
                "❌ ذخیره اسپانسر انجام نشد.\n"
                "خطای دقیق داخل Logs رندر ثبت شده."
            )

            return True

        send_message(
            chat_id,
            "✅ اسپانسر با موفقیت اضافه شد."
        )

        return True


    if text.startswith(
        "/remove_sponsor"
    ):

        value = text[
            len("/remove_sponsor"):
        ].strip()

        if not value.isdigit():

            send_message(
                chat_id,
                "❌ فرمت درست:\n"
                "/remove_sponsor ID"
            )

            return True

        result = remove_sponsor(
            int(value)
        )

        if result is None:

            send_message(
                chat_id,
                "❌ حذف اسپانسر انجام نشد."
            )

            return True

        send_message(
            chat_id,
            "✅ اسپانسر حذف شد."
        )

        return True

    return False


# =========================================================
# START
# =========================================================

def handle_start(
    chat_id,
    user_id,
    payload
):

    if payload:

        episode = get_episode(
            payload
        )

        if not episode:

            send_message(
                chat_id,
                "❌ این قسمت پیدا نشد."
            )

            return

        set_pending(
            user_id,
            payload
        )

    show_join_page(
        chat_id,
        user_id
    )


# =========================================================
# CALLBACK
# =========================================================

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

    message = callback.get(
        "message",
        {}
    )

    chat = message.get(
        "chat",
        {}
    )

    user = callback.get(
        "from",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    user_id = user.get(
        "id"
    )


    if data == "check_join":

        answer_callback(
            callback_id,
            "در حال بررسی عضویت..."
        )

        if not is_main_channel_member(
            user_id
        ):

            send_message(
                chat_id,
                "❌ هنوز عضو کانال اصلی نشدی.",
                channel_keyboard()
            )

            return

        missing = check_all_sponsors(
            user_id
        )

        if missing:

            send_message(
                chat_id,
                "❌ هنوز عضو همه کانال‌ها نشدی.",
                sponsor_keyboard(
                    missing
                )
            )

            return

        show_reaction_page(
            chat_id,
            user_id
        )

        return


    if data == "check_reactions":

        answer_callback(
            callback_id,
            "در حال بررسی ری‌اکشن‌ها..."
        )

        if user_reacted_to_last_posts(
            user_id,
            5
        ):

            send_episode_to_user(
                chat_id,
                user_id
            )

        else:

            send_message(
                chat_id,
                "❌ هنوز ری‌اکشن هر ۵ پست ثبت نشده.\n"
                "۵ پست آخر رو ❤️ بزن و دوباره «انجام شد ✅» رو بزن.",
                reaction_keyboard()
            )

        return


# =========================================================
# PROCESS UPDATE
# =========================================================

def process_update(
    update
):

    if update.get(
        "callback_query"
    ):

        handle_callback(
            update["callback_query"]
        )

        return


    channel_post = update.get(
        "channel_post"
    )

    if channel_post:

        chat = channel_post.get(
            "chat",
            {}
        )

        username = chat.get(
            "username"
        )

        if username == CHANNEL_ID.lstrip("@"):

            message_id = channel_post.get(
                "message_id"
            )

            if message_id:

                save_channel_post(
                    message_id
                )

        return


    message = update.get(
        "message"
    )

    if not message:
        return

    chat = message.get(
        "chat",
        {}
    )

    user = message.get(
        "from",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    user_id = user.get(
        "id"
    )

    text = message.get(
        "text",
        ""
    )


    if user_id == ADMIN_ID:

        if (
            message.get("video")
            or message.get("document")
        ):

            if handle_admin_file(
                message
            ):

                return


    if text.startswith("/"):

        if handle_admin_command(
            chat_id,
            user_id,
            text
        ):

            return


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


# =========================================================
# FLASK
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return "Bot is running ✅"


@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        )

        if update:

            process_update(
                update
            )

        return jsonify(
            {
                "ok": True
            }
        )

    except Exception as e:

        print(
            "WEBHOOK ERROR:",
            repr(e)
        )

        return jsonify(
            {
                "ok": False
            }
        )


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
        port=port
    )
