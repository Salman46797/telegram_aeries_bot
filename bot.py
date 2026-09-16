import os
import json
import re
import requests
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
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        return requests.post(
            url,
            data=data,
            timeout=20
        ).json()
    except Exception as e:
        print("Telegram error:", e)
        return {}


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(
            reply_markup
        )

    return telegram(
        "sendMessage",
        data
    )


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
        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception:
        return default


def save_json(filename, data):

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def load_episodes():
    return load_json(
        EPISODES_FILE,
        {}
    )


def save_episodes(data):
    save_json(
        EPISODES_FILE,
        data
    )


def load_sponsors():
    return load_json(
        SPONSORS_FILE,
        []
    )


def save_sponsors(data):
    save_json(
        SPONSORS_FILE,
        data
    )


def load_pending():
    return load_json(
        PENDING_FILE,
        {}
    )


def save_pending(data):
    save_json(
        PENDING_FILE,
        data
    )


# =========================
# Sponsor / Membership
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

    sponsors = load_sponsors()

    # کانال اصلی
    if not is_member(
        user_id,
        CHANNEL_ID
    ):
        return False

    # اسپانسرها
    for sponsor in sponsors:

        if not is_member(
            user_id,
            sponsor["chat_id"]
        ):
            return False

    return True


def membership_keyboard():

    buttons = []

    buttons.append([
        {
            "text": "📢 عضویت در کانال اصلی",
            "url": CHANNEL_URL
        }
    ])

    sponsors = load_sponsors()

    for sponsor in sponsors:

        buttons.append([
            {
                "text": "📢 " + sponsor["title"],
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

    text = (
        "برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ری‌اکشن ❤️ بزن 👇\n\n"
        f"{CHANNEL_URL}"
    )

    return send_message(
        chat_id,
        text,
        reaction_keyboard()
    )


# =========================
# Episode parser
# =========================

def extract_episode_number(caption):

    if not caption:
        return None

    patterns = [
        r"(?:قسمت|episode|ep)\s*[:：\-]?\s*(\d+)",
        r"(?:قسمت|episode|ep)(\d+)"
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

    if not caption:
        return None

    # حالت:
    # سریال | قسمت 15 | 1080

    parts = [
        x.strip()
        for x in caption.split("|")
    ]

    if len(parts) >= 2:

        series_name = parts[0]

        if series_name:
            return series_name

    return None


def make_series_key(series_name):

    series_name = series_name.strip().lower()

    # تبدیل فاصله‌ها به _
    series_name = re.sub(
        r"\s+",
        "_",
        series_name
    )

    # حذف کاراکترهای اضافی
    series_name = re.sub(
        r"[^a-zA-Z0-9آ-ی_]+",
        "",
        series_name
    )

    return series_name


# =========================
# Episode sending
# =========================

def normalize_episode(episode):

    # نسخه قدیمی
    if isinstance(episode, dict):

        return [episode]

    # نسخه جدید
    if isinstance(episode, list):

        return episode

    return []


def send_episode(
    chat_id,
    episode_key
):

    episodes = load_episodes()

    if episode_key not in episodes:

        send_message(
            chat_id,
            "❌ این قسمت هنوز آپلود نشده."
        )

        return []

    files = normalize_episode(
        episodes[episode_key]
    )

    sent_messages = []

    for item in files:

        file_id = item.get(
            "file_id"
        )

        file_type = item.get(
            "type"
        )

        caption = item.get(
            "caption",
            ""
        )

        result = {}

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

        if result.get("ok"):

            message_id = result[
                "result"
            ]["message_id"]

            sent_messages.append(
                message_id
            )

    return sent_messages


# =========================
# Webhook
# =========================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = request.get_json()

    if not update:
        return "OK"


    # =========================
    # CALLBACK
    # =========================

    if "callback_query" in update:

        callback = update[
            "callback_query"
        ]

        user_id = callback[
            "from"
        ]["id"]

        chat_id = callback[
            "message"
        ]["chat"]["id"]

        message_id = callback[
            "message"
        ]["message_id"]

        data = callback.get(
            "data"
        )

        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id":
                callback["id"]
            }
        )


        # =========================
        # Check Join
        # =========================

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
                    "❌ هنوز عضو همه کانال‌ها نشدی.\n"
                    "اول عضو شو و دوباره «عضو شدم ✅» رو بزن."
                )

            return "OK"


        # =========================
        # Reaction Done
        # =========================

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
    # NORMAL MESSAGE
    # =========================

    if "message" not in update:
        return "OK"

    message = update[
        "message"
    ]

    chat_id = message[
        "chat"
    ]["id"]

    user_id = message[
        "from"
    ]["id"]


    # =========================
    # ADMIN UPLOAD
    # =========================

    if user_id == ADMIN_ID:

        file_id = None
        file_type = None

        if "video" in message:

            file_id = message[
                "video"
            ]["file_id"]

            file_type = "video"

        elif "document" in message:

            file_id = message[
                "document"
            ]["file_id"]

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

            if episode_number and series_name:

                series_key = make_series_key(
                    series_name
                )

                episode_key = (
                    "ep_"
                    + series_key
                    + "_"
                    + episode_number
                )

                episodes = load_episodes()

                if episode_key not in episodes:

                    episodes[
                        episode_key
                    ] = []

                files = normalize_episode(
                    episodes[
                        episode_key
                    ]
                )

                # حداکثر 4 کیفیت
                if len(files) >= 4:

                    send_message(
                        chat_id,
                        "❌ برای این قسمت "
                        "قبلاً ۴ فایل ذخیره شده."
                    )

                    return "OK"


                files.append(
                    {
                        "file_id": file_id,
                        "type": file_type,
                        "caption": caption
                    }
                )

                episodes[
                    episode_key
                ] = files

                save_episodes(
                    episodes
                )

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
                    "📦 تعداد فایل‌های این قسمت: "
                    + str(len(files))
                    + "/4\n\n"
                    "🔗 لینک قسمت:\n"
                    + link
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

        if len(parts) > 1:

            episode_key = parts[1]

            episodes = load_episodes()

            if episode_key not in episodes:

                send_message(
                    chat_id,
                    "❌ این قسمت هنوز آپلود نشده."
                )

                return "OK"


            # ذخیره قسمت برای کاربر
            pending = load_pending()

            pending[
                str(user_id)
            ] = episode_key

            save_pending(
                pending
            )


            # عضویت
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


            send_reaction_message(
                chat_id,
                episode_key
            )

            return "OK"


        send_message(
            chat_id,
            "سلام 👋\n"
            "لینک قسمت موردنظرت رو باز کن."
        )

        return "OK"


    # =========================
    # SPONSOR MANAGEMENT
    # =========================

    if user_id == ADMIN_ID:


        # ADD SPONSOR
        if text.startswith(
            "/add_sponsor"
        ):

            parts = text.split(
                maxsplit=3
            )

            if len(parts) < 4:

                send_message(
                    chat_id,
                    "فرمت صحیح:\n\n"
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


        # SHOW SPONSORS
        if text == "/sponsors":

            sponsors = load_sponsors()

            if not sponsors:

                send_message(
                    chat_id,
                    "فعلاً اسپانسری وجود ندارد."
                )

                return "OK"

            result = (
                "📢 اسپانسرهای فعال:\n\n"
            )

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


        # REMOVE SPONSOR
        if text.startswith(
            "/remove_sponsor"
        ):

            parts = text.split()

            if (
                len(parts) < 2
                or not parts[1].isdigit()
            ):

                send_message(
                    chat_id,
                    "مثال:\n"
                    "/remove_sponsor 2"
                )

                return "OK"

            index = int(
                parts[1]
            ) - 1

            sponsors = load_sponsors()

            if 0 <= index < len(sponsors):

                sponsors.pop(
                    index
                )

                save_sponsors(
                    sponsors
                )

                send_message(
                    chat_id,
                    "✅ اسپانسر حذف شد."
                )

            else:

                send_message(
                    chat_id,
                    "❌ شماره اشتباه است."
                )

            return "OK"


    return "OK"


# =========================
# HOME
# =========================

@app.route("/")
def home():
    return "Bot is running."


# =========================
# START SERVER
# =========================

if __name__ == "__main__":

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is missing"
        )


    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )


    render_url = os.environ.get(
        "RENDER_EXTERNAL_URL"
    )

    if not render_url:

        raise RuntimeError(
            "RENDER_EXTERNAL_URL is missing"
        )


    webhook_url = (
        render_url.rstrip("/")
        + "/webhook"
    )


    result = requests.post(
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/setWebhook",
        data={
            "url": webhook_url
        },
        timeout=20
    )


    print(
        "Webhook:",
        result.text
    )


    app.run(
        host="0.0.0.0",
        port=port
    )
