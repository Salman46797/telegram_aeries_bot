import os
import re
import time
import threading
import hashlib
import html
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
MIN_WAIT = 2

PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is missing")


supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

app = Flask(__name__)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# =========================================================
# TELEGRAM
# =========================================================

def tg(method, data=None):
    try:
        response = requests.post(
            f"{TG_API}/{method}",
            json=data or {},
            timeout=30
        )

        try:
            result = response.json()
        except Exception:
            result = {
                "ok": False,
                "description": response.text
            }

        if not result.get("ok"):
            print("TELEGRAM ERROR:", method, result)

        return result

    except Exception as e:
        print("TELEGRAM REQUEST ERROR:", method, repr(e))

        return {
            "ok": False,
            "description": repr(e)
        }


def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    if parse_mode:
        data["parse_mode"] = parse_mode

    return tg("sendMessage", data)


def delete_message(chat_id, message_id):
    return tg(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


def answer_callback(callback_id, text=None):
    data = {
        "callback_query_id": callback_id
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


def send_video(chat_id, file_id, caption=None):
    data = {
        "chat_id": chat_id,
        "video": file_id
    }

    if caption:
        data["caption"] = caption

    return tg("sendVideo", data)


def send_document(chat_id, file_id, caption=None):
    data = {
        "chat_id": chat_id,
        "document": file_id
    }

    if caption:
        data["caption"] = caption

    return tg("sendDocument", data)


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id):
    return int(user_id) == int(ADMIN_ID)


def normalize_digits(text):
    if not text:
        return text

    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )

    return text.translate(table)


def make_series_key(series_name):
    value = hashlib.sha1(
        series_name.strip().lower().encode("utf-8")
    ).hexdigest()[:10]

    return "s" + value


def make_episode_key(series_name, episode_number):
    return (
        "ep_"
        + make_series_key(series_name)
        + "_"
        + str(episode_number)
    )


def parse_episode_caption(caption):
    if not caption:
        return None

    text = normalize_digits(caption)

    series_match = re.search(
        r"سریال\s*[«\"“]?(.+?)[»\"”]?\s*(?:\n|$)",
        text,
        re.IGNORECASE
    )

    episode_match = re.search(
        r"قسمت\s*[:：\-]?\s*(\d+)",
        text,
        re.IGNORECASE
    )

    if not series_match or not episode_match:
        return None

    series_name = series_match.group(1).strip()
    episode_number = int(episode_match.group(1))

    if not series_name:
        return None

    return series_name, episode_number


def safe_error(error):
    text = str(error)

    if len(text) > 3500:
        text = text[:3500] + "\n..."

    return html.escape(text)


# =========================================================
# EPISODES
# =========================================================

def get_episode(episode_key):
    try:
        result = (
            supabase
            .table("episodes")
            .select("*")
            .eq("episode_key", episode_key)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

        return None

    except Exception as e:
        print("GET EPISODE ERROR:", repr(e))
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

        result = (
            supabase
            .table("episodes")
            .upsert(
                data,
                on_conflict="episode_key"
            )
            .execute()
        )

        print("SAVE EPISODE:", result)

        return True, None

    except Exception as e:
        print("SAVE EPISODE ERROR:", repr(e))

        return False, repr(e)


# =========================================================
# PENDING
# =========================================================

def save_pending(user_id, episode_key):
    try:
        (
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

        return True

    except Exception as e:
        print("SAVE PENDING ERROR:", repr(e))
        return False


def get_pending(user_id):
    try:
        result = (
            supabase
            .table("pending")
            .select("*")
            .eq("user_id", int(user_id))
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

        return None

    except Exception as e:
        print("GET PENDING ERROR:", repr(e))
        return None


def delete_pending(user_id):
    try:
        (
            supabase
            .table("pending")
            .delete()
            .eq("user_id", int(user_id))
            .execute()
        )

    except Exception as e:
        print("DELETE PENDING ERROR:", repr(e))


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
        print("GET SPONSORS ERROR:", repr(e))
        return []


def add_sponsor(chat_id, title, url):
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

        return True, result

    except Exception as e:
        print("ADD SPONSOR ERROR:", repr(e))
        return False, e


def remove_sponsor(sponsor_id):
    try:
        (
            supabase
            .table("sponsors")
            .delete()
            .eq("id", int(sponsor_id))
            .execute()
        )

        return True

    except Exception as e:
        print("REMOVE SPONSOR ERROR:", repr(e))
        return False


# =========================================================
# CHANNEL POSTS
# =========================================================

def save_channel_post(message_id):
    try:
        (
            supabase
            .table("channel_posts")
            .upsert(
                {
                    "message_id": int(message_id)
                },
                on_conflict="message_id"
            )
            .execute()
        )

        return True

    except Exception as e:
        print("SAVE CHANNEL POST ERROR:", repr(e))
        return False


# =========================================================
# MEMBERSHIP
# =========================================================

def is_member(chat_id, user_id):
    result = get_chat_member(
        chat_id,
        user_id
    )

    if not result.get("ok"):
        print(
            "MEMBERSHIP ERROR:",
            chat_id,
            user_id,
            result
        )

        return False

    status = result["result"].get("status")

    return status in (
        "creator",
        "administrator",
        "member"
    )


def check_all_sponsors(user_id):
    sponsors = get_sponsors()

    for sponsor in sponsors:

        if not is_member(
            sponsor["chat_id"],
            user_id
        ):
            return False, sponsor

    return True, None


# =========================================================
# KEYBOARDS
# =========================================================

def main_join_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📢 عضویت در کانال اصلی",
                    "url": CHANNEL_URL
                }
            ],
            [
                {
                    "text": "عضو شدم ✅",
                    "callback_data": "check_join"
                }
            ]
        ]
    }


def sponsor_keyboard():
    sponsors = get_sponsors()

    rows = []

    for sponsor in sponsors:
        rows.append(
            [
                {
                    "text": f"📢 {sponsor['title']}",
                    "url": sponsor["url"]
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
                    "text": "📢 رفتن به کانال",
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


# =========================================================
# SEND EPISODE
# =========================================================

def delete_later(chat_id, message_id):

    def worker():
        time.sleep(DELETE_AFTER)

        delete_message(
            chat_id,
            message_id
        )

    threading.Thread(
        target=worker,
        daemon=True
    ).start()


def send_episode_to_user(user_id, episode):

    files = episode.get("files") or []

    if not files:
        send_message(
            user_id,
            "❌ فایل این قسمت پیدا نشد."
        )
        return

    for file_data in files:

        file_type = file_data.get("type")
        file_id = file_data.get("file_id")
        caption = file_data.get("caption")

        if not file_id:
            continue

        if file_type == "video":

            result = send_video(
                user_id,
                file_id,
                caption
            )

        else:

            result = send_document(
                user_id,
                file_id,
                caption
            )

        if result.get("ok"):

            sent_message = result["result"]

            delete_later(
                user_id,
                sent_message["message_id"]
            )

        else:

            print(
                "SEND FILE ERROR:",
                result
            )


# =========================================================
# USER FLOW
# =========================================================

def show_reaction_step(user_id):

    send_message(
        user_id,
        "برای دریافت فایل موردنظرت، ۵ پست آخر این کانال رو ببین 👇\n\n"
        f"{CHANNEL_URL}\n\n"
        "بعد از چند ثانیه روی «انجام شد ✅» بزن.",
        reaction_keyboard()
    )


def start_episode(user_id, episode_key):

    episode = get_episode(
        episode_key
    )

    if not episode:

        send_message(
            user_id,
            "❌ این قسمت پیدا نشد یا لینک اشتباه است."
        )

        return

    save_pending(
        user_id,
        episode_key
    )

    if not is_member(
        CHANNEL_ID,
        user_id
    ):

        send_message(
            user_id,
            "اول عضو کانال اصلی شو 👇",
            main_join_keyboard()
        )

        return

    sponsors_ok, sponsor = check_all_sponsors(
        user_id
    )

    if not sponsors_ok:

        send_message(
            user_id,
            "برای دریافت قسمت، اول داخل کانال‌های زیر عضو شو 👇",
            sponsor_keyboard()
        )

        return

    show_reaction_step(
        user_id
    )


def continue_after_join(user_id):

    pending = get_pending(
        user_id
    )

    if not pending:

        send_message(
            user_id,
            "❌ درخواست فعالی پیدا نشد."
        )

        return

    episode_key = pending["episode_key"]

    episode = get_episode(
        episode_key
    )

    if not episode:

        send_message(
            user_id,
            "❌ این قسمت پیدا نشد."
        )

        return

    if not is_member(
        CHANNEL_ID,
        user_id
    ):

        send_message(
            user_id,
            "❌ هنوز عضو کانال اصلی نشدی.",
            main_join_keyboard()
        )

        return

    sponsors_ok, sponsor = check_all_sponsors(
        user_id
    )

    if not sponsors_ok:

        send_message(
            user_id,
            "❌ هنوز داخل همه کانال‌های لازم عضو نشدی.",
            sponsor_keyboard()
        )

        return

    show_reaction_step(
        user_id
    )


def continue_after_reactions(user_id):

    pending = get_pending(
        user_id
    )

    if not pending:

        send_message(
            user_id,
            "❌ درخواست فعالی پیدا نشد."
        )

        return

    # فقط یک تأخیر خیلی کوتاه برای اینکه کاربر
    # مرحله کانال را ببیند.
    created_at = pending.get("created_at")

    if created_at:

        try:

            from datetime import datetime, timezone

            created = datetime.fromisoformat(
                created_at.replace(
                    "Z",
                    "+00:00"
                )
            )

            elapsed = (
                datetime.now(timezone.utc) - created
            ).total_seconds()

            if elapsed < MIN_WAIT:

                time.sleep(
                    MIN_WAIT - elapsed
                )

        except Exception as e:

            print(
                "WAIT CHECK ERROR:",
                repr(e)
            )

    episode_key = pending["episode_key"]

    episode = get_episode(
        episode_key
    )

    if not episode:

        send_message(
            user_id,
            "❌ این قسمت پیدا نشد."
        )

        return

    send_episode_to_user(
        user_id,
        episode
    )

    delete_pending(
        user_id
    )


# =========================================================
# ADMIN
# =========================================================

def handle_admin_command(message):

    user = message.get("from") or {}
    user_id = user.get("id")

    if not user_id:
        return False

    if not is_admin(user_id):
        return False

    text = message.get(
        "text",
        ""
    ).strip()

    # ---------------------------------
    # START
    # ---------------------------------

    if text == "/start":

        send_message(
            user_id,
            "👋 پنل مدیریت ربات فعال است.\n\n"
            "/sponsors - لیست اسپانسرها\n"
            "/add_sponsor - افزودن اسپانسر\n"
            "/remove_sponsor ID - حذف اسپانسر\n"
            "/episodes - لیست قسمت‌ها\n"
            "/testdb - تست دیتابیس"
        )

        return True

    # ---------------------------------
    # TEST DB
    # ---------------------------------

    if text == "/testdb":

        try:

            result = (
                supabase
                .table("episodes")
                .select("id")
                .limit(1)
                .execute()
            )

            send_message(
                user_id,
                "✅ اتصال ربات به Supabase برقرار است.\n\n"
                f"نتیجه: {result.data}"
            )

        except Exception as e:

            send_message(
                user_id,
                "❌ اتصال به Supabase مشکل دارد.\n\n"
                f"{safe_error(e)}"
            )

        return True

    # ---------------------------------
    # SPONSORS
    # ---------------------------------

    if text == "/sponsors":

        sponsors = get_sponsors()

        if not sponsors:

            send_message(
                user_id,
                "📭 هیچ اسپانسری ثبت نشده."
            )

            return True

        lines = [
            "📋 لیست اسپانسرها:\n"
        ]

        for sponsor in sponsors:

            lines.append(
                f"🆔 {sponsor['id']}\n"
                f"📢 {sponsor['title']}\n"
                f"🔗 {sponsor['url']}\n"
            )

        send_message(
            user_id,
            "\n".join(lines)
        )

        return True

    # ---------------------------------
    # ADD SPONSOR
    # ---------------------------------

    if text.startswith("/add_sponsor"):

        parts = text.split("|")

        if len(parts) != 3:

            send_message(
                user_id,
                "❌ فرمت درست:\n\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )

            return True

        first = parts[0].strip()
        title = parts[1].strip()
        url = parts[2].strip()

        first_parts = first.split(
            maxsplit=1
        )

        if len(first_parts) != 2:

            send_message(
                user_id,
                "❌ فرمت درست:\n\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )

            return True

        chat_id = first_parts[1].strip()

        if not chat_id.startswith("@"):

            send_message(
                user_id,
                "❌ آیدی کانال باید با @ شروع شود."
            )

            return True

        ok, result = add_sponsor(
            chat_id,
            title,
            url
        )

        if ok:

            send_message(
                user_id,
                "✅ اسپانسر با موفقیت ذخیره شد."
            )

        else:

            send_message(
                user_id,
                "❌ ذخیره اسپانسر انجام نشد.\n\n"
                f"{safe_error(result)}"
            )

        return True

    # ---------------------------------
    # REMOVE SPONSOR
    # ---------------------------------

    if text.startswith("/remove_sponsor"):

        parts = text.split()

        if len(parts) != 2:

            send_message(
                user_id,
                "❌ فرمت درست:\n\n"
                "/remove_sponsor ID"
            )

            return True

        try:

            sponsor_id = int(parts[1])

        except:

            send_message(
                user_id,
                "❌ ID باید عدد باشد."
            )

            return True

        if remove_sponsor(
            sponsor_id
        ):

            send_message(
                user_id,
                "✅ اسپانسر حذف شد."
            )

        else:

            send_message(
                user_id,
                "❌ حذف اسپانسر انجام نشد."
            )

        return True

    # ---------------------------------
    # EPISODES
    # ---------------------------------

    if text == "/episodes":

        try:

            result = (
                supabase
                .table("episodes")
                .select(
                    "episode_key,series_name,episode_number"
                )
                .order(
                    "id",
                    desc=True
                )
                .limit(20)
                .execute()
            )

            if not result.data:

                send_message(
                    user_id,
                    "📭 هنوز قسمتی ذخیره نشده."
                )

                return True

            lines = [
                "🎬 آخرین قسمت‌ها:\n"
            ]

            for item in result.data:

                lines.append(
                    f"• {item['series_name']} - قسمت {item['episode_number']}\n"
                    f"https://t.me/Seryyaltorki_bot?start={item['episode_key']}\n"
                )

            send_message(
                user_id,
                "\n".join(lines)
            )

        except Exception as e:

            send_message(
                user_id,
                f"❌ خطا:\n{safe_error(e)}"
            )

        return True

    return False


# =========================================================
# MESSAGE / VIDEO / DOCUMENT
# =========================================================

def handle_message(message):

    user = message.get("from") or {}
    user_id = user.get("id")

    if not user_id:
        return

    # -------------------------------
    # ADMIN
    # -------------------------------

    if is_admin(user_id):

        if handle_admin_command(message):
            return

    # -------------------------------
    # START DEEP LINK
    # -------------------------------

    text = message.get(
        "text",
        ""
    ).strip()

    if text.startswith("/start"):

        parts = text.split(
            maxsplit=1
        )

        if len(parts) == 2:

            episode_key = parts[1].strip()

            start_episode(
                user_id,
                episode_key
            )

            return

        if is_admin(user_id):

            send_message(
                user_id,
                "👋 پنل مدیریت فعال است."
            )

        else:

            send_message(
                user_id,
                "سلام 👋\n"
                "برای دریافت قسمت، لینک قسمت موردنظر رو باز کن."
            )

        return

    # -------------------------------
    # ADMIN VIDEO
    # -------------------------------

    if is_admin(user_id):

        video = message.get("video")
        document = message.get("document")

        if video or document:

            caption = message.get(
                "caption",
                ""
            )

            parsed = parse_episode_caption(
                caption
            )

            if not parsed:

                send_message(
                    user_id,
                    "❌ کپشن قابل تشخیص نیست.\n\n"
                    "مثال:\n"
                    "سریال «جایی که خورشید طلوع میکند»\n"
                    "قسمت: 1"
                )

                return

            series_name, episode_number = parsed

            episode_key = make_episode_key(
                series_name,
                episode_number
            )

            if video:

                file_data = {
                    "type": "video",
                    "file_id": video["file_id"],
                    "caption": caption
                }

            else:

                file_data = {
                    "type": "document",
                    "file_id": document["file_id"],
                    "caption": caption
                }

            existing = get_episode(
                episode_key
            )

            files = []

            if existing:

                files = existing.get(
                    "files"
                ) or []

            files.append(
                file_data
            )

            ok, error = save_episode(
                episode_key,
                series_name,
                episode_number,
                files
            )

            if not ok:

                send_message(
                    user_id,
                    "❌ ذخیره ویدیو انجام نشد.\n\n"
                    f"{safe_error(error)}"
                )

                return

            send_message(
                user_id,
                "✅ قسمت با موفقیت ذخیره شد.\n\n"
                f"🎬 سریال: {series_name}\n"
                f"🔢 قسمت: {episode_number}\n"
                f"📦 تعداد فایل: {len(files)}\n\n"
                "🔗 لینک قسمت:\n"
                f"https://t.me/Seryyaltorki_bot?start={episode_key}"
            )

            return


# =========================================================
# CALLBACKS
# =========================================================

def handle_callback(callback):

    callback_id = callback.get("id")

    data = callback.get(
        "data",
        ""
    )

    message = callback.get(
        "message"
    ) or {}

    user = callback.get(
        "from"
    ) or {}

    user_id = user.get("id")

    chat_id = (
        message.get("chat", {})
        .get("id")
    )

    message_id = message.get(
        "message_id"
    )

    if not user_id:
        return

    # ---------------------------------
    # CHECK JOIN
    # ---------------------------------

    if data == "check_join":

        answer_callback(
            callback_id,
            "⏳ در حال بررسی عضویت..."
        )

        # کانال اصلی
        if not is_member(
            CHANNEL_ID,
            user_id
        ):

            send_message(
                user_id,
                "❌ هنوز عضو کانال اصلی نشدی.",
                main_join_keyboard()
            )

            return

        # اسپانسرها
        sponsors_ok, sponsor = check_all_sponsors(
            user_id
        )

        if not sponsors_ok:

            # پیام قبلی دکمه‌دار حذف شود
            if chat_id and message_id:
                delete_message(
                    chat_id,
                    message_id
                )

            send_message(
                user_id,
                "❌ هنوز داخل همه کانال‌های لازم عضو نشدی.",
                sponsor_keyboard()
            )

            return

        # پیام قبلی حذف شود
        if chat_id and message_id:

            delete_message(
                chat_id,
                message_id
            )

        continue_after_join(
            user_id
        )

        return

    # ---------------------------------
    # CHECK REACTIONS
    # ---------------------------------

    if data == "check_reactions":

        answer_callback(
            callback_id,
            "⏳ آماده‌سازی فایل..."
        )

        # پیام مرحله قبلی حذف شود
        if chat_id and message_id:

            delete_message(
                chat_id,
                message_id
            )

        continue_after_reactions(
            user_id
        )

        return


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/",
    methods=["GET", "HEAD"]
)
def home():

    return "OK", 200


@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        ) or {}

        print(
            "UPDATE:",
            update
        )

        # -----------------------------
        # CALLBACK
        # -----------------------------

        if "callback_query" in update:

            handle_callback(
                update["callback_query"]
            )

            return jsonify(
                {"ok": True}
            )

        # -----------------------------
        # CHANNEL POST
        # -----------------------------

        if "channel_post" in update:

            channel_post = update[
                "channel_post"
            ]

            chat = channel_post.get(
                "chat"
            ) or {}

            username = chat.get(
                "username"
            )

            if username and username.lower() == CHANNEL_ID.replace("@", "").lower():

                message_id = channel_post.get(
                    "message_id"
                )

                if message_id:

                    save_channel_post(
                        message_id
                    )

            return jsonify(
                {"ok": True}
            )

        # -----------------------------
        # NORMAL MESSAGE
        # -----------------------------

        if "message" in update:

            handle_message(
                update["message"]
            )

            return jsonify(
                {"ok": True}
            )

        return jsonify(
            {"ok": True}
        )

    except Exception as e:

        print(
            "WEBHOOK ERROR:",
            repr(e)
        )

        return jsonify(
            {"ok": True}
        )


# =========================================================
# SET WEBHOOK
# =========================================================

def set_webhook():

    # آدرس Render را از محیط می‌گیرد
    render_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        ""
    ).strip()

    if not render_url:

        print(
            "RENDER_EXTERNAL_URL is missing"
        )

        return

    webhook_url = (
        render_url.rstrip("/")
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
            ]
        }
    )

    print(
        "SET WEBHOOK:",
        result
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    threading.Thread(
        target=set_webhook,
        daemon=True
    ).start()

    app.run(
        host="0.0.0.0",
        port=PORT
    )
