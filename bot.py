import os
import json
import random
import string
import time
import threading
import requests

from flask import Flask, request, jsonify

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

BOT_USERNAME = "Seryyaltorki_bot"

DELETE_AFTER = 30

app = Flask(__name__)


# =========================
# TELEGRAM API
# =========================

def tg(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        r = requests.post(url, json=data or {}, timeout=30)
        result = r.json()

        print("TG:", method, result)

        return result

    except Exception as e:
        print("TG ERROR:", method, repr(e))
        return {"ok": False, "error": str(e)}


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return tg("sendMessage", data)


def delete_message(chat_id, message_id):
    return tg("deleteMessage", {
        "chat_id": chat_id,
        "message_id": message_id
    })


def answer_callback(callback_id, text=None):
    data = {
        "callback_query_id": callback_id
    }

    if text:
        data["text"] = text

    return tg("answerCallbackQuery", data)


# =========================
# SUPABASE
# =========================

def supabase_request(method, table, params=None, json_data=None):
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
            json=json_data,
            timeout=30
        )

        print("SUPABASE:", method, table, r.status_code, r.text)

        if not r.text:
            return []

        try:
            return r.json()
        except:
            return []

    except Exception as e:
        print("SUPABASE ERROR:", repr(e))
        return []


# =========================
# RANDOM START CODE
# =========================

def generate_start_code():
    chars = string.ascii_lowercase + string.digits

    for _ in range(50):
        code = "".join(random.choice(chars) for _ in range(6))

        result = supabase_request(
            "GET",
            "episodes",
            {
                "select": "id",
                "start_code": f"eq.{code}",
                "limit": "1"
            }
        )

        if not result:
            return code

    return str(int(time.time()))[-6:]


# =========================
# TEXT HELPERS
# =========================

def normalize_text(text):
    if not text:
        return ""

    return (
        text.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", " ")
        .strip()
    )


# =========================
# PARSE EPISODE
# =========================

def parse_caption(caption):
    caption = normalize_text(caption)

    series_name = None
    episode_number = None
    file_type = "فایل"

    lines = caption.splitlines()

    for line in lines:

        # سریال
        if "سریال" in line:
            if "«" in line and "»" in line:
                try:
                    series_name = line.split("«", 1)[1].split("»", 1)[0].strip()
                except:
                    pass

            elif ":" in line:
                series_name = line.split(":", 1)[1].strip()

        # قسمت
        if "قسمت" in line:
            digits = ""

            for ch in line:
                if ch.isdigit():
                    digits += ch

            if digits:
                episode_number = int(digits)

        # نوع فایل
        if "زبان اصلی" in line:
            file_type = "زبان اصلی"

        elif "زیرنویس مووی باز" in line:
            file_type = "زیرنویس مووی باز"

        elif "زیرنویس فوری" in line:
            file_type = "زیرنویس فوری"

    return series_name, episode_number, file_type


# =========================
# EPISODE DB
# =========================

def get_episode_by_code(start_code):
    print("SEARCH START CODE:", repr(start_code))

    result = supabase_request(
        "GET",
        "episodes",
        {
            "select": "*",
            "start_code": f"eq.{start_code}",
            "limit": "1"
        }
    )

    print("EPISODE RESULT:", result)

    if result:
        print(
            "FOUND EPISODE:",
            result[0].get("series_name"),
            result[0].get("episode_number")
        )
        return result[0]

    return None


def get_episode(series_name, episode_number):
    result = supabase_request(
        "GET",
        "episodes",
        {
            "select": "*",
            "series_name": f"eq.{series_name}",
            "episode_number": f"eq.{episode_number}",
            "limit": "1"
        }
    )

    if result:
        return result[0]

    return None


def save_episode(series_name, episode_number, file_info):

    existing = get_episode(series_name, episode_number)

    if existing:

        files = existing.get("files") or []

        files.append(file_info)

        supabase_request(
            "PATCH",
            "episodes",
            {
                "id": f"eq.{existing['id']}"
            },
            {
                "files": files
            }
        )

        return existing.get("start_code")

    start_code = generate_start_code()

    episode_key = f"{series_name}__{episode_number}"

    data = {
        "episode_key": episode_key,
        "series_name": series_name,
        "episode_number": episode_number,
        "start_code": start_code,
        "files": [file_info]
    }

    result = supabase_request(
        "POST",
        "episodes",
        json_data=data
    )

    if result:
        return start_code

    return None


# =========================
# PENDING
# =========================

def save_pending(user_id, start_code):

    supabase_request(
        "DELETE",
        "pending",
        {
            "user_id": f"eq.{user_id}"
        }
    )

    supabase_request(
        "POST",
        "pending",
        json_data={
            "user_id": user_id,
            "episode_key": start_code
        }
    )


def get_pending(user_id):

    result = supabase_request(
        "GET",
        "pending",
        {
            "select": "*",
            "user_id": f"eq.{user_id}",
            "limit": "1"
        }
    )

    if result:
        return result[0]

    return None


def delete_pending(user_id):

    supabase_request(
        "DELETE",
        "pending",
        {
            "user_id": f"eq.{user_id}"
        }
    )


# =========================
# SPONSORS
# =========================

def get_sponsors():

    return supabase_request(
        "GET",
        "sponsors",
        {
            "select": "*",
            "order": "id.asc"
        }
    )


def add_sponsor(chat_id, title, url):

    result = supabase_request(
        "POST",
        "sponsors",
        json_data={
            "chat_id": chat_id,
            "title": title,
            "url": url
        }
    )

    return bool(result)


def remove_sponsor(sponsor_id):

    result = supabase_request(
        "DELETE",
        "sponsors",
        {
            "id": f"eq.{sponsor_id}"
        }
    )

    return True


# =========================
# MEMBERSHIP
# =========================

def is_member(chat_id, user_id):

    result = tg(
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


def check_all_memberships(user_id):

    # کانال اصلی
    if not is_member(CHANNEL_ID, user_id):
        return False

    # اسپانسرها
    sponsors = get_sponsors()

    for sponsor in sponsors:

        chat_id = sponsor.get("chat_id")

        if not chat_id:
            continue

        if not is_member(chat_id, user_id):
            return False

    return True


# =========================
# JOIN KEYBOARD
# =========================

def join_keyboard():

    buttons = []

    # کانال اصلی
    buttons.append([
        {
            "text": "📢 عضویت در کانال اصلی",
            "url": CHANNEL_URL
        }
    ])

    # اسپانسرها
    sponsors = get_sponsors()

    for sponsor in sponsors:

        title = sponsor.get("title", "عضویت در کانال")
        url = sponsor.get("url")

        if url:
            buttons.append([
                {
                    "text": f"📢 {title}",
                    "url": url
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


def done_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "انجام شد ✅",
                    "callback_data": "done"
                }
            ]
        ]
    }


# =========================
# SHOW JOIN
# =========================

def show_join(chat_id):

    text = """🔐 برای دریافت قسمت موردنظر ابتدا در کانال‌های زیر عضو شو.

بعد از عضویت روی «عضو شدم ✅» بزن."""

    return send_message(
        chat_id,
        text,
        join_keyboard()
    )


# =========================
# SHOW LAST 5 POSTS
# =========================

def show_last_posts(chat_id):

    text = f"""❤️ برای دریافت فایل موردنظرت، ۵ پست آخر این کانال رو ری‌اکشن (❤️) بزن 👇

{CHANNEL_URL}

بعد از انجامش روی «انجام شد ✅» بزن."""

    return send_message(
        chat_id,
        text,
        done_keyboard()
    )


# =========================
# SEND EPISODE
# =========================

def send_episode(chat_id, episode):

    files = episode.get("files") or []

    if not files:
        send_message(
            chat_id,
            "❌ فایل این قسمت پیدا نشد."
        )
        return

    sent_messages = []

    for file_info in files:

        file_id = file_info.get("file_id")
        file_type = file_info.get("file_type", "فایل")
        caption = file_info.get("caption", "")

        if not file_id:
            continue

        text = caption

        if not text:
            text = f"🎬 {file_type}"

        result = None

        if file_info.get("type") == "video":

            result = tg(
                "sendVideo",
                {
                    "chat_id": chat_id,
                    "video": file_id,
                    "caption": text
                }
            )

        elif file_info.get("type") == "document":

            result = tg(
                "sendDocument",
                {
                    "chat_id": chat_id,
                    "document": file_id,
                    "caption": text
                }
            )

        if result and result.get("ok"):

            sent_messages.append(
                result["result"]["message_id"]
            )

    # حذف بعد از 30 ثانیه
    def delete_later():

        time.sleep(DELETE_AFTER)

        for message_id in sent_messages:
            delete_message(chat_id, message_id)

    if sent_messages:
        threading.Thread(
            target=delete_later,
            daemon=True
        ).start()


# =========================
# START HANDLER
# =========================

def handle_start(message):

    chat = message.get("chat", {})
    user = message.get("from", {})

    chat_id = chat.get("id")
    user_id = user.get("id")

    text = message.get("text", "")

    print("START RECEIVED:", repr(text))

    parts = text.split(maxsplit=1)

    if len(parts) < 2:

        send_message(
            chat_id,
            "سلام 👋\n\nبرای دریافت قسمت، لینک قسمت رو باز کن."
        )

        return

    start_code = parts[1].strip()

    print("START CODE:", repr(start_code))

    episode = get_episode_by_code(start_code)

    if not episode:

        send_message(
            chat_id,
            "❌ این لینک قسمت پیدا نشد یا منقضی شده."
        )

        return

    save_pending(user_id, start_code)

    if not check_all_memberships(user_id):

        show_join(chat_id)
        return

    show_last_posts(chat_id)


# =========================
# CALLBACK HANDLER
# =========================

def handle_callback(callback):

    callback_id = callback.get("id")
    data = callback.get("data")

    message = callback.get("message", {})
    chat = message.get("chat", {})
    user = callback.get("from", {})

    chat_id = chat.get("id")
    user_id = user.get("id")

    print("CALLBACK:", data, user_id)

    if data == "check_join":

        if not check_all_memberships(user_id):

            answer_callback(
                callback_id,
                "❌ هنوز عضو همه کانال‌ها نشدی."
            )

            return

        answer_callback(
            callback_id,
            "✅ عضویت تأیید شد."
        )

        # حذف پیام عضویت
        message_id = message.get("message_id")

        if message_id:
            delete_message(chat_id, message_id)

        show_last_posts(chat_id)

        return

    if data == "done":

        pending = get_pending(user_id)

        if not pending:

            answer_callback(
                callback_id,
                "❌ درخواست پیدا نشد. دوباره لینک قسمت رو باز کن."
            )

            return

        start_code = pending.get("episode_key")

        episode = get_episode_by_code(start_code)

        if not episode:

            answer_callback(
                callback_id,
                "❌ قسمت پیدا نشد."
            )

            return

        answer_callback(
            callback_id,
            "✅ در حال ارسال فایل..."
        )

        delete_pending(user_id)

        message_id = message.get("message_id")

        if message_id:
            delete_message(chat_id, message_id)

        send_episode(
            chat_id,
            episode
        )

        return


# =========================
# ADMIN UPLOAD
# =========================

def handle_admin_file(message):

    user_id = message.get("from", {}).get("id")

    if user_id != ADMIN_ID:
        return

    caption = message.get("caption", "")

    if not caption:
        send_message(
            message["chat"]["id"],
            "❌ کپشن فایل وجود ندارد."
        )
        return

    series_name, episode_number, file_type = parse_caption(caption)

    if not series_name or episode_number is None:

        send_message(
            message["chat"]["id"],
            """❌ فرمت کپشن اشتباهه.

مثال:

🪴 سریال «بالا پایین استانبول»
🪷 قسمت : 14
⚡ زیرنویس فوری
🎍 کیفیت : 1080"""
        )

        return

    file_id = None
    file_kind = None

    if "video" in message:

        file_id = message["video"]["file_id"]
        file_kind = "video"

    elif "document" in message:

        file_id = message["document"]["file_id"]
        file_kind = "document"

    else:

        send_message(
            message["chat"]["id"],
            "❌ فقط ویدیو یا فایل قابل قبول است."
        )

        return

    file_info = {
        "type": file_kind,
        "file_id": file_id,
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
            message["chat"]["id"],
            "❌ ذخیره قسمت انجام نشد."
        )

        return

    link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start={start_code}"
    )

    send_message(
        message["chat"]["id"],
        f"""✅ فایل ذخیره شد.

🎬 سریال: {series_name}
🪷 قسمت: {episode_number}
📁 نوع: {file_type}

🔗 لینک دریافت:

{link}"""
    )


# =========================
# ADMIN COMMANDS
# =========================

def handle_admin_command(message):

    user_id = message.get("from", {}).get("id")

    if user_id != ADMIN_ID:
        return

    text = message.get("text", "")
    chat_id = message["chat"]["id"]

    # /testdb
    if text == "/testdb":

        result = supabase_request(
            "GET",
            "episodes",
            {
                "select": "id",
                "limit": "1"
            }
        )

        send_message(
            chat_id,
            f"✅ اتصال ربات به Supabase برقرار است.\nنتیجه: {result}"
        )

        return

    # /sponsors
    if text == "/sponsors":

        sponsors = get_sponsors()

        if not sponsors:

            send_message(
                chat_id,
                "📭 هیچ اسپانسری ثبت نشده."
            )

            return

        lines = ["📋 لیست اسپانسرها:\n"]

        for s in sponsors:

            lines.append(
                f"🆔 {s.get('id')}\n"
                f"📢 {s.get('title')}\n"
                f"🔗 {s.get('url')}\n"
            )

        send_message(
            chat_id,
            "\n".join(lines)
        )

        return

    # /remove_sponsor
    if text.startswith("/remove_sponsor"):

        parts = text.split()

        if len(parts) != 2:

            send_message(
                chat_id,
                "فرمت درست:\n/remove_sponsor ID"
            )

            return

        try:
            sponsor_id = int(parts[1])
        except:

            send_message(
                chat_id,
                "❌ ID اشتباه است."
            )

            return

        remove_sponsor(sponsor_id)

        send_message(
            chat_id,
            "✅ اسپانسر حذف شد."
        )

        return

    # /add_sponsor
    if text.startswith("/add_sponsor"):

        content = text[len("/add_sponsor"):].strip()

        parts = [
            x.strip()
            for x in content.split("|")
        ]

        if len(parts) != 3:

            send_message(
                chat_id,
                """❌ فرمت درست:

/add_sponsor @channel | نام کانال | https://t.me/channel"""
            )

            return

        sponsor_chat_id = parts[0]
        title = parts[1]
        url = parts[2]

        if add_sponsor(
            sponsor_chat_id,
            title,
            url
        ):

            send_message(
                chat_id,
                "✅ اسپانسر با موفقیت ذخیره شد."
            )

        else:

            send_message(
                chat_id,
                "❌ ذخیره اسپانسر انجام نشد."
            )

        return

    # /delete_episode
    if text.startswith("/delete_episode"):

        content = text[len("/delete_episode"):].strip()

        parts = [
            x.strip()
            for x in content.split("|")
        ]

        if len(parts) != 2:

            send_message(
                chat_id,
                """فرمت درست:

/delete_episode اسم سریال | شماره قسمت"""
            )

            return

        series_name = parts[0]

        try:
            episode_number = int(parts[1])
        except:

            send_message(
                chat_id,
                "❌ شماره قسمت اشتباه است."
            )

            return

        result = supabase_request(
            "DELETE",
            "episodes",
            {
                "series_name": f"eq.{series_name}",
                "episode_number": f"eq.{episode_number}"
            }
        )

        send_message(
            chat_id,
            "✅ قسمت حذف شد."
        )

        return

    # /delete_all
    if text == "/delete_all":

        supabase_request(
            "DELETE",
            "episodes",
            {
                "id": "gt.0"
            }
        )

        send_message(
            chat_id,
            "🗑 همه قسمت‌ها حذف شدند."
        )

        return


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

        print("================================")
        print("UPDATE RECEIVED:")
        print(json.dumps(
            update,
            ensure_ascii=False
        ))
        print("================================")

        if not update:

            return jsonify({
                "ok": True
            })

        # پیام
        if "message" in update:

            message = update["message"]

            user_id = message.get(
                "from",
                {}
            ).get("id")

            text = message.get(
                "text",
                ""
            )

            print(
                "MESSAGE:",
                user_id,
                repr(text)
            )

            # فایل ادمین
            if (
                user_id == ADMIN_ID
                and (
                    "video" in message
                    or "document" in message
                )
            ):

                handle_admin_file(message)

            # دستورات ادمین
            elif (
                user_id == ADMIN_ID
                and text.startswith("/")
            ):

                handle_admin_command(message)

            # استارت
            elif text.startswith("/start"):

                handle_start(message)

        # callback
        elif "callback_query" in update:

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


# =========================
# HOME
# =========================

@app.route("/", methods=["GET", "HEAD"])
def home():

    return "Bot is running.", 200


# =========================
# RUN
# =========================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    print("BOT STARTING...")
    print("SUPABASE:", SUPABASE_URL)
    print("ADMIN:", ADMIN_ID)

    app.run(
        host="0.0.0.0",
        port=port
    )
