import os
import re
import time
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

app = Flask(__name__)

TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ==================================================
# TELEGRAM
# ==================================================

def tg(method, data=None):

    try:

        r = requests.post(
            f"{TG_URL}/{method}",
            json=data or {},
            timeout=30
        )

        return r.json()

    except Exception as e:

        print("Telegram Error:", e)

        return {}


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


# ==================================================
# SUPABASE
# ==================================================

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}


def sb_request(method, table, data=None, params=None):

    try:

        url = f"{SUPABASE_URL}/rest/v1/{table}"

        r = requests.request(
            method,
            url,
            headers=SB_HEADERS,
            json=data,
            params=params,
            timeout=30
        )

        if r.status_code >= 400:

            print(
                "SUPABASE ERROR:",
                r.status_code,
                r.text
            )

            return None

        if not r.text:
            return True

        return r.json()

    except Exception as e:

        print(
            "Supabase Exception:",
            e
        )

        return None


# ==================================================
# NORMALIZE TEXT
# ==================================================

def normalize_text(text):

    if not text:
        return ""

    text = str(text)

    replacements = {
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "‌": " "
    }

    for old, new in replacements.items():

        text = text.replace(
            old,
            new
        )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ==================================================
# PARSE EPISODE CAPTION
# ==================================================

def parse_episode_caption(caption):

    if not caption:
        return None

    original = caption

    text = normalize_text(
        caption
    )

    # ----------------------------------------------
    # SERIES NAME
    # ----------------------------------------------

    series_name = None

    patterns = [

        r"سریال\s*[:：]\s*[«\"“](.*?)[»\"”]",

        r"سریال\s*[:：]\s*(.+?)(?:\n|قسمت)",

        r"سریال\s*[«\"“](.*?)[»\"”]",

        r"سریال\s+(.+?)(?:\n|قسمت)"

    ]

    for pattern in patterns:

        m = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if m:

            series_name = m.group(1).strip()

            break

    if not series_name:

        return None


    # ----------------------------------------------
    # EPISODE NUMBER
    # ----------------------------------------------

    episode_number = None

    episode_patterns = [

        r"قسمت\s*[:：\-]?\s*(\d+)",

        r"episode\s*[:：\-]?\s*(\d+)",

        r"اپیزود\s*[:：\-]?\s*(\d+)"

    ]

    for pattern in episode_patterns:

        m = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if m:

            episode_number = int(
                m.group(1)
            )

            break

    if episode_number is None:

        return None


    # ----------------------------------------------
    # FILE TYPE
    # ----------------------------------------------

    file_type = "نامشخص"

    if "زیرنویس مووی باز" in text:

        file_type = "زیرنویس مووی باز"

    elif "زیرنویس فوری" in text:

        file_type = "زیرنویس فوری"

    elif "زبان اصلی" in text:

        file_type = "زبان اصلی"


    return {

        "series_name": series_name,

        "episode_number": episode_number,

        "file_type": file_type,

        "original_caption": original

    }


# ==================================================
# EPISODE KEY
# ==================================================

def make_episode_key(
    series_name,
    episode_number
):

    safe_name = normalize_text(
        series_name
    ).lower()

    safe_name = re.sub(
        r"\s+",
        " ",
        safe_name
    ).strip()

    return (
        f"{safe_name}__{episode_number}"
    )


# ==================================================
# SAVE EPISODE
# ==================================================

def save_episode(
    series_name,
    episode_number,
    file_data
):

    episode_key = make_episode_key(
        series_name,
        episode_number
    )

    existing = sb_request(
        "GET",
        "episodes",
        params={
            "episode_key":
                f"eq.{episode_key}",

            "select": "*"
        }
    )

    if existing is None:

        return False, None


    # ----------------------------------------------
    # EXISTING EPISODE
    # ----------------------------------------------

    if len(existing) > 0:

        episode = existing[0]

        files = episode.get(
            "files"
        ) or []

        files.append(
            file_data
        )

        result = sb_request(
            "PATCH",
            "episodes",
            data={
                "files": files,

                "series_name":
                    series_name,

                "episode_number":
                    episode_number
            },
            params={
                "episode_key":
                    f"eq.{episode_key}"
            }
        )

        if result is None:

            return False, None

        return True, episode_key


    # ----------------------------------------------
    # NEW EPISODE
    # ----------------------------------------------

    result = sb_request(
        "POST",
        "episodes",
        data={
            "episode_key":
                episode_key,

            "series_name":
                series_name,

            "episode_number":
                episode_number,

            "files":
                [file_data]
        }
    )

    if result is None:

        return False, None

    return True, episode_key


# ==================================================
# GET EPISODE
# ==================================================

def get_episode(episode_key):

    result = sb_request(
        "GET",
        "episodes",
        params={
            "episode_key":
                f"eq.{episode_key}",

            "select": "*"
        }
    )

    if not result:

        return None

    return result[0]


# ==================================================
# PENDING
# ==================================================

def set_pending(
    user_id,
    episode_key
):

    sb_request(
        "POST",
        "pending",
        data={
            "user_id":
                user_id,

            "episode_key":
                episode_key
        }
    )


def get_pending(user_id):

    result = sb_request(
        "GET",
        "pending",
        params={
            "user_id":
                f"eq.{user_id}",

            "select": "*"
        }
    )

    if not result:

        return None

    return result[0]


def delete_pending(user_id):

    sb_request(
        "DELETE",
        "pending",
        params={
            "user_id":
                f"eq.{user_id}"
        }
    )


# ==================================================
# SPONSORS
# ==================================================

def get_sponsors():

    result = sb_request(
        "GET",
        "sponsors",
        params={
            "select": "*",
            "order": "id.asc"
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
        data={
            "chat_id":
                chat_id,

            "title":
                title,

            "url":
                url
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
# DELETE ONE EPISODE
# ==================================================

def delete_episode(
    series_name,
    episode_number
):

    episode_key = make_episode_key(
        series_name,
        episode_number
    )

    result = sb_request(
        "DELETE",
        "episodes",
        params={
            "episode_key":
                f"eq.{episode_key}"
        }
    )

    return result is not None


# ==================================================
# DELETE ALL EPISODES
# ==================================================

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
# MEMBERSHIP
# ==================================================

def is_member(
    chat_id,
    user_id
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

    if not result.get("ok"):

        return False

    status = result["result"]["status"]

    return status in [

        "creator",

        "administrator",

        "member"

    ]


def check_all_channels(user_id):

    # ----------------------------------------------
    # MAIN CHANNEL
    # ----------------------------------------------

    if not is_member(
        CHANNEL_ID,
        user_id
    ):

        return False, "main"


    # ----------------------------------------------
    # SPONSORS
    # ----------------------------------------------

    sponsors = get_sponsors()

    for sponsor in sponsors:

        chat_id = sponsor["chat_id"]

        if not is_member(
            chat_id,
            user_id
        ):

            return False, sponsor

    return True, None


# ==================================================
# BUTTONS
# ==================================================

def join_button():

    return {

        "inline_keyboard": [

            [

                {
                    "text":
                        "📢 عضویت در کانال اصلی",

                    "url":
                        CHANNEL_URL
                }

            ],

            [

                {
                    "text":
                        "عضو شدم ✅",

                    "callback_data":
                        "check_join"
                }

            ]

        ]

    }


def sponsor_buttons():

    sponsors = get_sponsors()

    buttons = []

    for sponsor in sponsors:

        buttons.append(

            [

                {

                    "text":
                        f"📢 {sponsor['title']}",

                    "url":
                        sponsor["url"]

                }

            ]

        )

    buttons.append(

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
            buttons

    }


def done_button():

    return {

        "inline_keyboard": [

            [

                {

                    "text":
                        "انجام شد ✅",

                    "callback_data":
                        "check_done"

                }

            ]

        ]

    }


# ==================================================
# SHOW JOIN
# ==================================================

def show_join_step(chat_id):

    sponsors = get_sponsors()

    if sponsors:

        text = (
            "برای دریافت فایل باید اول "
            "در کانال‌های زیر عضو بشی 👇\n\n"
        )

        for i, sponsor in enumerate(
            sponsors,
            1
        ):

            text += (
                f"{i}. "
                f"{sponsor['title']}\n"
            )

        text += (
            "\nبعد از عضویت روی "
            "«عضو شدم ✅» بزن."
        )

        send_message(
            chat_id,
            text,
            sponsor_buttons()
        )

    else:

        send_message(
            chat_id,
            "برای دریافت فایل اول "
            "عضو کانال اصلی شو 👇",
            join_button()
        )


# ==================================================
# SECOND STEP
# ==================================================

def show_second_step(chat_id):

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
        done_button()
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
            "❌ فایلی برای این قسمت پیدا نشد."
        )

        return


    sent_messages = []


    for file_data in files:

        file_id = file_data.get(
            "file_id"
        )

        file_type = file_data.get(
            "file_type",
            "نامشخص"
        )

        caption = file_data.get(
            "caption",
            ""
        )


        # ------------------------------------------
        # VIDEO
        # ------------------------------------------

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


        # ------------------------------------------
        # DOCUMENT
        # ------------------------------------------

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


        if result.get("ok"):

            sent_messages.append(

                result["result"][
                    "message_id"
                ]

            )


    # ------------------------------------------
    # DELETE AFTER 30 SEC
    # ------------------------------------------

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
# CONTINUE AFTER DONE
# ==================================================

def continue_after_done(
    chat_id,
    user_id
):

    pending = get_pending(
        user_id
    )

    if not pending:

        send_message(
            chat_id,
            "❌ لینک منقضی شده. "
            "دوباره لینک قسمت رو باز کن."
        )

        return


    # ------------------------------------------
    # WAIT A LITTLE
    # ------------------------------------------

    created_at = pending.get(
        "created_at"
    )

    if created_at:

        try:

            from datetime import datetime

            created = datetime.fromisoformat(

                created_at.replace(
                    "Z",
                    "+00:00"
                )

            )

            now = datetime.now(
                created.tzinfo
            )

            elapsed = (
                now - created
            ).total_seconds()

            if elapsed < 2:

                time.sleep(
                    2 - elapsed
                )

        except Exception:

            pass


    episode_key = pending[
        "episode_key"
    ]

    episode = get_episode(
        episode_key
    )

    if not episode:

        delete_pending(
            user_id
        )

        send_message(
            chat_id,
            "❌ این قسمت پیدا نشد."
        )

        return


    send_episode(
        chat_id,
        episode
    )

    delete_pending(
        user_id
    )


# ==================================================
# CALLBACK HANDLER
# ==================================================

def handle_callback(query):

    callback_id = query["id"]

    tg(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_id
        }
    )

    user = query["from"]

    user_id = user["id"]

    message = query.get(
        "message"
    )

    if not message:

        return

    chat_id = message[
        "chat"
    ]["id"]

    data = query.get(
        "data"
    )


    # ------------------------------------------
    # CHECK JOIN
    # ------------------------------------------

    if data == "check_join":

        ok, failed = check_all_channels(
            user_id
        )

        if not ok:

            if failed == "main":

                send_message(
                    chat_id,

                    "❌ هنوز در کانال اصلی "
                    "عضو نشدی.\n"
                    "اول عضو شو و دوباره "
                    "«عضو شدم ✅» رو بزن.",

                    join_button()
                )

            else:

                send_message(
                    chat_id,

                    f"❌ هنوز در کانال "
                    f"«{failed['title']}» "
                    f"عضو نشدی.",

                    sponsor_buttons()
                )

            return


        # حذف پیام قبلی

        delete_message(
            chat_id,
            message["message_id"]
        )


        pending = get_pending(
            user_id
        )

        if not pending:

            send_message(
                chat_id,
                "❌ لینک قسمت پیدا نشد.\n"
                "دوباره لینک قسمت رو باز کن."
            )

            return


        show_second_step(
            chat_id
        )

        return


    # ------------------------------------------
    # DONE
    # ------------------------------------------

    if data == "check_done":

        delete_message(
            chat_id,
            message["message_id"]
        )

        continue_after_done(
            chat_id,
            user_id
        )

        return


# ==================================================
# START
# ==================================================

def handle_start(message):

    chat_id = message[
        "chat"
    ]["id"]

    user_id = message[
        "from"
    ]["id"]

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
            "سلام 👋\n\n"
            "برای دریافت قسمت، "
            "لینک قسمت رو باز کن."
        )

        return


    start_param = parts[1].strip()


    if not start_param.startswith(
        "ep_"
    ):

        send_message(
            chat_id,
            "❌ لینک قسمت نامعتبره."
        )

        return


    episode_key = start_param[
        3:
    ]

    episode = get_episode(
        episode_key
    )

    if not episode:

        send_message(
            chat_id,
            "❌ این قسمت پیدا نشد "
            "یا حذف شده."
        )

        return


    # ذخیره pending

    set_pending(
        user_id,
        episode_key
    )


    # بررسی عضویت

    ok, failed = check_all_channels(
        user_id
    )

    if not ok:

        if failed == "main":

            send_message(
                chat_id,
                "برای دریافت این قسمت "
                "اول عضو کانال اصلی شو 👇",
                join_button()
            )

        else:

            show_join_step(
                chat_id
            )

        return


    show_second_step(
        chat_id
    )


# ==================================================
# ADMIN MESSAGE
# ==================================================

def handle_admin_message(message):

    chat_id = message[
        "chat"
    ]["id"]

    user_id = message[
        "from"
    ]["id"]


    if user_id != ADMIN_ID:

        return False


    text = message.get(
        "text",
        ""
    )


    # ==================================================
    # DELETE ONE EPISODE
    # ==================================================

    if text.startswith(
        "/delete_episode"
    ):

        command = text.replace(
            "/delete_episode",
            "",
            1
        ).strip()

        parts = [

            x.strip()

            for x in command.split("|")

        ]


        if len(parts) != 2:

            send_message(
                chat_id,

                "فرمت درست:\n\n"

                "/delete_episode "
                "اسم سریال | شماره قسمت\n\n"

                "مثال:\n"

                "/delete_episode "
                "بالا پایین استانبول | 14"
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
                "❌ شماره قسمت نامعتبره."
            )

            return True


        success = delete_episode(
            series_name,
            episode_number
        )


        if success:

            send_message(
                chat_id,

                f"✅ قسمت "
                f"{episode_number} "
                f"سریال «{series_name}» "
                f"حذف شد."
            )

        else:

            send_message(
                chat_id,
                "❌ حذف قسمت انجام نشد."
            )


        return True


    # ==================================================
    # DELETE ALL
    # ==================================================

    if text == "/delete_all":

        success = delete_all_episodes()


        if success:

            send_message(
                chat_id,

                "🗑 تمام قسمت‌های "
                "ذخیره‌شده با موفقیت حذف شدند."
            )

        else:

            send_message(
                chat_id,

                "❌ حذف قسمت‌ها انجام نشد."
            )


        return True


    # ==================================================
    # ADD SPONSOR
    # ==================================================

    if text.startswith(
        "/add_sponsor"
    ):

        command = text.replace(
            "/add_sponsor",
            "",
            1
        ).strip()


        parts = [

            x.strip()

            for x in command.split("|")

        ]


        if len(parts) != 3:

            send_message(
                chat_id,

                "فرمت درست:\n\n"

                "/add_sponsor "
                "@channel | نام کانال | "
                "https://t.me/channel"
            )

            return True


        chat_username = parts[0]

        title = parts[1]

        url = parts[2]


        success = add_sponsor(
            chat_username,
            title,
            url
        )


        if success:

            send_message(
                chat_id,

                "✅ اسپانسر با موفقیت اضافه شد."
            )

        else:

            send_message(
                chat_id,

                "❌ ذخیره اسپانسر انجام نشد."
            )


        return True


    # ==================================================
    # SPONSORS
    # ==================================================

    if text == "/sponsors":

        sponsors = get_sponsors()


        if not sponsors:

            send_message(
                chat_id,

                "❌ هیچ اسپانسری ثبت نشده."
            )

            return True


        result = "📋 لیست اسپانسرها:\n\n"


        for sponsor in sponsors:

            result += (

                f"🆔 {sponsor['id']}\n"

                f"📢 {sponsor['title']}\n"

                f"🔗 {sponsor['url']}\n\n"

            )


        send_message(
            chat_id,
            result
        )

        return True


    # ==================================================
    # REMOVE SPONSOR
    # ==================================================

    if text.startswith(
        "/remove_sponsor"
    ):

        parts = text.split()


        if len(parts) != 2:

            send_message(
                chat_id,

                "فرمت:\n\n"

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
                "❌ ID نامعتبره."
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


    # ==================================================
    # UPLOAD EPISODE
    # ==================================================

    video = message.get(
        "video"
    )

    document = message.get(
        "document"
    )


    if not video and not document:

        return False


    caption = message.get(
        "caption",
        ""
    )


    parsed = parse_episode_caption(
        caption
    )


    if not parsed:

        send_message(
            chat_id,

            "❌ کپشن قابل تشخیص نیست.\n\n"

            "فرمت درست:\n\n"

            "سریال: بالا پایین استانبول\n"

            "قسمت: 14\n"

            "زیرنویس مووی باز"
        )

        return True


    # ==================================================
    # FILE DATA
    # ==================================================

    if video:

        file_id = video[
            "file_id"
        ]

        file_type = "video"

    else:

        file_id = document[
            "file_id"
        ]

        file_type = "document"


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


    success, episode_key = save_episode(

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
        f"Seryyaltorki_bot"
        f"?start=ep_{episode_key}"

    )


    send_message(

        chat_id,

        "✅ قسمت با موفقیت ذخیره شد.\n\n"

        f"🎬 سریال: "
        f"{parsed['series_name']}\n"

        f"🔢 قسمت: "
        f"{parsed['episode_number']}\n"

        f"📦 نوع فایل: "
        f"{parsed['file_type']}\n"

        f"🔗 {link}"

    )


    return True


# ==================================================
# MESSAGE HANDLER
# ==================================================

def handle_message(message):

    if message.get(
        "from",
        {}
    ).get("id") == ADMIN_ID:

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
            "❌ اتصال به Supabase "
            "مشکل دارد.",
            500
        )


    return (

        f"✅ اتصال ربات به Supabase "
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
