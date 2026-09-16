import os
import json
import re
import time
import threading
import requests
import hashlib
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 5648301086

BOT_USERNAME = "Seryyaltorki_bot"

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

EPISODES_FILE = "episodes.json"
SPONSORS_FILE = "sponsors.json"
PENDING_FILE = "pending.json"

app = Flask(__name__)


# =========================
# Telegram API
# =========================

def telegram(method, data):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            data=data,
            timeout=20
        )
        result = response.json()
        print(method, result)
        return result

    except Exception as e:
        print("Telegram error:", e)
        return {}


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)

    return telegram("sendMessage", data)


def delete_message(chat_id, message_id):
    return telegram(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


# =========================
# JSON
# =========================

def load_json(filename, default):
    if not os.path.exists(filename):
        return default

    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print("JSON load error:", filename, e)
        return default


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        print("JSON save error:", filename, e)


def load_episodes():
    return load_json(EPISODES_FILE, {})


def save_episodes(data):
    save_json(EPISODES_FILE, data)


def load_sponsors():
    return load_json(SPONSORS_FILE, [])


def save_sponsors(data):
    save_json(SPONSORS_FILE, data)


def load_pending():
    return load_json(PENDING_FILE, {})


def save_pending(data):
    save_json(PENDING_FILE, data)


# =========================
# Series Key
# =========================

def make_series_key(series_name):

    # چون اسم سریال ممکنه فارسی باشه،
    # از SHA1 یک کلید انگلیسی/عددی می‌سازیم.
    # این کلید برای Deep Link کاملاً امن است.

    clean_name = series_name.strip().lower()

    series_hash = hashlib.sha1(
        clean_name.encode("utf-8")
    ).hexdigest()[:10]

    return "s" + series_hash


def make_episode_key(series_name, episode_number):

    series_key = make_series_key(series_name)

    return (
        "ep_"
        + series_key
        + "_"
        + str(episode_number)
    )


# =========================
# Caption Parser
# =========================

def extract_episode_number(caption):

    patterns = [
        r"قسمت\s*[:：\-]?\s*(\d+)",
        r"episode\s*[:：\-]?\s*(\d+)",
        r"ep\s*[:：\-]?\s*(\d+)"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            caption,
            re.IGNORECASE
        )

        if match:
            return match.group(1)

    return None


def extract_series_name(caption):

    lines = [
        line.strip()
        for line in caption.splitlines()
        if line.strip()
    ]

    # مدل:
    # 🪴 سریال « عشق و تخت»

    for line in lines:

        match = re.search(
            r"سریال\s*[«\"]?\s*(.+?)\s*[»\"]?\s*$",
            line
        )

        if match:

            name = match.group(1).strip()

            name = name.strip("«»\"'")

            return name


    # مدل:
    # سریال: عشق و تخت

    for line in lines:

        match = re.search(
            r"سریال\s*[:：]\s*(.+)",
            line,
            re.IGNORECASE
        )

        if match:

            name = match.group(1).strip()

            name = name.strip("«»\"'")

            return name


    # مدل قدیمی:
    # عشق و تخت | قسمت 2 | 1080

    parts = [
        x.strip()
        for x in caption.split("|")
    ]

    if len(parts) >= 2 and parts[0]:

        first = parts[0]

        first = re.sub(
            r"^[^\wآ-ی]*",
            "",
            first
        )

        if first:

            return first


    return None


# =========================
# Normalize
# =========================

def normalize_episode(episode):

    if isinstance(episode, dict):
        return [episode]

    if isinstance(episode, list):
        return episode

    return []


# =========================
# Membership
# =========================

def is_member(user_id, channel_id):

    result = telegram(
        "getChatMember",
        {
            "chat_id": channel_id,
            "user_id": user_id
        }
    )

    if not result.get("ok"):
        return False

    status = result["result"]["status"]

    return status in [
        "member",
        "administrator",
        "creator"
    ]


def check_all_sponsors(user_id):

    if not is_member(
        user_id,
        CHANNEL_ID
    ):
        return False

    for sponsor in load_sponsors():

        if not is_member(
            user_id,
            sponsor["chat_id"]
        ):
            return False

    return True


def membership_keyboard():

    buttons = [
        [
            {
                "text": "📢 عضویت در کانال اصلی",
                "url": CHANNEL_URL
            }
        ]
    ]

    for sponsor in load_sponsors():

        buttons.append(
            [
                {
                    "text": "📢 " + sponsor["title"],
                    "url": sponsor["url"]
                }
            ]
        )

    buttons.append(
        [
            {
                "text": "عضو شدم ✅",
                "callback_data": "check_join"
            }
        ]
    )

    return {
        "inline_keyboard": buttons
    }


# =========================
# Reaction
# =========================

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
                    "callback_data": "reaction_done"
                }
            ]
        ]
    }


def send_reaction_message(
    chat_id,
    episode_key
):

    return send_message(
        chat_id,

        "برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ری‌اکشن ❤️ بزن 👇\n\n"
        + CHANNEL_URL,

        reaction_keyboard()
    )


# =========================
# Send Episode
# =========================

def send_episode(
    chat_id,
    episode_key
):

    episodes = load_episodes()

    if episode_key not in episodes:

        send_message(
            chat_id,
            "❌ این قسمت حذف شده یا وجود ندارد."
        )

        return


    files = normalize_episode(
        episodes[episode_key]
    )

    sent_message_ids = []


    for item in files:

        file_id = item.get("file_id")
        file_type = item.get("type")
        caption = item.get("caption", "")


        if not file_id:
            continue


        if file_type == "video":

            result = telegram(
                "sendVideo",
                {
                    "chat_id": chat_id,
                    "video": file_id,
                    "caption": caption
                }
            )


        elif file_type == "document":

            result = telegram(
                "sendDocument",
                {
                    "chat_id": chat_id,
                    "document": file_id,
                    "caption": caption
                }
            )


        else:
            continue


        if result.get("ok"):

            sent_message_ids.append(
                result["result"]["message_id"]
            )


    # اگر فایل ارسال شد، تایمر شروع شود
    if sent_message_ids:

        threading.Thread(
            target=delete_after_30_seconds,

            args=(
                chat_id,
                sent_message_ids,
                episode_key
            ),

            daemon=True
        ).start()

    else:

        send_message(
            chat_id,
            "❌ ارسال فایل انجام نشد."
        )


# =========================
# 30 Second Delete
# =========================

def delete_after_30_seconds(
    chat_id,
    message_ids,
    episode_key
):

    time.sleep(30)


    for message_id in message_ids:

        delete_message(
            chat_id,
            message_id
        )


    link = (
        "https://t.me/"
        + BOT_USERNAME
        + "?start="
        + episode_key
    )


    send_message(
        chat_id,

        "⏰ زمان دانلود تمام شد.\n\n"
        "🔗 دانلود مجدد:\n"
        + link
    )


# =========================
# Webhook
# =========================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = request.get_json(
        silent=True
    )


    if not update:
        return "OK"


    print("UPDATE:", update)


    # =========================
    # CALLBACK QUERY
    # =========================

    if "callback_query" in update:

        callback = update["callback_query"]

        user_id = callback["from"]["id"]

        chat_id = callback["message"]["chat"]["id"]

        message_id = callback["message"]["message_id"]

        data = callback.get("data")


        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id":
                    callback["id"]
            }
        )


        # -------------------------
        # Check Join
        # -------------------------

        if data == "check_join":

            if check_all_sponsors(
                user_id
            ):

                delete_message(
                    chat_id,
                    message_id
                )


                pending = load_pending()

                episode_key = pending.get(
                    str(user_id)
                )


                if not episode_key:

                    send_message(
                        chat_id,
                        "❌ لینک قسمت منقضی شده."
                    )

                    return "OK"


                send_reaction_message(
                    chat_id,
                    episode_key
                )


            else:

                send_message(
                    chat_id,
                    "❌ هنوز عضو همه کانال‌ها نشدی."
                )


            return "OK"


        # -------------------------
        # Reaction Done
        # -------------------------

        if data == "reaction_done":

            pending = load_pending()

            episode_key = pending.get(
                str(user_id)
            )


            if not episode_key:

                send_message(
                    chat_id,
                    "❌ لینک قسمت پیدا نشد."
                )

                return "OK"


            delete_message(
                chat_id,
                message_id
            )


            send_episode(
                chat_id,
                episode_key
            )


            return "OK"


        return "OK"


    # =========================
    # MESSAGE
    # =========================

    if "message" not in update:
        return "OK"


    message = update["message"]

    chat_id = message["chat"]["id"]

    user_id = message["from"]["id"]


    # =========================
    # ADMIN UPLOAD
    # =========================

    if user_id == ADMIN_ID:

        file_id = None

        file_type = None


        if "video" in message:

            file_id = message["video"]["file_id"]

            file_type = "video"


        elif "document" in message:

            file_id = message["document"]["file_id"]

            file_type = "document"


        if file_id:

            caption = message.get(
                "caption",
                ""
            ).strip()


            episode_number = extract_episode_number(
                caption
            )

            series_name = extract_series_name(
                caption
            )


            # -------------------------
            # Valid Caption
            # -------------------------

            if episode_number and series_name:

                episode_key = make_episode_key(
                    series_name,
                    episode_number
                )


                episodes = load_episodes()


                if episode_key not in episodes:

                    episodes[episode_key] = []


                files = normalize_episode(
                    episodes[episode_key]
                )


                # حداکثر ۴ کیفیت
                if len(files) >= 4:

                    send_message(
                        chat_id,
                        "❌ این قسمت قبلاً ۴ فایل دارد."
                    )

                    return "OK"


                files.append(
                    {
                        "file_id": file_id,
                        "type": file_type,
                        "caption": caption
                    }
                )


                episodes[episode_key] = files


                save_episodes(
                    episodes
                )


                # Deep Link
                link = (
                    "https://t.me/"
                    + BOT_USERNAME
                    + "?start="
                    + episode_key
                )


                send_message(
                    chat_id,

                    "✅ فایل ذخیره شد.\n\n"

                    "🎬 سریال: "
                    + series_name
                    + "\n"

                    "📺 قسمت: "
                    + episode_number
                    + "\n"

                    "📦 فایل‌ها: "
                    + str(len(files))
                    + "/4\n\n"

                    "🔗 لینک قسمت:\n"
                    + link
                )


                return "OK"


            # -------------------------
            # Invalid Caption
            # -------------------------

            send_message(
                chat_id,

                "❌ کپشن قابل تشخیص نیست.\n\n"

                "فرمت درست:\n\n"

                "🪴 سریال «اسم سریال»\n"
                "🪷 قسمت : 2\n"
                "🫧 زبان اصلی ‼️\n"
                "🎍 کیفیت : 1080"
            )


            return "OK"


    # =========================
    # TEXT
    # =========================

    text = message.get(
        "text",
        ""
    )


    # =========================
    # START
    # =========================

    if text.startswith("/start"):

        parts = text.split()


        # Deep Link
        if len(parts) > 1:

            episode_key = parts[1].strip()


            print(
                "START PAYLOAD:",
                episode_key
            )


            episodes = load_episodes()


            if episode_key not in episodes:

                send_message(
                    chat_id,
                    "❌ این قسمت حذف شده یا وجود ندارد."
                )

                return "OK"


            # ذخیره قسمت برای کاربر
            pending = load_pending()

            pending[str(user_id)] = episode_key

            save_pending(
                pending
            )


            # بررسی عضویت
            if not check_all_sponsors(
                user_id
            ):

                send_message(
                    chat_id,

                    "🔒 برای دریافت فایل ابتدا "
                    "باید عضو کانال‌ها بشی.",

                    membership_keyboard()
                )

                return "OK"


            # بعد از عضویت
            send_reaction_message(
                chat_id,
                episode_key
            )

            return "OK"


        # Start معمولی
        send_message(
            chat_id,

            "سلام 👋\n"
            "لینک قسمت موردنظرت رو باز کن."
        )

        return "OK"


    # =========================
    # ADMIN COMMANDS
    # =========================

    if user_id == ADMIN_ID:


        # =========================
        # DELETE EPISODES
        # =========================

        if text.startswith(
            "/delete_episode"
        ):

            parts = text.split()


            if len(parts) < 2:

                send_message(
                    chat_id,

                    "فرمت:\n\n"
                    "/delete_episode ep_s1234567890_2"
                )

                return "OK"


            episodes = load_episodes()

            deleted = []

            not_found = []


            for episode_key in parts[1:]:

                if episode_key in episodes:

                    del episodes[
                        episode_key
                    ]

                    deleted.append(
                        episode_key
                    )

                else:

                    not_found.append(
                        episode_key
                    )


            save_episodes(
                episodes
            )


            result = ""


            if deleted:

                result += (
                    "✅ حذف شدند:\n"
                    + "\n".join(deleted)
                )


            if not_found:

                if result:
                    result += "\n\n"

                result += (
                    "❌ پیدا نشدند:\n"
                    + "\n".join(not_found)
                )


            send_message(
                chat_id,
                result
            )

            return "OK"


        # =========================
        # ADD SPONSOR
        # =========================

        if text.startswith(
            "/add_sponsor"
        ):

            parts = text.split(
                maxsplit=3
            )


            if len(parts) < 4:

                send_message(
                    chat_id,

                    "فرمت:\n\n"
                    "/add_sponsor "
                    "@channel "
                    "https://t.me/channel "
                    "| نام کانال"
                )

                return "OK"


            channel_id = parts[1]

            url = parts[2]

            title = parts[3].strip()


            if title.startswith("|"):

                title = title[1:].strip()


            sponsors = load_sponsors()


            sponsors.append(
                {
                    "chat_id": channel_id,
                    "title": title,
                    "url": url
                }
            )


            save_sponsors(
                sponsors
            )


            send_message(
                chat_id,
                "✅ اسپانسر اضافه شد."
            )

            return "OK"


        # =========================
        # SPONSORS
        # =========================

        if text == "/sponsors":

            sponsors = load_sponsors()


            if not sponsors:

                send_message(
                    chat_id,
                    "فعلاً اسپانسری وجود ندارد."
                )

                return "OK"


            result = "📢 اسپانسرها:\n\n"


            for i, sponsor in enumerate(
                sponsors,
                1
            ):

                result += (
                    str(i)
                    + ". "
                    + sponsor["title"]
                    + "\n"
                )


            send_message(
                chat_id,
                result
            )

            return "OK"


        # =========================
        # REMOVE SPONSOR
        # =========================

        if text.startswith(
            "/remove_sponsor"
 
