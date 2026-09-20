import os
import re
import time
import random
import string
import threading
import requests

from flask import Flask, request


# ==================================================
# CONFIG
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

DELETE_AFTER = 30

BOT_USERNAME = "Seryyaltorki_bot"

app = Flask(__name__)

TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ==================================================
# TELEGRAM
# ==================================================

def tg(method, data=None):

    try:

        r = requests.post(
            f"{TG_URL}/{method}",
            data=data or {},
            timeout=20
        )

        return r.json()

    except Exception as e:

        print("TG ERROR:", e)

        return None


def send_message(chat_id, text, reply_markup=None):

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


def delete_message(chat_id, message_id):

    return tg(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


def answer_callback(callback_id):

    return tg(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
    )


# ==================================================
# SUPABASE
# ==================================================

def sb_request(
    method,
    table,
    params=None,
    json_data=None,
    headers_extra=None
):

    try:

        headers = {

            "apikey": SUPABASE_KEY,

            "Authorization":
                f"Bearer {SUPABASE_KEY}",

            "Content-Type":
                "application/json"

        }

        if headers_extra:

            headers.update(
                headers_extra
            )

        url = (
            f"{SUPABASE_URL.rstrip('/')}"
            f"/rest/v1/{table}"
        )

        r = requests.request(

            method,

            url,

            headers=headers,

            params=params,

            json=json_data,

            timeout=20

        )

        print(
            "SUPABASE:",
            method,
            table,
            r.status_code,
            r.text[:500]
        )

        if r.status_code >= 400:

            return None

        if not r.text:

            return True

        try:

            return r.json()

        except:

            return True

    except Exception as e:

        print(
            "SUPABASE ERROR:",
            e
        )

        return None


# ==================================================
# TEXT
# ==================================================

def normalize_text(text):

    if not text:

        return ""

    text = text.replace(
        "ي",
        "ی"
    )

    text = text.replace(
        "ك",
        "ک"
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ==================================================
# RANDOM START CODE
# ==================================================

def generate_start_code():

    chars = (
        string.ascii_lowercase +
        string.digits
    )

    while True:

        code = "".join(
            random.choices(
                chars,
                k=6
            )
        )

        result = sb_request(

            "GET",

            "episodes",

            params={
                "select": "id",
                "start_code":
                    f"eq.{code}",
                "limit": "1"
            }

        )

        if result == []:

            return code


# ==================================================
# PARSE CAPTION
# ==================================================

def parse_episode_caption(caption):

    caption = caption or ""

    normalized = normalize_text(
        caption
    )

    # ----------------------------------------------
    # SERIES NAME
    # ----------------------------------------------

    series_name = None

    patterns = [

        r"سریال\s*[:：]?\s*[«\"“](.*?)[»\"”]",

        r"سریال\s*[:：]\s*(.+)",

        r"سریال\s+(.+)"

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            normalized,
            re.IGNORECASE
        )

        if match:

            series_name = match.group(1).strip()

            break

    if not series_name:

        lines = normalized.splitlines()

        for line in lines:

            line = line.strip()

            if line.startswith("سریال"):

                series_name = re.sub(
                    r"^سریال\s*[:：]?\s*",
                    "",
                    line
                ).strip()

                series_name = (
                    series_name
                    .strip("«»\"“”")
                    .strip()
                )

                break

    # ----------------------------------------------
    # EPISODE NUMBER
    # ----------------------------------------------

    episode_number = None

    patterns = [

        r"قسمت\s*[:：]?\s*(\d+)",

        r"episode\s*[:：]?\s*(\d+)",

        r"اپیزود\s*[:：]?\s*(\d+)"

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            normalized,
            re.IGNORECASE
        )

        if match:

            episode_number = int(
                match.group(1)
            )

            break

    # ----------------------------------------------
    # FILE TYPE
    # ----------------------------------------------

    file_type = "نامشخص"

    if "زیرنویس مووی باز" in normalized:

        file_type = "زیرنویس مووی باز"

    elif "زیرنویس فوری" in normalized:

        file_type = "زیرنویس فوری"

    elif "زبان اصلی" in normalized:

        file_type = "زبان اصلی"

    return {

        "series_name":
            series_name,

        "episode_number":
            episode_number,

        "file_type":
            file_type

    }


# ==================================================
# EPISODE
# ==================================================

def get_episode_by_code(
    start_code
):

    result = sb_request(

        "GET",

        "episodes",

        params={

            "select":
                "*",

            "start_code":
                f"eq.{start_code}",

            "limit":
                "1"

        }

    )

    if not result:

        return None

    return result[0]


def get_episode(
    series_name,
    episode_number
):

    result = sb_request(

        "GET",

        "episodes",

        params={

            "select":
                "*",

            "series_name":
                f"eq.{series_name}",

            "episode_number":
                f"eq.{episode_number}",

            "limit":
                "1"

        }

    )

    if not result:

        return None

    return result[0]


# ==================================================
# SAVE EPISODE
# ==================================================

def save_episode(
    series_name,
    episode_number,
    file_data
):

    existing = get_episode(
        series_name,
        episode_number
    )

    # ----------------------------------------------
    # EXISTING EPISODE
    # ----------------------------------------------

    if existing:

        files = existing.get(
            "files"
        ) or []

        files.append(
            file_data
        )

        result = sb_request(

            "PATCH",

            "episodes",

            params={

                "id":
                    f"eq.{existing['id']}"

            },

            json_data={

                "files":
                    files

            },

            headers_extra={

                "Prefer":
                    "return=representation"

            }

        )

        if result is None:

            return False, None

        return (
            True,
            existing.get(
                "start_code"
            )
        )

    # ----------------------------------------------
    # NEW EPISODE
    # ----------------------------------------------

    start_code = generate_start_code()

    episode_key = (
        f"{normalize_text(series_name)}"
        f"__"
        f"{episode_number}"
    )

    data = {

        "episode_key":
            episode_key,

        "series_name":
            series_name,

        "episode_number":
            episode_number,

        "start_code":
            start_code,

        "files":
            [file_data]

    }

    result = sb_request(

        "POST",

        "episodes",

        json_data=data,

        headers_extra={

            "Prefer":
                "return=representation"

        }

    )

    if result is None:

        return False, None

    return True, start_code


# ==================================================
# SPONSORS
# ==================================================

def get_sponsors():

    result = sb_request(

        "GET",

        "sponsors",

        params={

            "select":
                "*",

            "order":
                "id.asc"

        }

    )

    return result or []


def add_sponsor(
    chat_id,
    title,
    url
):

    result = sb_request(

        "POST",

        "sponsors",

        json_data={

            "chat_id":
                chat_id,

            "title":
                title,

            "url":
                url

        },

        headers_extra={

            "Prefer":
                "return=representation"

        }

    )

    return result is not None


def remove_sponsor(
    sponsor_id
):

    result = sb_request(

        "DELETE",

        "sponsors",

        params={

            "id":
                f"eq.{sponsor_id}"

        }

    )

    return result is not None


# ==================================================
# MEMBERSHIP
# ==================================================

def is_member(
    user_id,
    chat_id
):

    result = tg(

        "getChatMember",

        {

            "chat_id":
                chat_id,

            "user_id":
                user_id

        }

    )

    if not result:

        return False

    if not result.get(
        "ok"
    ):

        return False

    status = (
        result
        .get("result", {})
        .get("status")
    )

    return status in [

        "creator",

        "administrator",

        "member"

    ]


def check_all_memberships(
    user_id
):

    if not is_member(
        user_id,
        CHANNEL_ID
    ):

        return False

    sponsors = get_sponsors()

    for sponsor in sponsors:

        if not is_member(
            user_id,
            sponsor["chat_id"]
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

            "text":
                "عضویت در کانال اصلی 📢",

            "url":
                CHANNEL_URL

        }

    ])

    sponsors = get_sponsors()

    for sponsor in sponsors:

        rows.append([

            {

                "text":
                    f"عضویت در {sponsor['title']}",

                "url":
                    sponsor["url"]

            }

        ])

    rows.append([

        {

            "text":
                "عضو شدم ✅",

            "callback_data":
                "check_join"

        }

    ])

    return {

        "inline_keyboard":
            rows

    }


def done_keyboard():

    return {

        "inline_keyboard": [

            [

                {

                    "text":
                        "انجام شد ✅",

                    "callback_data":
                        "done"

                }

            ]

        ]

    }


# ==================================================
# STEP 1
# ==================================================

def show_join(
    chat_id
):

    text = (

        "برای دریافت فایل، "
        "اول در کانال‌های زیر عضو شو 👇\n\n"

        "بعد روی «عضو شدم ✅» بزن."

    )

    send_message(

        chat_id,

        text,

        join_keyboard()

    )


# ==================================================
# STEP 2
# ==================================================

def show_second_step(
    chat_id
):

    text = (

        "برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ببین 👇\n\n"

        f"{CHANNEL_URL}\n\n"

        "بعد از چند ثانیه روی "
        "«انجام شد ✅» بزن."

    )

    send_message(

        chat_id,

        text,

        done_keyboard()

    )


# ==================================================
# SEND EPISODE
# ==================================================

def send_episode(
    chat_id,
    episode
):

    files = episode.get(
        "files"
    ) or []

    if not files:

        send_message(

            chat_id,

            "❌ فایل این قسمت پیدا نشد."

        )

        return

    sent_messages = []

    for file_data in files:

        file_id = file_data.get(
            "file_id"
        )

        file_type = file_data.get(
            "file_type",
            "فایل"
        )

        caption = file_data.get(
            "caption",
            ""
        )

        if file_data.get(
            "type"
        ) == "video":

            result = tg(

                "sendVideo",

                {

                    "chat_id":
                        chat_id,

                    "video":
                        file_id,

                    "caption":
                        caption

                }

            )

        else:

            result = tg(

                "sendDocument",

                {

                    "chat_id":
                        chat_id,

                    "document":
                        file_id,

                    "caption":
                        caption

                }

            )

        if result and result.get(
            "ok"
        ):

            message_id = (
                result["result"]["message_id"]
            )

            sent_messages.append(
                message_id
            )

    def delete_later():

        time.sleep(
            DELETE_AFTER
        )

        for message_id in sent_messages:

            delete_message(
                chat_id,
                message_id
            )

    threading.Thread(
        target=delete_later,
        daemon=True
    ).start()


# ==================================================
# CALLBACK
# ==================================================

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
        "message"
    ) or {}

    chat = message.get(
        "chat"
    ) or {}

    chat_id = chat.get(
        "id"
    )

    user = callback.get(
        "from"
    ) or {}

    user_id = user.get(
        "id"
    )

    answer_callback(
        callback_id
    )

    # ----------------------------------------------
    # CHECK JOIN
    # ----------------------------------------------

    if data == "check_join":

        if not check_all_memberships(
            user_id
        ):

            send_message(

                chat_id,

                "❌ هنوز در همه کانال‌ها عضو نشدی."

            )

            return

        show_second_step(
            chat_id
        )

        return

    # ----------------------------------------------
    # DONE
    # ----------------------------------------------

    if data == "done":

        send_message(

            chat_id,

            "⏳ در حال آماده‌سازی فایل..."

        )

        start_code = callback.get(
            "message",
            {}
        ).get(
            "text",
            ""
        )

        # pending data handled below

        pending = sb_request(

            "GET",

            "pending",

            params={

                "select":
                    "*",

                "user_id":
                    f"eq.{user_id}",

                "limit":
                    "1"

            }

        )

        if not pending:

            send_message(

                chat_id,

                "❌ درخواست قبلی پیدا نشد. "
                "دوباره لینک قسمت را باز کن."

            )

            return

        episode_key = pending[0].get(
            "episode_key"
        )

        episode = get_episode_by_code(
            episode_key
        )

        if not episode:

            # compatibility with old pending records

            episode = get_episode(
                "",
                0
            )

        if not episode:

            send_message(

                chat_id,

                "❌ قسمت موردنظر پیدا نشد."

            )

            return

        send_episode(
            chat_id,
            episode
        )

        sb_request(

            "DELETE",

            "pending",

            params={

                "user_id":
                    f"eq.{user_id}"

            }

        )

        return


# ==================================================
# START
# ==================================================

def handle_start(
    message
):

    chat = message.get(
        "chat"
    ) or {}

    chat_id = chat.get(
        "id"
    )

    user = message.get(
        "from"
    ) or {}

    user_id = user.get(
        "id"
    )

    text = message.get(
        "text",
        ""
    )

    parts = text.split(
        maxsplit=1
    )

    if len(parts) < 2:

        send_message(

            chat_id,

            "سلام 👋\n"
            "لینک قسمت موردنظر رو باز کن."

        )

        return

    start_code = parts[1].strip()

    if start_code.startswith(
        "ep_"
    ):

        start_code = start_code[3:]

    # فقط حروف انگلیسی و عدد

    if not re.fullmatch(
        r"[A-Za-z0-9_-]{1,64}",
        start_code
    ):

        send_message(

            chat_id,

            "❌ لینک قسمت نامعتبره."

        )

        return

    episode = get_episode_by_code(
        start_code
    )

    if not episode:

        send_message(

            chat_id,

            "❌ این قسمت پیدا نشد یا لینک آن اشتباه است."

        )

        return

    # ----------------------------------------------
    # SAVE PENDING
    # ----------------------------------------------

    sb_request(

        "DELETE",

        "pending",

        params={

            "user_id":
                f"eq.{user_id}"

        }

    )

    sb_request(

        "POST",

        "pending",

        json_data={

            "user_id":
                user_id,

            "episode_key":
                start_code

        },

        headers_extra={

            "Prefer":
                "return=minimal"

        }

    )

    # ----------------------------------------------
    # MEMBERSHIP
    # ----------------------------------------------

    if not check_all_memberships(
        user_id
    ):

        show_join(
            chat_id
        )

        return

    show_second_step(
        chat_id
    )


# ==================================================
# ADMIN UPLOAD
# ==================================================

def handle_admin_upload(
    message
):

    chat = message.get(
        "chat"
    ) or {}

    chat_id = chat.get(
        "id"
    )

    caption = ""

    file_id = None

    file_type = None

    # ----------------------------------------------
    # VIDEO
    # ----------------------------------------------

    if message.get(
        "video"
    ):

        video = message["video"]

        file_id = video.get(
            "file_id"
        )

        file_type = "video"

        caption = (
            message.get(
                "caption"
            )
            or ""
        )

    # ----------------------------------------------
    # DOCUMENT
    # ----------------------------------------------

    elif message.get(
        "document"
    ):

        document = message["document"]

        file_id = document.get(
            "file_id"
        )

        file_type = "document"

        caption = (
            message.get(
                "caption"
            )
            or ""
        )

    else:

        return False

    parsed = parse_episode_caption(
        caption
    )

    if not parsed.get(
        "series_name"
    ):

        send_message(

            chat_id,

            "❌ اسم سریال داخل کپشن پیدا نشد."

        )

        return True

    if not parsed.get(
        "episode_number"
    ):

        send_message(

            chat_id,

            "❌ شماره قسمت داخل کپشن پیدا نشد."

        )

        return True

    file_data = {

        "file_id":
            file_id,

        "type":
            file_type,

        "file_type":
            parsed["file_type"],

        "caption":
            caption

    }

    success, start_code = save_episode(

        parsed["series_name"],

        parsed["episode_number"],

        file_data

    )

    if not success:

        send_message(

            chat_id,

            "❌ ذخیره قسمت انجام نشد."

        )

        return True

    link = (

        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start={start_code}"

    )

    send_message(

        chat_id,

        "✅ قسمت با موفقیت ذخیره شد.\n\n"

        f"🎬 سریال: "
        f"{parsed['series_name']}\n"

        f"🔢 قسمت: "
        f"{parsed['episode_number']}\n"

        f"📦 نوع فایل: "
        f"{parsed['file_type']}\n\n"

        f"🔗 لینک دریافت:\n"
        f"{link}"

    )

    return True


# ==================================================
# ADMIN COMMANDS
# ==================================================

def handle_admin_message(
    message
):

    chat = message.get(
        "chat"
    ) or {}

    chat_id = chat.get(
        "id"
    )

    text = message.get(
        "text",
        ""
    ).strip()

    # ----------------------------------------------
    # ADD SPONSOR
    # ----------------------------------------------

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

                "فرمت درست:\n\n"
                "/add_sponsor @channel | نام کانال | https://t.me/channel"

            )

            return True

        sponsor_chat = parts[0]

        title = parts[1]

        url = parts[2]

        success = add_sponsor(

            sponsor_chat,

            title,

            url

        )

        if success:

            send_message(

                chat_id,

                "✅ اسپانسر با موفقیت ذخیره شد."

            )

        else:

            send_message(

                chat_id,

                "❌ ذخیره اسپانسر انجام نشد."

            )

        return True

    # ----------------------------------------------
    # SPONSORS
    # ----------------------------------------------

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

                f"🆔 {sponsor['id']}\n"
                f"📢 {sponsor['title']}\n"
                f"🔗 {sponsor['url']}\n"

            )

        send_message(

            chat_id,

            "\n".join(lines)

        )

        return True

    # ----------------------------------------------
    # REMOVE SPONSOR
    # ----------------------------------------------

    if text.startswith(
        "/remove_sponsor"
    ):

        sponsor_id = text[
            len("/remove_sponsor"):
        ].strip()

        if not sponsor_id.isdigit():

            send_message(

                chat_id,

                "❌ آیدی اسپانسر نامعتبره."

            )

            return True

        success = remove_sponsor(
            sponsor_id
        )

        if success:

            send_message(

                chat_id,

                "✅ اسپانسر حذف شد."

            )

        else:

            send_message(

                chat_id,

                "❌ حذف اسپانسر انجام نشد."

            )

        return True

    # ----------------------------------------------
    # DELETE EPISODE
    # ----------------------------------------------

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

                "فرمت درست:\n\n"
                "/delete_episode اسم سریال | شماره قسمت"

            )

            return True

        series_name = parts[0]

        try:

            episode_number = int(
                parts[1]
            )

        except:

            send_message(

                chat_id,

                "❌ شماره قسمت اشتباهه."

            )

            return True

        episode = get_episode(

            series_name,

            episode_number

        )

        if not episode:

            send_message(

                chat_id,

                "❌ این قسمت پیدا نشد."

            )

            return True

        result = sb_request(

            "DELETE",

            "episodes",

            params={

                "id":
                    f"eq.{episode['id']}"

            }

        )

        if result is None:

            send_message(

                chat_id,

                "❌ حذف قسمت انجام نشد."

            )

        else:

            send_message(

                chat_id,

                "✅ قسمت حذف شد."

            )

        return True

    # ----------------------------------------------
    # DELETE ALL
    # ----------------------------------------------

    if text == "/delete_all":

        result = sb_request(

            "DELETE",

            "episodes",

            params={

                "id":
                    "not.is.null"

            }

        )

        if result is None:

            send_message(

                chat_id,

                "❌ حذف همه قسمت‌ها انجام نشد."

            )

        else:

            send_message(

                chat_id,

                "✅ همه قسمت‌ها حذف شدند."

            )

        return True

    # ----------------------------------------------
    # UPLOAD
    # ----------------------------------------------

    if message.get(
        "video"
    ) or message.get(
        "document"
    ):

        return handle_admin_upload(
            message
        )

    return False


# ==================================================
# MESSAGE HANDLER
# ==================================================

def handle_message(
    message
):

    user = message.get(
        "from"
    ) or {}

    user_id = user.get(
        "id"
    )

    if user_id == ADMIN_ID:

        handled = handle_admin_message(
            message
        )

        if handled:

            return

    text = message.get(
        "text",
        ""
    )

    if text.startswith(
        "/start"
    ):

        handle_start(
            message
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
            force=True
        )

        if "message" in update:

            handle_message(
                update["message"]
            )

        elif "callback_query" in update:

            handle_callback(
                update["callback_query"]
            )

        return "OK", 200

    except Exception as e:

        print(
            "WEBHOOK ERROR:",
            e
        )

        return "OK", 200


# ==================================================
# TEST DATABASE
# ==================================================

@app.route(
    "/testdb"
)
def testdb():

    result = sb_request(

        "GET",

        "episodes",

        params={

            "select":
                "id",

            "limit":
                "1"

        }

    )

    if result is None:

        return (

            "❌ اتصال ربات به Supabase "
            "مشکل دارد.",

            500

        )

    return (

        "✅ اتصال ربات به Supabase "
        f"برقرار است. نتیجه: {result}"

    )


# ==================================================
# HOME
# ==================================================

@app.route("/")
def home():

    return "Bot is running."


# ==================================================
# RUN
# ==================================================

if __name__ == "__main__":

    port = int(

        os.getenv(
            "PORT",
            10000
        )

    )

    app.run(

        host="0.0.0.0",

   port=port

    )
