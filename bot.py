import os
import time
import random
import string
import threading
import requests

from flask import Flask, request, jsonify


# ==================================================
# CONFIG
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

BOT_USERNAME = "Seryyaltorki_bot"

DELETE_AFTER = 30

RENDER_URL = "https://telegram-aeries-bot.onrender.com"
WEBHOOK_URL = f"{RENDER_URL}/webhook"

app = Flask(__name__)


# ==================================================
# LOG
# ==================================================

def log(*args):
    print(*args, flush=True)


# ==================================================
# TELEGRAM
# ==================================================

def telegram(method, data=None):

    if not BOT_TOKEN:
        log("❌ BOT_TOKEN پیدا نشد")
        return {"ok": False}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        r = requests.post(
            url,
            json=data or {},
            timeout=30
        )

        result = r.json()

        log("TELEGRAM", method, result)

        return result

    except Exception as e:
        log("❌ TELEGRAM ERROR:", repr(e))
        return {"ok": False}


def send_message(chat_id, text, keyboard=None):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:
        data["reply_markup"] = keyboard

    return telegram("sendMessage", data)


def delete_message(chat_id, message_id):

    return telegram(
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

    return telegram(
        "answerCallbackQuery",
        data
    )


# ==================================================
# SUPABASE
# ==================================================

def supabase(method, table, params=None, body=None):

    if not SUPABASE_URL or not SUPABASE_KEY:
        log("❌ SUPABASE ENV ناقص")
        return []

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

        log(
            "SUPABASE",
            method,
            table,
            r.status_code,
            flush=True
        )

        if not r.text:
            return []

        try:
            return r.json()
        except:
            return []

    except Exception as e:

        log("❌ SUPABASE ERROR:", repr(e))

        return []


# ==================================================
# WEBHOOK SETUP
# ==================================================

def setup_webhook():

    log("🔧 Setting webhook...")

    result = telegram(
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

    log("WEBHOOK RESULT:", result)

    info = telegram("getWebhookInfo")

    log("WEBHOOK INFO:", info)


# ==================================================
# START CODE
# ==================================================

def generate_code():

    chars = string.ascii_lowercase + string.digits

    for _ in range(100):

        code = "".join(
            random.choice(chars)
            for _ in range(6)
        )

        result = supabase(
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


# ==================================================
# PARSE CAPTION
# ==================================================

def parse_caption(caption):

    if not caption:
        return None, None, "فایل"

    caption = (
        caption
        .replace("ي", "ی")
        .replace("ك", "ک")
    )

    series = None
    episode = None
    file_type = "فایل"

    for line in caption.splitlines():

        line = line.strip()

        if "سریال" in line:

            if "«" in line and "»" in line:

                try:
                    series = line.split("«", 1)[1].split("»", 1)[0].strip()
                except:
                    pass

            elif ":" in line:

                series = line.split(":", 1)[1].strip()

        if "قسمت" in line:

            numbers = "".join(
                c for c in line
                if c.isdigit()
            )

            if numbers:
                episode = int(numbers)

        if "زبان اصلی" in line:
            file_type = "زبان اصلی"

        elif "زیرنویس مووی باز" in line:
            file_type = "زیرنویس مووی باز"

        elif "زیرنویس فوری" in line:
            file_type = "زیرنویس فوری"

    return series, episode, file_type


# ==================================================
# EPISODES
# ==================================================

def get_episode_by_code(code):

    log("🔎 SEARCH CODE:", repr(code))

    result = supabase(
        "GET",
        "episodes",
        {
            "select": "*",
            "start_code": f"eq.{code}",
            "limit": "1"
        }
    )

    log("🔎 RESULT:", result)

    if result:
        return result[0]

    return None


def get_episode(series, episode):

    result = supabase(
        "GET",
        "episodes",
        {
            "select": "*",
            "series_name": f"eq.{series}",
            "episode_number": f"eq.{episode}",
            "limit": "1"
        }
    )

    if result:
        return result[0]

    return None


def save_episode(series, episode, file_info):

    existing = get_episode(
        series,
        episode
    )

    if existing:

        files = existing.get("files") or []

        files.append(file_info)

        supabase(
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

    code = generate_code()

    body = {
        "episode_key": f"{series}__{episode}",
        "series_name": series,
        "episode_number": episode,
        "start_code": code,
        "files": [
            file_info
        ]
    }

    result = supabase(
        "POST",
        "episodes",
        body=body
    )

    if result:
        return code

    return None


# ==================================================
# SPONSORS
# ==================================================

def get_sponsors():

    return supabase(
        "GET",
        "sponsors",
        {
            "select": "*",
            "order": "id.asc"
        }
    )


def add_sponsor(chat_id, title, url):

    result = supabase(
        "POST",
        "sponsors",
        body={
            "chat_id": chat_id,
            "title": title,
            "url": url
        }
    )

    return bool(result)


def remove_sponsor(sponsor_id):

    supabase(
        "DELETE",
        "sponsors",
        {
            "id": f"eq.{sponsor_id}"
        }
    )


# ==================================================
# PENDING
# ==================================================

def save_pending(user_id, code):

    supabase(
        "DELETE",
        "pending",
        {
            "user_id": f"eq.{user_id}"
        }
    )

    supabase(
        "POST",
        "pending",
        body={
            "user_id": user_id,
            "episode_key": code
        }
    )


def get_pending(user_id):

    result = supabase(
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

    supabase(
        "DELETE",
        "pending",
        {
            "user_id": f"eq.{user_id}"
        }
    )


# ==================================================
# MEMBERSHIP
# ==================================================

def is_member(chat_id, user_id):

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


def check_memberships(user_id):

    # کانال اصلی
    if not is_member(
        CHANNEL_ID,
        user_id
    ):
        return False

    # اسپانسرها
    sponsors = get_sponsors()

    for sponsor in sponsors:

        chat_id = sponsor.get("chat_id")

        if not chat_id:
            continue

        if not is_member(
            chat_id,
            user_id
        ):
            return False

    return True


# ==================================================
# KEYBOARDS
# ==================================================

def join_keyboard():

    rows = []

    rows.append([
        {
            "text": "📢 کانال اصلی",
            "url": CHANNEL_URL
        }
    ])

    sponsors = get_sponsors()

    for sponsor in sponsors:

        url = sponsor.get("url")
        title = sponsor.get(
            "title",
            "کانال اسپانسر"
        )

        if url:

            rows.append([
                {
                    "text": f"📢 {title}",
                    "url": url
                }
            ])

    rows.append([
        {
            "text": "عضو شدم ✅",
            "callback_data": "check_join"
        }
    ])

    return {
        "inline_keyboard": rows
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


# ==================================================
# JOIN MESSAGE
# ==================================================

def show_join(chat_id):

    text = """🔐 برای دریافت قسمت موردنظر ابتدا در کانال‌های زیر عضو شو.

بعد از عضویت روی «عضو شدم ✅» بزن."""

    send_message(
        chat_id,
        text,
        join_keyboard()
    )


# ==================================================
# LAST 5 POSTS MESSAGE
# ==================================================

def show_done(chat_id):

    text = f"""❤️ برای دریافت فایل موردنظرت، ۵ پست آخر این کانال رو ری‌اکشن (❤️) بزن 👇

{CHANNEL_URL}

بعد از انجامش روی «انجام شد ✅» بزن."""

    send_message(
        chat_id,
        text,
        done_keyboard()
    )


# ==================================================
# SEND FILES
# ==================================================

def send_episode(chat_id, episode):

    files = episode.get("files") or []

    log(
        "📦 SENDING FILES:",
        len(files)
    )

    sent = []

    for item in files:

        file_id = item.get("file_id")

        if not file_id:
            continue

        caption = item.get("caption", "")

        file_type = item.get(
            "type",
            "video"
        )

        if file_type == "video":

            result = telegram(
                "sendVideo",
                {
                    "chat_id": chat_id,
                    "video": file_id,
                    "caption": caption
                }
            )

        else:

            result = telegram(
                "sendDocument",
                {
                    "chat_id": chat_id,
                    "document": file_id,
                    "caption": caption
                }
            )

        if result.get("ok"):

            sent.append(
                result["result"]["message_id"]
            )

    def remove_later():

        time.sleep(DELETE_AFTER)

        for message_id in sent:

            delete_message(
                chat_id,
                message_id
            )

    if sent:

        threading.Thread(
            target=remove_later,
            daemon=True
        ).start()


# ==================================================
# /START
# ==================================================

def handle_start(message):

    chat_id = message["chat"]["id"]

    user_id = message.get(
        "from",
        {}
    ).get("id")

    text = message.get(
        "text",
        ""
    )

    log("🚀 START:", repr(text))

    parts = text.split(
        maxsplit=1
    )

    if len(parts) == 1:

        send_message(
            chat_id,
            "سلام 👋\n\nلینک قسمت موردنظر رو باز کن."
        )

        return

    code = parts[1].strip()

    log("🔑 CODE:", repr(code))

    episode = get_episode_by_code(code)

    if not episode:

        send_message(
            chat_id,
            "❌ این لینک قسمت پیدا نشد."
        )

        return

    save_pending(
        user_id,
        code
    )

    if not check_memberships(user_id):

        show_join(chat_id)

        return

    show_done(chat_id)


# ==================================================
# CALLBACK
# ==================================================

def handle_callback(callback):

    callback_id = callback["id"]

    data = callback.get(
        "data",
        ""
    )

    message = callback.get(
        "message",
        {}
    )

    chat_id = message.get(
        "chat",
        {}
    ).get("id")

    user_id = callback.get(
        "from",
        {}
    ).get("id")

    log(
        "🔘 CALLBACK:",
        data,
        user_id
    )

    # --------------------------
    # CHECK JOIN
    # --------------------------

    if data == "check_join":

        if not check_memberships(
            user_id
        ):

            answer_callback(
                callback_id,
                "❌ هنوز عضو همه کانال‌ها نشدی."
            )

            return

        answer_callback(
            callback_id,
            "✅ عضویت تأیید شد."
        )

        old_message_id = message.get(
            "message_id"
        )

        if old_message_id:

            delete_message(
                chat_id,
                old_message_id
            )

        show_done(chat_id)

        return

    # --------------------------
    # DONE
    # --------------------------

    if data == "done":

        pending = get_pending(
            user_id
        )

        if not pending:

            answer_callback(
                callback_id,
                "❌ درخواست پیدا نشد. دوباره لینک قسمت رو باز کن."
            )

            return

        code = pending.get(
            "episode_key"
        )

        episode = get_episode_by_code(
            code
        )

        if not episode:

            answer_callback(
                callback_id,
                "❌ قسمت پیدا نشد."
            )

            return

        answer_callback(
            callback_id,
            "✅ فایل در حال ارسال است..."
        )

        delete_pending(
            user_id
        )

        old_message_id = message.get(
            "message_id"
        )

        if old_message_id:

            delete_message(
                chat_id,
                old_message_id
            )

        send_episode(
            chat_id,
            episode
        )


# ==================================================
# ADMIN FILE
# ==================================================

def handle_admin_file(message):

    user_id = message.get(
        "from",
        {}
    ).get("id")

    if user_id != ADMIN_ID:
        return

    caption = message.get(
        "caption",
        ""
    )

    series, episode, file_type = parse_caption(
        caption
    )

    if not series or episode is None:

        send_message(
            message["chat"]["id"],
            """❌ کپشن درست نیست.

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

    if not file_id:

        return

    file_info = {
        "type": file_kind,
        "file_id": file_id,
        "file_type": file_type,
        "caption": caption
    }

    code = save_episode(
        series,
        episode,
        file_info
    )

    if not code:

        send_message(
            message["chat"]["id"],
            "❌ ذخیره قسمت انجام نشد."
        )

        return

    link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start={code}"
    )

    send_message(
        message["chat"]["id"],
        f"""✅ فایل ذخیره شد.

🎬 سریال: {series}
🪷 قسمت: {episode}
📁 نوع: {file_type}

🔗 لینک دریافت:

{link}"""
    )


# ==================================================
# ADMIN COMMANDS
# ==================================================

def admin_command(message):

    user_id = message.get(
        "from",
        {}
    ).get("id")

    if user_id != ADMIN_ID:
        return

    text = message.get(
        "text",
        ""
    )

    chat_id = message["chat"]["id"]

    # TEST DB
    if text == "/testdb":

        result = supabase(
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

    # SPONSORS
    if text == "/sponsors":

        sponsors = get_sponsors()

        if not sponsors:

            send_message(
                chat_id,
                "📭 هیچ اسپانسری ثبت نشده."
            )

            return

        lines = [
            "📋 لیست اسپانسرها:"
        ]

        for s in sponsors:

            lines.append(
                f"\n🆔 {s.get('id')}"
                f"\n📢 {s.get('title')}"
                f"\n🔗 {s.get('url')}"
            )

        send_message(
            chat_id,
            "\n".join(lines)
        )

        return

    # ADD SPONSOR
    if text.startswith(
        "/add_sponsor"
    ):

        content = text[
            len("/add_sponsor"):
        ].strip()

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

        ok = add_sponsor(
            parts[0],
            parts[1],
            parts[2]
        )

        if ok:

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

    # REMOVE SPONSOR
    if text.startswith(
        "/remove_sponsor"
    ):

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

        remove_sponsor(
            sponsor_id
        )

        send_message(
            chat_id,
            "✅ اسپانسر حذف شد."
        )

        return

    # DELETE EPISODE
    if text.startswith(
        "/delete_episode"
    ):

        content = text[
            len("/delete_episode"):
        ].strip()

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

        series = parts[0]

        try:
            episode = int(parts[1])
        except:

            send_message(
                chat_id,
                "❌ شماره قسمت اشتباه است."
            )

            return

        supabase(
            "DELETE",
            "episodes",
            {
                "series_name": f"eq.{series}",
                "episode_number": f"eq.{episode}"
            }
        )

        send_message(
            chat_id,
            "✅ قسمت حذف شد."
        )

        return

    # DELETE ALL
    if text == "/delete_all":

        supabase(
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


# ==================================================
# WEBHOOK
# ==================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            silent=True
        )

        log(
            "📩 WEBHOOK RECEIVED:",
            bool(update)
        )

        if not update:

            log(
                "⚠️ EMPTY UPDATE"
            )

            return jsonify({
                "ok": True
            })

        # MESSAGE
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

            log(
                "📨 MESSAGE:",
                user_id,
                repr(text)
            )

            # ADMIN FILE
            if (
                user_id == ADMIN_ID
                and (
                    "video" in message
                    or "document" in message
                )
            ):

                handle_admin_file(
                    message
                )

            # ADMIN COMMAND
            elif (
                user_id == ADMIN_ID
                and text.startswith("/")
            ):

                admin_command(
                    message
                )

            # START
            elif text.startswith(
                "/start"
            ):

                handle_start(
                    message
                )

        # CALLBACK
        elif "callback_query" in update:

            handle_callback(
                update["callback_query"]
            )

        else:

            log(
                "ℹ️ OTHER UPDATE TYPE:",
                list(update.keys())
            )

        return jsonify({
            "ok": True
        })

    except Exception as e:

        log(
            "🔥 WEBHOOK ERROR:",
            repr(e)
        )

        return jsonify({
            "ok": True
        })


# ==================================================
# HOME
# ==================================================

@app.route(
    "/",
    methods=["GET", "HEAD"]
)
def home():

    return "Bot is running.", 200


# ==================================================
# START
# ==================================================

if __name__ == "__main__":

    log("==============================")
    log("🚀 BOT STARTING...")
    log("ADMIN:", ADMIN_ID)
    log("SUPABASE:", SUPABASE_URL)
    log("WEBHOOK:", WEBHOOK_URL)
    log("==============================")

    # تنظیم خودکار Webhook
    if BOT_TOKEN:

        setup_webhook()

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
