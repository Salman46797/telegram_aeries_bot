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


# =========================================================
# TELEGRAM API
# =========================================================

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


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


def get_me():
    return tg("getMe")


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


def normalize_digits(text):
    if not text:
        return text

    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )

    return text.translate(table)


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


def is_admin(user_id):
    return int(user_id) == int(ADMIN_ID)


def safe_error(error):
    text = str(error)

    if len(text) > 3500:
        text = text[:3500] + "\n..."

    return html.escape(text)


# =========================================================
# SUPABASE - EPISODES
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

        print("========== SAVE EPISODE START ==========")
        print("DATA:", data)

        result = (
            supabase
            .table("episodes")
            .upsert(
                data,
                on_conflict="episode_key"
            )
            .execute()
        )

        print("SUPABASE RESULT:", result)
        print("========== SAVE EPISODE SUCCESS ==========")

        return True, None

    except Exception as e:
        error_text = repr(e)

        print("========== SAVE EPISODE ERROR ==========")
        print(error_text)
        print("========================================")

        return False, error_text


# =========================================================
# SUPABASE - PENDING
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
# SUPABASE - SPONSORS
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
# SUPABASE - CHANNEL POSTS
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


def get_last_five_posts():
    try:
        result = (
            supabase
            .table("channel_posts")
            .select("message_id")
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )

        return [
            int(x["message_id"])
            for x in (result.data or [])
        ]

    except Exception as e:
        print("GET LAST FIVE POSTS ERROR:", repr(e))
        return []


# =========================================================
# SUPABASE - REACTIONS
# =========================================================

def save_reaction(user_id, message_id, reacted):
    try:
        # اول رکورد قبلی همین کاربر و پست را پاک می‌کنیم
        # تا به unique constraint احتیاج نداشته باشیم.
        (
            supabase
            .table("reactions")
            .delete()
            .eq("user_id", int(user_id))
            .eq("message_id", int(message_id))
            .execute()
        )

        (
            supabase
            .table("reactions")
            .insert(
                {
                    "user_id": int(user_id),
                    "message_id": int(message_id),
                    "reacted": bool(reacted)
                }
            )
            .execute()
        )

        return True

    except Exception as e:
        print("SAVE REACTION ERROR:", repr(e))
        return False


def has_reacted_to_last_five(user_id):
    posts = get_last_five_posts()

    if len(posts) < 5:
        print("LESS THAN 5 CHANNEL POSTS:", len(posts))
        return False

    try:
        result = (
            supabase
            .table("reactions")
            .select("message_id,reacted")
            .eq("user_id", int(user_id))
            .eq("reacted", True)
            .in_("message_id", posts)
            .execute()
        )

        reacted_ids = {
            int(x["message_id"])
            for x in (result.data or [])
        }

        return all(
            int(post_id) in reacted_ids
            for post_id in posts
        )

    except Exception as e:
        print("CHECK REACTIONS ERROR:", repr(e))
        return False


# =========================================================
# MEMBERSHIP
# =========================================================

def is_member(chat_id, user_id):
    result = get_chat_member(chat_id, user_id)

    if not result.get("ok"):
        print(
            "MEMBERSHIP CHECK FAILED:",
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
        chat_id = sponsor["chat_id"]

        if not is_member(chat_id, user_id):
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

        try:
            delete_message(
                chat_id,
                message_id
            )
        except Exception as e:
            print("DELETE LATER ERROR:", repr(e))

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

def start_episode(user_id, episode_key):
    episode = get_episode(episode_key)

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

    # کانال اصلی
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

    # اسپانسرها
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

    # ری‌اکشن
    send_message(
        user_id,
        "برای دریافت فایل موردنظرت، ۵ پست آخر این کانال رو ری‌اکشن (❤️) بزن 👇\n\n"
        f"{CHANNEL_URL}",
        reaction_keyboard()
    )


def continue_after_join(user_id):
    pending = get_pending(user_id)

    if not pending:
        send_message(
            user_id,
            "❌ درخواست فعالی پیدا نشد."
        )
        return

    episode_key = pending["episode_key"]

    episode = get_episode(episode_key)

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

    send_message(
        user_id,
        "حالا ۵ پست آخر کانال رو با ❤️ ری‌اکشن کن و بعد «انجام شد ✅» رو بزن 👇\n\n"
        f"{CHANNEL_URL}",
        reaction_keyboard()
    )


def continue_after_reactions(user_id):
    pending = get_pending(user_id)

    if not pending:
        send_message(
            user_id,
            "❌ درخواست فعالی پیدا نشد."
        )
        return

    episode_key = pending["episode_key"]

    episode = get_episode(episode_key)

    if not episode:
        send_message(
            user_id,
            "❌ این قسمت پیدا نشد."
        )
        return

    if not has_reacted_to_last_five(user_id):
        send_message(
            user_id,
            "❌ هنوز هر ۵ پست آخر رو ❤️ ری‌اکشن نکردی.\n\n"
            "هر ۵ پست رو ری‌اکشن کن و دوباره «انجام شد ✅» رو بزن.",
            reaction_keyboard()
        )
        return

    send_episode_to_user(
        user_id,
        episode
    )

    delete_pending(user_id)


# =========================================================
# ADMIN
# =========================================================

def handle_admin_command(message):
    user = message.get("from") or {}
    user_id = user.get("id")

    if not user_id or not is_admin(user_id):
        return False

    text = message.get("text", "").strip()

    if text == "/start":
        send_message(
            user_id,
            "👋 پنل مدیریت ربات فعال است.\n\n"
            "/sponsors - لیست اسپانسرها\n"
            "/add_sponsor - افزودن اسپانسر\n"
            "/remove_sponsor ID - حذف اسپانسر\n"
            "/episodes - لیست قسمت‌ها\n"
            "/testdb - تست اتصال دیتابیس"
        )
        return True

    if text == "/sponsors":
        sponsors = get_sponsors()

        if not sponsors:
            send_message(
                user_id,
                "📭 هیچ اسپانسری ثبت نشده."
            )
            return True

        lines = ["📋 لیست اسپانسرها:\n"]

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

        first_parts = first.split(maxsplit=1)

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

        if not url.startswith("https://t.me/"):
            send_message(
                user_id,
                "❌ لینک کانال باید با https://t.me/ شروع شود."
            )
            return True

        if not title:
            send_message(
                user_id,
                "❌ نام کانال خالی است."
            )
            return True

        success, result = add_sponsor(
            chat_id,
            title,
            url
        )

        if not success:
            send_message(
                user_id,
                "❌ ذخیره اسپانسر انجام نشد.\n\n"
                "خطای واقعی:\n"
                f"<code>{safe_error(result)}</code>",
                parse_mode="HTML"
            )
            return True

        send_message(
            user_id,
            "✅ اسپانسر با موفقیت ذخیره شد."
        )
        return True

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
        except ValueError:
            send_message(
                user_id,
                "❌ ID باید عدد باشد."
            )
            return True

        if remove_sponsor(sponsor_id):
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

    if text == "/episodes":
        try:
            result = (
                supabase
                .table("episodes")
                .select(
                    "id,series_name,episode_number,episode_key"
                )
                .order("id", desc=True)
                .limit(30)
                .execute()
            )

            rows = result.data or []

            if not rows:
                send_message(
                    user_id,
                    "📭 هیچ قسمتی ذخیره نشده."
                )
                return True

            lines = ["📚 آخرین قسمت‌ها:\n"]

            for row in rows:
                lines.append(
                    f"🆔 {row['id']} | "
                    f"{row['series_name']} | "
                    f"قسمت {row['episode_number']}"
                )

            send_message(
                user_id,
                "\n".join(lines)
            )

        except Exception as e:
            send_message(
                user_id,
                "❌ خطا:\n\n"
                f"<code>{safe_error(e)}</code>",
                parse_mode="HTML"
            )

        return True

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
                f"نتیجه: {html.escape(str(result.data))}",
                parse_mode="HTML"
            )

        except Exception as e:
            send_message(
                user_id,
                "❌ اتصال ربات به Supabase مشکل دارد.\n\n"
                "خطای واقعی:\n"
                f"<code>{safe_error(e)}</code>",
                parse_mode="HTML"
            )

        return True

    return False


def handle_admin_media(message):
    user = message.get("from") or {}
    user_id = user.get("id")

    if not user_id or not is_admin(user_id):
        return False

    file_type = None
    file_id = None

    if message.get("video"):
        file_type = "video"
        file_id = message["video"]["file_id"]

    elif message.get("document"):
        file_type = "document"
        file_id = message["document"]["file_id"]

    else:
        return False

    caption = (
        message.get("caption")
        or ""
    ).strip()

    parsed = parse_episode_caption(
        caption
    )

    if not parsed:
        send_message(
            user_id,
            "❌ کپشن قابل تشخیص نیست.\n\n"
            "فرمت نمونه:\n"
            "سریال «بالا پایین استانبول»\n"
            "قسمت : 15\n"
            "کیفیت : 1080p"
        )
        return True

    series_name, episode_number = parsed

    episode_key = make_episode_key(
        series_name,
        episode_number
    )

    existing = get_episode(
        episode_key
    )

    files = []

    if existing:
        files = existing.get("files") or []

    new_file = {
        "type": file_type,
        "file_id": file_id,
        "caption": caption
    }

    # جلوگیری از ذخیره فایل تکراری
    duplicate = False

    for old_file in files:
        if old_file.get("file_id") == file_id:
            duplicate = True
            break

    if not duplicate:
        files.append(new_file)

    success, error = save_episode(
        episode_key,
        series_name,
        episode_number,
        files
    )

    if not success:
        send_message(
            user_id,
            "❌ ذخیره ویدیو انجام نشد.\n\n"
            "خطای واقعی:\n"
            f"<code>{safe_error(error)}</code>",
            parse_mode="HTML"
        )
        return True

    me = get_me()

    if not me.get("ok"):
        send_message(
            user_id,
            "✅ ویدیو ذخیره شد، ولی گرفتن آیدی ربات برای ساخت لینک خطا داد."
        )
        return True

    bot_username = me["result"]["username"]

    deep_link = (
        f"https://t.me/{bot_username}?start={episode_key}"
    )

    send_message(
        user_id,
        "✅ قسمت با موفقیت ذخیره شد.\n\n"
        f"🎬 سریال: {series_name}\n"
        f"🔢 قسمت: {episode_number}\n"
        f"📦 تعداد فایل: {len(files)}\n\n"
        f"🔗 لینک قسمت:\n{deep_link}"
    )

    return True


# =========================================================
# CALLBACKS
# =========================================================

def handle_callback(callback):
    callback_id = callback.get("id")
    data = callback.get("data")

    user = callback.get("from") or {}
    user_id = user.get("id")

    if not user_id:
        return

    if data == "check_join":
        answer_callback(
            callback_id,
            "در حال بررسی عضویت..."
        )

        continue_after_join(
            user_id
        )

        return

    if data == "check_reactions":
        answer_callback(
            callback_id,
            "در حال بررسی ری‌اکشن‌ها..."
        )

        continue_after_reactions(
            user_id
        )

        return


# =========================================================
# CHANNEL POSTS
# =========================================================

def handle_channel_post(message):
    chat = message.get("chat") or {}

    username = chat.get("username")

    if username:
        if username.lower() == CHANNEL_ID.replace("@", "").lower():
            message_id = message.get("message_id")

            if message_id:
                save_channel_post(
                    message_id
                )


# =========================================================
# REACTION UPDATES
# =========================================================

def reaction_contains_heart(reactions):
    for reaction in reactions or []:
        reaction_type = reaction.get("type")

        if reaction_type != "emoji":
            continue

        emoji = reaction.get("emoji")

        if emoji == "❤️":
            return True

    return False


def handle_message_reaction(update):
    user = update.get("user") or {}

    user_id = user.get("id")

    if not user_id:
        return

    chat = update.get("chat") or {}

    username = chat.get("username")

    if not username:
        return

    if username.lower() != CHANNEL_ID.replace("@", "").lower():
        return

    message_id = update.get("message_id")

    if not message_id:
        return

    new_reaction = update.get(
        "new_reaction",
        []
    )

    has_heart = reaction_contains_heart(
        new_reaction
    )

    save_reaction(
        user_id,
        message_id,
        has_heart
    )


# =========================================================
# MESSAGE PROCESSING
# =========================================================

def process_message(message):
    if not message:
        return

    user = message.get("from") or {}
    user_id = user.get("id")

    if not user_id:
        return

    # دستورات ادمین
    if "text" in message and is_admin(user_id):
        handled = handle_admin_command(
            message
        )

        if handled:
            return

    # ویدیو / فایل ادمین
    if is_admin(user_id):
        if message.get("video") or message.get("document"):
            handled = handle_admin_media(
                message
            )

            if handled:
                return

    # /start
    text = message.get("text", "").strip()

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)

        if len(parts) == 1:
            send_message(
                user_id,
                "سلام 👋\n"
                "لینک قسمت موردنظرت رو باز کن."
            )
            return

        episode_key = parts[1].strip()

        if episode_key:
            start_episode(
                user_id,
                episode_key
            )

        return


# =========================================================
# WEBHOOK
# =========================================================

@app.route("/", methods=["GET", "HEAD"])
def home():
    return "Telegram bot is running.", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = request.get_json(
            silent=True
        )

        if not update:
            return jsonify(
                {
                    "ok": True
                }
            )

        print(
            "UPDATE TYPE:",
            list(update.keys())
        )

        # channel post
        if update.get("channel_post"):
            handle_channel_post(
                update["channel_post"]
            )

        # reaction update
        elif update.get("message_reaction"):
            handle_message_reaction(
                update["message_reaction"]
            )

        # callback
        elif update.get("callback_query"):
            handle_callback(
                update["callback_query"]
            )

        # normal message
        elif update.get("message"):
            process_message(
                update["message"]
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
                "ok": True
            }
        )


# =========================================================
# WEBHOOK SETUP
# =========================================================

def setup_webhook():
    try:
        render_url = os.getenv(
            "RENDER_EXTERNAL_URL"
        )

        if not render_url:
            print(
                "RENDER_EXTERNAL_URL not found."
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
                    "channel_post",
                    "message_reaction"
                ]
            }
        )

        print(
            "SET WEBHOOK RESULT:",
            result
        )

    except Exception as e:
        print(
            "SET WEBHOOK ERROR:",
            repr(e)
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    print("===================================")
    print("Telegram Bot Starting...")
    print("===================================")

    setup_webhook()

    app.run(
        host="0.0.0.0",
        port=PORT
    )
