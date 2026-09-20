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

BOT_USERNAME = "Seryyaltorki_bot"

DELETE_AFTER = 30

app = Flask(__name__)

TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ==================================================
# TELEGRAM
# ==================================================

def tg(method, data=None):

    try:

        response = requests.post(
            f"{TG_URL}/{method}",
            data=data or {},
            timeout=20
        )

        result = response.json()

        print(
            "TELEGRAM:",
            method,
            result
        )

        return result

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            e
        )

        return None


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
            "chat_id":
                chat_id,

            "message_id":
                message_id
        }
    )


def answer_callback(
    callback_id
):

    return tg(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_id
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

            "apikey":
                SUPABASE_KEY,

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

        response = requests.request(

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
            response.status_code,
            response.text[:500]
        )

        if response.status_code >= 400:

            return None

        if not response.text:

            return True

        try:

            return response.json()

        except:

            return True

    except Exception as e:

        print(
            "SUPABASE ERROR:",
            e
        )

        return None


# ==================================================
# NORMALIZE
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

                "select":
                    "id",

                "start_code":
                    f"eq.{code}",

                "limit":
                    "1"

            }

        )

        if result == []:

            return code


# ==================================================
# PARSE CAPTION
# ==================================================

def parse_episode_caption(
    caption
):

    caption = caption or ""

    normalized = normalize_text(
        caption
    )

    # ----------------------------------------------
    # SERIES
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

            series_name = (
                match.group(1)
                .strip()
            )

            break

    if not series_name:

        for line in normalized.splitlines():

            line = line.strip()

            if line.startswith(
                "سریال"
            ):

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
    # EPISODE
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
# GET EPISODE BY CODE
# ==================================================

def get_episode_by_code(
    start_code
):

    print(
        "SEARCH START CODE:",
        repr(start_code)
    )

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

    print(
        "EPISODE RESULT:",
        result
    )

    if not result:

        return None

    return result[0]


# ==================================================
# GET EPISODE
# ==================================================

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
    # EXISTING
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
    # NEW
    # ----------------------------------------------

    start_code = generate_start_code()

    episode_key = (

        f"{normalize_text(series_name)}"
        f"__"
        f"{episode_number}"

    )

    result = sb_request(

        "POST",

        "episodes",

        json_data={

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

        },

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

    for sponsor in get_sponsors():

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

    for sponsor in get_sponsors():

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
# SHOW JOIN
# ==================================================

def show_join(
    chat_id
):

    send_message(

        chat_id,

        "برای دریافت فایل، "
        "اول در کانال‌های زیر عضو شو 👇\n\n"
        "بعد روی «عضو شدم ✅» بزن.",

        join_keyboard()

    )


# ==================================================
# SHOW SECOND STEP
# ==================================================

def show_second_step(
    chat_id
):

    send_message(

        chat_id,

        "برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ببین 👇\n\n"
        f"{CHANNEL_URL}\n\n"
        "بعد از چند ثانیه روی "
        "«انجام شد ✅» بزن.",

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

            sent_messages.append(

                result["result"][
                    "message_id"
                ]

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
# PENDING
# ==================================================

def save_pending(
    user_id,
    start_code
):

    # حذف قبلی

    sb_request(

        "DELETE",

        "pending",

        params={

            "user_id":
                f"eq.{user_id}"

        }

    )

    result = sb_request(

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

    return result is not None


def get_pending(
    user_id
):

    result = sb_request(

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

    if not result:

        return None

    return result[0]


def delete_pending(
    user_id
):

    return sb_request(

        "DELETE",

        "pending",

        params={

            "user_id":
                f"eq.{user_id}"

        }

    )


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
    ).strip()

    print(
        "START RECEIVED:",
        repr(text)
    )

    # ----------------------------------------------
    # GET CODE
    # ----------------------------------------------

    match = re.match(

        r"^/start(?:@\w+)?\s+(.+)$",

        text,

        re.IGNORECASE

    )

    if not match:

        send_message(

            chat_id,

            "سلام 👋\n"
            "لینک قسمت موردنظر رو باز کن."

        )

        return

    start_code = match.group(
        1
    ).strip()

    print(
        "START CODE:",
        repr(start_code)
    )

    # فقط انگلیسی، عدد، _ و -

    if not re.fullmatch(

        r"[A-Za-z0-9_-]{1,64}",

        start_code

    ):

        send_message(

            chat_id,

            "❌ لینک قسمت نامعتبره."

        )

        return

    # ----------------------------------------------
    # FIND EPISODE
    # ----------------------------------------------

    episode = get_episode_by_code(
        start_code
    )

    if not episode:

        send_message(

            chat_id,

            "❌ این قسمت پیدا نشد یا لینک اشتباهه."

        )

        return

    print(
        "FOUND EPISODE:",
        episode.get(
            "series_name"
        ),
        episode.get(
            "episode_number"
        )
    )

    # ----------------------------------------------
    # SAVE PENDING
    # ----------------------------------------------

    save_pending(

        user_id,

        start_code

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

        pending = get_pending(
            user_id
        )

        if not pending:

            send_message(

                chat_id,

                "❌ درخواست قبلی پیدا نشد.\n"
                "دوباره لینک قسمت رو باز کن."

            )

            return

        start_code = pending.get(
            "episode_key"
        )

        episode = get_episode_by_code(
            start_code
        )

        if not episode:

            send_message(

                chat_id,

                "❌ قسمت موردنظر پیدا نشد."

            )

            return

        send_message(

            chat_id,

            "⏳ در حال آماده‌سازی فایل..."

        )

        send_episode(

            chat_id,

            episode

        )

        delete_pending(
            user_id
        )

        return


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

    telegram_type = None

    # ----------------------------------------------
    # VIDEO
    # ----------------------------------------------

    if message.get(
        "video"
    ):

        video = message[
            "video"
        ]

        file_id = video.get(
            "file_id"
        )

        telegram_type = "video"

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

        document = message[
            "document"
        ]

        file_id = document.get(
            "file_id"
        )

        telegram_type = "document"

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
            telegram_type,

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
# DELETE EPISODE
# ==================================================

def delete_episode(
    series_name,
    episode_number
):

    episode = get_episode(

        series_name,

        episode_number

    )

    if not episode:

        return False

    result = sb_request(

        "DELETE",

        "episodes",

        params={

            "id":
                f"eq.{episode['id']}"

        }

    )

    return result is not None


def delete_all_episodes():

    result = sb_request(

        "DELETE",

        "episodes",

        params={

            "id":
                "not.is.null"

        }

    )

    return result is not None


# ==================================================
# ADMIN
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

        success = add_sponsor(

            parts[0],

            parts[1],

            parts[2]

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
            "📋 لیست اسپانسرها:"
        ]

        for sponsor in sponsors:

            lines.append(

                f"\n🆔 {sponsor['id']}"
                f"\n📢 {sponsor['title']}"
                f"\n🔗 {sponsor['url']}"

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

        if remove_sponsor(
            sponsor_id
        ):

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

        if delete_episode(

            parts[0],

            episode_number

        ):

            send_message(

                chat_id,

                "✅ قسمت حذف شد."

            )

        else:

            send_message(

                chat_id,

                "❌ قسمت پیدا نشد یا حذف نشد."

            )

        return True

    # ----------------------------------------------
    # DELETE ALL
    # ----------------------------------------------

    if text == "/delete_all":

        if delete_all_episodes():

            send_message(

                chat_id,

                "✅ همه قسمت‌ها حذف شدند."

            )

        else:

            send_message(

                chat_id,

                "❌ حذف همه قسمت‌ها انجام نشد."

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

    # ADMIN

    if user_id == ADMIN_ID:

        handled = handle_admin_message(
            message
        )

        if handled:

            return

    # START

    text = message.get(
        "text",
        ""
    ).strip()

    if text.startswith(
        "/start"
    ):

        print(
            "START RECEIVED:",
            repr(text)
        )

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

        print(
            "UPDATE:",
            update
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
