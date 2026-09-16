import os
import json
import re
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

BOT_USERNAME = "Seryyaltorki_bot"

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


# =========================
# ارسال پیام
# =========================

def send_message(chat_id, text, reply_markup=None):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(
            reply_markup,
            ensure_ascii=False
        )

    return telegram(
        "sendMessage",
        data
    )


# =========================
# حذف پیام
# =========================

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


# =========================
# Episodes
# =========================

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


# =========================
# Sponsors
# =========================

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


# =========================
# عضویت در کانال
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


# =========================
# همه اسپانسرها
# =========================

def check_all_sponsors(user_id):

    sponsors = load_sponsors()

    for sponsor in sponsors:

        if not is_member(
            user_id,
            sponsor["chat_id"]
        ):

            return False

    return True


# =========================
# دکمه عضویت
# =========================

def membership_keyboard():

    sponsors = load_sponsors()

    buttons = []

    # کانال اصلی
    buttons.append([
        {
            "text": "📢 عضویت در کانال",
            "url": CHANNEL_URL
        }
    ])

    # اسپانسرها
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
# مرحله ری‌اکشن
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
    episode_number
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
# استخراج شماره قسمت از کپشن
# =========================

def extract_episode_number(caption):

    if not caption:
        return None

    pattern = (
        r"(?:قسمت|episode)"
        r"\s*[:：]?\s*"
        r"(\d+)"
    )

    match = re.search(
        pattern,
        caption,
        re.IGNORECASE
    )

    if match:
        return match.group(1)

    return None


# =========================
# ارسال قسمت
# =========================

def send_episode(
    chat_id,
    episode_number
):

    episodes = load_episodes()

    key = "ep_" + str(
        episode_number
    )

    if key not in episodes:

        send_message(
            chat_id,
            "❌ این قسمت هنوز آپلود نشده."
        )

        return

    episode = episodes[key]

    caption = episode.get(
        "caption",
        ""
    )

    file_id = episode["file_id"]
    file_type = episode["type"]

    # -------------------------
    # Document
    # -------------------------

    if file_type == "document":

        telegram(
            "sendDocument",
            {
                "chat_id": chat_id,
                "document": file_id,
                "caption": caption
            }
        )

    # -------------------------
    # Video
    # -------------------------

    elif file_type == "video":

        telegram(
            "sendVideo",
            {
                "chat_id": chat_id,
                "video": file_id,
                "caption": caption
            }
        )


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


    # ==================================================
    # CALLBACK QUERY
    # ==================================================

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


        # جواب دادن به دکمه
        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id":
                callback["id"]
            }
        )


        # ==================================================
        # عضو شدم
        # ==================================================

        if data == "check_join":

            # کانال اصلی
            main_member = is_member(
                user_id,
                CHANNEL_ID
            )

            # اسپانسرها
            sponsors_member = check_all_sponsors(
                user_id
            )

            if main_member and sponsors_member:

                delete_message(
                    chat_id,
                    message_id
                )

                pending = load_json(
                    PENDING_FILE,
                    {}
                )

                ep = pending.get(
                    str(user_id)
                )

                if not ep:

                    send_message(
                        chat_id,
                        "❌ لینک قسمت منقضی شده."
                    )

                    return "OK"

                send_reaction_message(
                    chat_id,
                    ep
                )

            else:

                send_message(
                    chat_id,
                    "❌ هنوز در همه کانال‌های لازم عضو نشدی.\n\n"
                    "اول عضو شو و دوباره «عضو شدم ✅» رو بزن."
                )

            return "OK"


        # ==================================================
        # انجام شد
        # ==================================================

        if data == "reaction_done":

            pending = load_json(
                PENDING_FILE,
                {}
            )

            ep = pending.get(
                str(user_id)
            )

            if not ep:

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
                ep
            )

            return "OK"


        return "OK"


    # ==================================================
    # MESSAGE
    # ==================================================

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


    # ==================================================
    # آپلود فایل توسط ادمین
    # ==================================================

    if user_id == ADMIN_ID:

        file_id = None
        file_type = None


        # Document
        if "document" in message:

            file_id = message[
                "document"
            ]["file_id"]

            file_type = "document"


        # Video
        elif "video" in message:

            file_id = message[
                "video"
            ]["file_id"]

            file_type = "video"


        if file_id:

            # کپشن اصلی را بدون lower کردن نگه می‌داریم
            caption = message.get(
                "caption",
                ""
            ).strip()


            # پیدا کردن شماره قسمت
            episode_number = extract_episode_number(
                caption
            )


            if episode_number:

                episodes = load_episodes()

                key = (
                    "ep_"
                    + episode_number
                )


                # ذخیره فایل + کپشن
                episodes[key] = {

                    "file_id": file_id,

                    "type": file_type,

                    "caption": caption
                }


                save_episodes(
                    episodes
                )


                # لینک قسمت
                link = (
                    "https://t.me/"
                    + BOT_USERNAME
                    + "?start=ep_"
                    + episode_number
                )


                send_message(
                    chat_id,

                    "✅ قسمت "
                    + episode_number
                    + " ذخیره شد.\n\n"
                    "🔗 لینک دانلود:\n"
                    + link
                )


                return "OK"


    # ==================================================
    # TEXT
    # ==================================================

    text = message.get(
        "text",
        ""
    )


    # ==================================================
    # START
    # ==================================================

    if text.startswith(
        "/start"
    ):

        parts = text.split()

        if len(parts) > 1:

            parameter = parts[1]


            if parameter.startswith(
                "ep_"
            ):

                episode_number = parameter.replace(
                    "ep_",
                    "",
                    1
                )


                # فقط عدد قبول شود
                if not episode_number.isdigit():

                    send_message(
                        chat_id,
                        "❌ لینک قسمت نامعتبر است."
                    )

                    return "OK"


                episodes = load_episodes()

                key = (
                    "ep_"
                    + episode_number
                )


                if key not in episodes:

                    send_message(
                        chat_id,
                        "❌ این قسمت هنوز آپلود نشده."
                    )

                    return "OK"


                # ذخیره قسمت در انتظار
                pending = load_json(
                    PENDING_FILE,
                    {}
                )

                pending[
                    str(user_id)
                ] = episode_number

                save_json(
                    PENDING_FILE,
                    pending
                )


                # بررسی عضویت
                if not is_member(
                    user_id,
                    CHANNEL_ID
                ):

                    send_message(
                        chat_id,

                        "🔒 برای دریافت فایل ابتدا "
                        "باید عضو کانال بشی.",

                        membership_keyboard()
                    )

                    return "OK"


                # بررسی اسپانسرها
                if not check_all_sponsors(
                    user_id
                ):

                    send_message(
                        chat_id,

                        "🔒 برای دریافت فایل باید "
                        "در کانال‌های لازم هم عضو بشی.",

                        membership_keyboard()
                    )

                    return "OK"


                # عضو است → مرحله ری‌اکشن
                send_reaction_message(
                    chat_id,
                    episode_number
                )

                return "OK"


        send_message(
            chat_id,

            "سلام 👋\n"
            "لینک قسمت موردنظرت رو باز کن."
        )

        return "OK"


    # ==================================================
    # مدیریت اسپانسرها
    # ==================================================

    if user_id == ADMIN_ID:


        # ==================================================
        # ADD SPONSOR
        # ==================================================

        if text.startswith(
            "/add_sponsor"
        ):

            parts = text.split(
                maxsplit=2
            )


            if len(parts) < 3:

                send_message(
                    chat_id,

                    "فرمت درست:\n\n"
                    "/add_sponsor @channel "
                    "https://t.me/channel | نام کانال"
                )

                return "OK"


            channel_id = parts[1]

            remaining = parts[2]


            if "|" not in remaining:

                send_message(
                    chat_id,

                    "فرمت درست:\n\n"
                    "/add_sponsor @channel "
                    "https://t.me/channel | نام کانال"
                )

                return "OK"


            url, title = remaining.split(
                "|",
                1
            )


            url = url.strip()
            title = title.strip()


            if not title:

                title = channel_id


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

                "✅ اسپانسر اضافه شد.\n\n"
                "📢 "
                + title
            )

            return "OK"


        # ==================================================
        # SPONSORS
        # ==================================================

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
                    + sponsor["chat_id"]
                    + "\n\n"
                )


            send_message(
                chat_id,
                result
            )

            return "OK"


        # ==================================================
        # REMOVE SPONSOR
        # ==================================================

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


            index = (
                int(parts[1])
                - 1
            )


            sponsors = load_sponsors()


            if 0 <= index < len(sponsors):

                removed = sponsors.pop(
                    index
                )


                save_sponsors(
                    sponsors
                )


                send_message(
                    chat_id,

                    "✅ اسپانسر حذف شد:\n"
                    + removed["title"]
                )

            else:

                send_message(
                    chat_id,

                    "❌ شماره اسپانسر اشتباه است."
                )

            return "OK"


    return "OK"


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
