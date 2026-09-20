import os
import re
import json
import random
import string
import threading
import time

import requests
from flask import Flask, request

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

WEBHOOK_URL = "https://telegram-aeries-bot.onrender.com/webhook"

app = Flask(__name__)


# =========================
# TELEGRAM API
# =========================

def telegram(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        r = requests.post(url, json=data or {}, timeout=30)

        try:
            return r.json()
        except:
            return {
                "ok": False,
                "description": r.text
            }

    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return {
            "ok": False,
            "description": str(e)
        }


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return telegram("sendMessage", data)


def delete_message(chat_id, message_id):
    return telegram("deleteMessage", {
        "chat_id": chat_id,
        "message_id": message_id
    })


# =========================
# SUPABASE
# =========================

def supabase_request(method, table, params=None, body=None):
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    try:
        r = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=body,
            timeout=30
        )

        print(
            "SUPABASE",
            method,
            table,
            r.status_code,
            r.text[:1000]
        )

        try:
            data = r.json()
        except:
            data = r.text

        return r.status_code < 300, data, r.status_code

    except Exception as e:
        print("SUPABASE REQUEST ERROR:", repr(e))
        return False, str(e), 0


# =========================
# RANDOM START CODE
# =========================

def generate_start_code():
    chars = string.ascii_lowercase + string.digits

    for _ in range(50):
        code = "".join(random.choice(chars) for _ in range(6))

        ok, data, status = supabase_request(
            "GET",
            "episodes",
            params={
                "start_code": f"eq.{code}",
                "select": "id"
            }
        )

        if ok and not data:
            return code

    return "".join(random.choice(chars) for _ in range(6))


# =========================
# PARSE CAPTION
# =========================

def parse_caption(caption):
    if not caption:
        return None, None, None

    # سریال
    series_match = re.search(
        r"سریال\s*[«\"]([^»\"]+)[»\"]",
        caption
    )

    # قسمت
    episode_match = re.search(
        r"قسمت\s*[:：]?\s*(\d+)",
        caption
    )

    if not series_match or not episode_match:
        return None, None, None

    series_name = series_match.group(1).strip()
    episode_number = int(episode_match.group(1))

    # نوع فایل
    file_type = "نامشخص"

    if "زبان اصلی" in caption:
        file_type = "زبان اصلی"

    elif "زیرنویس مووی باز" in caption:
        file_type = "زیرنویس مووی باز"

    elif "زیرنویس فوری" in caption:
        file_type = "زیرنویس فوری"

    return series_name, episode_number, file_type


# =========================
# GET EPISODE
# =========================

def get_episode_by_key(episode_key):
    ok, data, status = supabase_request(
        "GET",
        "episodes",
        params={
            "episode_key": f"eq.{episode_key}",
            "select": "*",
            "limit": "1"
        }
    )

    if not ok:
        print("GET EPISODE ERROR:", data)
        return None

    if isinstance(data, list) and data:
        return data[0]

    return None


def get_episode_by_code(code):
    ok, data, status = supabase_request(
        "GET",
        "episodes",
        params={
            "start_code": f"eq.{code}",
            "select": "*",
            "limit": "1"
        }
    )

    if not ok:
        print("GET CODE ERROR:", data)
        return None

    if isinstance(data, list) and data:
        return data[0]

    return None


# =========================
# SAVE EPISODE
# =========================

def save_episode(series_name, episode_number, file_info):

    episode_key = f"{series_name}__{episode_number}"

    print("SAVE EPISODE:", episode_key)

    # اول خود قسمت موجود را دقیقاً با episode_key پیدا می‌کنیم
    episode = get_episode_by_key(episode_key)

    # -------------------------
    # قسمت از قبل وجود دارد
    # -------------------------

    if episode:

        print("EXISTING EPISODE FOUND:", episode["id"])

        old_files = episode.get("files") or []

        if not isinstance(old_files, list):
            old_files = []

        # جلوگیری از ذخیره دوباره همان فایل
        existing_ids = [
            x.get("file_id")
            for x in old_files
            if isinstance(x, dict)
        ]

        if file_info["file_id"] not in existing_ids:
            old_files.append(file_info)

        ok, data, status = supabase_request(
            "PATCH",
            "episodes",
            params={
                "id": f"eq.{episode['id']}"
            },
            body={
                "files": old_files
            }
        )

        if not ok:
            print("UPDATE EPISODE ERROR:", data)
            return None

        print("EPISODE UPDATED:", episode["start_code"])

        return episode["start_code"]

    # -------------------------
    # قسمت جدید
    # -------------------------

    print("NEW EPISODE")

    start_code = generate_start_code()

    body = {
        "episode_key": episode_key,
        "series_name": series_name,
        "episode_number": episode_number,
        "files": [file_info],
        "start_code": start_code
    }

    ok, data, status = supabase_request(
        "POST",
        "episodes",
        body=body
    )

    if not ok:
        print("INSERT EPISODE ERROR:", data)

        # اگر به هر دلیلی قسمت همزمان توسط درخواست دیگری ساخته شده،
        # دوباره آن را پیدا می‌کنیم.
        existing = get_episode_by_key(episode_key)

        if existing:
            old_files = existing.get("files") or []

            if not isinstance(old_files, list):
                old_files = []

            old_files.append(file_info)

            supabase_request(
                "PATCH",
                "episodes",
                params={
                    "id": f"eq.{existing['id']}"
                },
                body={
                    "files": old_files
                }
            )

            return existing["start_code"]

        return None

    # -------------------------
    # POST موفق
    # -------------------------

    if isinstance(data, list) and data:
        saved = data[0]

        print("NEW EPISODE SAVED:", saved)

        return saved.get("start_code", start_code)

    # بعضی مواقع Supabase پاسخ خالی می‌دهد.
    # پس دوباره قسمت را می‌خوانیم.
    saved = get_episode_by_key(episode_key)

    if saved:
        return saved["start_code"]

    return start_code


# =========================
# SPONSORS
# =========================

def get_sponsors():

    ok, data, status = supabase_request(
        "GET",
        "sponsors",
        params={
            "select": "*",
            "order": "id.asc"
        }
    )

    if not ok:
        print("SPONSORS ERROR:", data)
        return []

    return data if isinstance(data, list) else []


# =========================
# PENDING
# =========================

def save_pending(user_id, episode_key):

    supabase_request(
        "POST",
        "pending",
        body={
            "user_id": user_id,
            "episode_key": episode_key
        }
    )


def get_pending(user_id):

    ok, data, status = supabase_request(
        "GET",
        "pending",
        params={
            "user_id": f"eq.{user_id}",
            "select": "*",
            "limit": "1"
        }
    )

    if ok and isinstance(data, list) and data:
        return data[0]

    return None


def delete_pending(user_id):

    supabase_request(
        "DELETE",
        "pending",
        params={
            "user_id": f"eq.{user_id}"
        }
    )


# =========================
# MEMBERSHIP
# =========================

def is_member(user_id, chat_id):

    result = telegram(
        "getChatMember",
        {
            "chat_id": chat_id,
            "user_id": user_id
        }
    )

    if not result.get("ok"):
        return False

    status = result["result"]["status"]

    return status in [
        "creator",
        "administrator",
        "member"
    ]


# =========================
# JOIN BUTTONS
# =========================

def make_join_keyboard():

    buttons = []

    buttons.append([
        {
            "text": "📢 عضویت در کانال اصلی",
            "url": CHANNEL_URL
        }
    ])

    sponsors = get_sponsors()

    for sponsor in sponsors:

        buttons.append([
            {
                "text": sponsor["title"],
                "url": sponsor["url"]
            }
        ])

    buttons.append([
        {
            "text": "عضو شدم ✅",
            "callback_data": "check_join"
        }
    ])

    return {
        "inline_keyboard": buttons
    }


def all_channels_joined(user_id):

    if not is_member(user_id, CHANNEL_ID):
        return False

    sponsors = get_sponsors()

    for sponsor in sponsors:

        if not is_member(user_id, sponsor["chat_id"]):
            return False

    return True


# =========================
# SEND EPISODE
# =========================

def send_episode_files(chat_id, episode):

    files = episode.get("files") or []

    if not files:
        send_message(
            chat_id,
            "❌ فایلی برای این قسمت پیدا نشد."
        )
        return

    sent_messages = []

    for file_info in files:

        file_id = file_info.get("file_id")
        file_type = file_info.get("file_type", "نامشخص")
        caption = file_info.get("caption", "")

        if not file_id:
            continue

        text = caption

        if not text:
            text = (
                f"🎬 {episode['series_name']}\n"
                f"🪷 قسمت : {episode['episode_number']}\n"
                f"🫧 {file_type}"
            )

        if file_info.get("type") == "video":

            result = telegram(
                "sendVideo",
                {
                    "chat_id": chat_id,
                    "video": file_id,
                    "caption": text,
                    "parse_mode": "HTML"
                }
            )

        elif file_info.get("type") == "document":

            result = telegram(
                "sendDocument",
                {
                    "chat_id": chat_id,
                    "document": file_id,
                    "caption": text,
                    "parse_mode": "HTML"
                }
            )

        else:
            continue

        if result.get("ok"):
            sent_messages.append(
                result["result"]["message_id"]
            )

    # حذف بعد از 30 ثانیه
    def delete_later():

        time.sleep(30)

        for message_id in sent_messages:
            delete_message(chat_id, message_id)

    threading.Thread(
        target=delete_later,
        daemon=True
    ).start()


# =========================
# START
# =========================

def handle_start(message):

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]

    text = message.get("text", "")

    parts = text.split(maxsplit=1)

    if len(parts) < 2:

        send_message(
            chat_id,
            "👋 سلام!\n\n"
            "برای دریافت قسمت موردنظر، لینک قسمت رو باز کن."
        )

        return

    code = parts[1].strip()

    episode = get_episode_by_code(code)

    if not episode:

        send_message(
            chat_id,
            "❌ این لینک قسمت پیدا نشد یا معتبر نیست."
        )

        return

    save_pending(
        user_id,
        episode["episode_key"]
    )

    if not all_channels_joined(user_id):

        send_message(
            chat_id,
            "🔐 برای دریافت فایل، اول در کانال‌های زیر عضو شو:\n\n"
            "بعد روی «عضو شدم ✅» بزن.",
            make_join_keyboard()
        )

        return

    show_reaction_step(chat_id)


# =========================
# STEP 2
# =========================

def show_reaction_step(chat_id):

    keyboard = {
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
                    "callback_data": "done_posts"
                }
            ]
        ]
    }

    send_message(
        chat_id,
        "❤️ برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ببین 👇\n\n"
        f"{CHANNEL_URL}\n\n"
        "بعد روی «انجام شد ✅» بزن.",
        keyboard
    )


# =========================
# CALLBACKS
# =========================

def handle_callback(callback):

    callback_id = callback["id"]
    data = callback.get("data", "")

    user_id = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]

    telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
    )

    # -------------------------
    # CHECK JOIN
    # -------------------------

    if data == "check_join":

        if not all_channels_joined(user_id):

            send_message(
                chat_id,
                "❌ هنوز در همه کانال‌ها عضو نشدی.\n"
                "عضویتت رو کامل کن و دوباره بزن."
            )

            return

        show_reaction_step(chat_id)

        return

    # -------------------------
    # DONE POSTS
    # -------------------------

    if data == "done_posts":

        pending = get_pending(user_id)

        if not pending:

            send_message(
                chat_id,
                "❌ درخواست فعالی پیدا نشد.\n"
                "لینک قسمت رو دوباره باز کن."
            )

            return

        if not all_channels_joined(user_id):

            send_message(
                chat_id,
                "❌ هنوز در کانال‌ها عضو نیستی."
            )

            return

        episode = get_episode_by_key(
            pending["episode_key"]
        )

        if not episode:

            send_message(
                chat_id,
                "❌ قسمت پیدا نشد."
            )

            return

        delete_pending(user_id)

        send_episode_files(
            chat_id,
            episode
        )


# =========================
# ADMIN UPLOAD
# =========================

def handle_admin_file(message):

    chat_id = message["chat"]["id"]

    if chat_id != ADMIN_ID:
        return

    caption = message.get("caption", "")

    series_name, episode_number, file_type = parse_caption(caption)

    if not series_name or not episode_number:

        send_message(
            chat_id,
            "❌ فرمت کپشن درست نیست.\n\n"
            "مثال:\n"
            "🪴 سریال «بالا پایین استانبول»\n"
            "🪷 قسمت : 14\n"
            "⚡ زیرنویس فوری\n"
            "🎍 کیفیت : 1080"
        )

        return

    file_id = None
    file_kind = None

    # VIDEO
    if "video" in message:

        file_id = message["video"]["file_id"]
        file_kind = "video"

    # DOCUMENT
    elif "document" in message:

        file_id = message["document"]["file_id"]
        file_kind = "document"

    if not file_id:
        return

    file_info = {
        "file_id": file_id,
        "type": file_kind,
        "file_type": file_type,
        "caption": caption
    }

    start_code = save_episode(
        series_name,
        episode_number,
        file_info
    )

    if not start_code:

        send_message(
            chat_id,
            "❌ ذخیره قسمت انجام نشد.\n\n"
            "جزئیات خطا داخل لاگ Render ثبت شده."
        )

        return

    link = (
        f"https://t.me/Seryyaltorki_bot"
        f"?start={start_code}"
    )

    send_message(
        chat_id,
        "✅ فایل با موفقیت ذخیره شد.\n\n"
        f"🎬 سریال: {series_name}\n"
        f"🪷 قسمت: {episode_number}\n"
        f"🫧 نوع: {file_type}\n\n"
        f"🔗 لینک قسمت:\n{link}"
    )


# =========================
# ADMIN COMMANDS
# =========================

def handle_admin_command(message):

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if chat_id != ADMIN_ID:
        return False

    # -------------------------
    # TEST DB
    # -------------------------

    if text == "/testdb":

        ok, data, status = supabase_request(
            "GET",
            "episodes",
            params={
                "select": "id",
                "limit": "1"
            }
        )

        if ok:

            send_message(
                chat_id,
                f"✅ اتصال ربات به Supabase برقرار است.\n\n"
                f"نتیجه: {data}"
            )

        else:

            send_message(
                chat_id,
                f"❌ خطای Supabase:\n\n{data}"
            )

        return True

    # -------------------------
    # SPONSORS
    # -------------------------

    if text == "/sponsors":

        sponsors = get_sponsors()

        if not sponsors:

            send_message(
                chat_id,
                "📭 هیچ اسپانسری ثبت نشده."
            )

            return True

        result = "📢 لیست اسپانسرها:\n\n"

        for sponsor in sponsors:

            result += (
                f"🆔 {sponsor['id']}\n"
                f"📛 {sponsor['title']}\n"
                f"🔗 {sponsor['url']}\n\n"
            )

        send_message(chat_id, result)

        return True

    # -------------------------
    # ADD SPONSOR
    # -------------------------

    if text.startswith("/add_sponsor"):

        content = text[len("/add_sponsor"):].strip()

        parts = [
            x.strip()
            for x in content.split("|")
        ]

        if len(parts) != 3:

            send_message(
                chat_id,
                "❌ فرمت درست:\n\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"
            )

            return True

        channel_username = parts[0]
        title = parts[1]
        url = parts[2]

        ok, data, status = supabase_request(
            "POST",
            "sponsors",
            body={
                "chat_id": channel_username,
                "title": title,
                "url": url
            }
        )

        if ok:

            send_message(
                chat_id,
                "✅ اسپانسر با موفقیت ذخیره شد."
            )

        else:

            send_message(
                chat_id,
                f"❌ ذخیره اسپانسر انجام نشد.\n\n"
                f"خطا:\n{data}"
            )

        return True

    # -------------------------
    # REMOVE SPONSOR
    # -------------------------

    if text.startswith("/remove_sponsor"):

        parts = text.split()

        if len(parts) != 2:

            send_message(
                chat_id,
                "❌ فرمت:\n/remove_sponsor ID"
            )

            return True

        sponsor_id = parts[1]

        ok, data, status = supabase_request(
            "DELETE",
            "sponsors",
            params={
                "id": f"eq.{sponsor_id}"
            }
        )

        if ok:

            send_message(
                chat_id,
                "✅ اسپانسر حذف شد."
            )

        else:

            send_message(
                chat_id,
                f"❌ حذف نشد:\n{data}"
            )

        return True

    # -------------------------
    # DELETE EPISODE
    # -------------------------

    if text.startswith("/delete_episode"):

        content = text[len("/delete_episode"):].strip()

        parts = [
            x.strip()
            for x in content.split("|")
        ]

        if len(parts) != 2:

            send_message(
                chat_id,
                "❌ فرمت درست:\n\n"
                "/delete_episode اسم سریال | شماره قسمت"
            )

            return True

        series_name = parts[0]
        episode_number = parts[1]

        episode_key = f"{series_name}__{episode_number}"

        ok, data, status = supabase_request(
            "DELETE",
            "episodes",
            params={
                "episode_key": f"eq.{episode_key}"
            }
        )

        if ok:

            send_message(
                chat_id,
                "✅ قسمت حذف شد."
            )

        else:

            send_message(
                chat_id,
                f"❌ حذف قسمت انجام نشد:\n{data}"
            )

        return True

    # -------------------------
    # DELETE ALL
    # -------------------------

    if text == "/delete_all":

        ok, data, status = supabase_request(
            "DELETE",
            "episodes",
            params={
                "id": "not.is.null"
            }
        )

        if ok:

            send_message(
                chat_id,
                "✅ تمام قسمت‌ها حذف شدند."
            )

        else:

            send_message(
                chat_id,
                f"❌ حذف انجام نشد:\n{data}"
            )

        return True

    return False


# =========================
# UPDATE HANDLER
# =========================

def process_update(update):

    print("UPDATE RECEIVED:", json.dumps(
        update,
        ensure_ascii=False
    )[:3000])

    # CALLBACK
    if "callback_query" in update:

        handle_callback(
            update["callback_query"]
        )

        return

    # MESSAGE
    if "message" not in update:
        return

    message = update["message"]

    # ADMIN COMMAND
    if "text" in message:

        if handle_admin_command(message):
            return

        if message["text"].startswith("/start"):

            handle_start(message)

            return

    # ADMIN FILE
    if (
        message["chat"]["id"] == ADMIN_ID
        and (
            "video" in message
            or "document" in message
        )
    ):

        handle_admin_file(message)


# =========================
# WEBHOOK
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        update = request.get_json(
            force=True,
            silent=True
        )

        if update:
            process_update(update)

        return "OK", 200

    except Exception as e:

        print("WEBHOOK ERROR:", repr(e))

        return "OK", 200


# =========================
# HOME
# =========================

@app.route("/", methods=["GET"])
def home():

    return "Bot is running.", 200


# =========================
# SET WEBHOOK
# =========================

def setup_webhook():

    result = telegram(
        "setWebhook",
        {
            "url": WEBHOOK_URL,
                        "allowed_updates": [
                "message",
                "channel_post",
                "callback_query"
            ],
            "max_connections": 40
        }
    )

    print("WEBHOOK SET:", result)


# =========================
# START SERVER
# =========================

if __name__ == "__main__":

    print("BOT STARTING...")

    setup_webhook()

    port = int(
        os.environ.get("PORT", 10000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
