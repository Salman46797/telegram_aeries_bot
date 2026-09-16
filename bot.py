import os
import json
import time
import threading
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

EPISODES_FILE = "episodes.json"
SPONSORS_FILE = "sponsors.json"

BOT_USERNAME = "Seryyaltorki_bot"

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
    except Exception:
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
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


# =========================
# بررسی عضویت
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

    for sponsor in sponsors:

        if not is_member(
            user_id,
            sponsor["chat_id"]
        ):
            return False

    return True


# =========================
# دکمه‌های عضویت
# =========================

def membership_keyboard():

    sponsors = load_sponsors()

    buttons = []

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
# دکمه ری‌اکشن
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
        + CHANNEL_URL
    )

    return send_message(
        chat_id,
        text,
        reaction_keyboard()
    )


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

    sent_messages = []

    # نسخه جدید چند کیفیت
    if isinstance(
        episode,
        list
    ):

        for video in episode:

            result = None

            if video["type"] == "document":

                result = telegram(
                    "sendDocument",
                    {
                        "chat_id": chat_id,
                        "document": video["file_id"],
                        "caption": video.get(
                            "caption",
                            ""
                        )
                    }
                )

            elif video["type"] == "video":

                result = telegram(
                    "sendVideo",
                    {
                        "chat_id": chat_id,
                        "video": video["file_id"],
                        "caption": video.get(
                            "caption",
                            ""
                        )
                    }
                )

            if (
                result
                and result.get("ok")
            ):

                sent_messages.append(
                    result["result"]["message_id"]
                )

    # نسخه قدیمی تک فایل
    else:

        result = None

        if episode["type"] == "document":

            result = telegram(
                "sendDocument",
                {
                    "chat_id": chat_id,
                    "document": episode["file_id"],
                    "caption": episode.get(
                        "caption",
                        ""
                    )
                }
            )

        elif episode["type"] == "video":

            result = telegram(
                "sendVideo",
                {
                    "chat_id": chat_id,
                    "video": episode["file_id"],
                    "caption": episode.get(
                        "caption",
                        ""
                    )
                }
            )

        if (
            result
            and result.get("ok")
        ):

            sent_messages.append(
                result["result"]["message_id"]
            )


    # حذف بعد از ۳۰ ثانیه
    if sent_messages:

        threading.Thread(
            target=delete_after_30,
            args=(
                chat_id,
                sent_messages,
                episode_number
            ),
            daemon=True
        ).start()


def delete_after_30(
    chat_id,
    message_ids,
    episode_number
):

    time.sleep(30)

    for message_id in message_ids:

        delete_message(
            chat_id,
            message_id
        )

    link = (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start=ep_"
        f"{episode_number}"
    )

    send_message(
        chat_id,
        "⏰ زمان دانلود تمام شد.\n\n"
        "🔗 برای دانلود مجدد قسمت "
        + str(episode_number)
        + " روی لینک زیر بزن:\n\n"
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

    update = request.get_json()

    if not update:
        return "OK"


    # =========================
    # Callback
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


        # -------------------------
        # بررسی عضویت
        # -------------------------

        if data == "check_join":

            if check_all_sponsors(
                user_id
            ):

                delete_message(
                    chat_id,
                    message_id
                )

                pending = load_json(
                    "pending.json",
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
                    "❌ هنوز عضو همه کانال‌های اسپانسر نشدی.\n"
                    "اول عضو همه کانال‌ها شو و دوباره "
                    "«عضو شدم ✅» رو بزن."
                )

            return "OK"


        # -------------------------
        # ری‌اکشن
        # -------------------------

        if data == "reaction_done":

            pending = load_json(
                "pending.json",
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


    # =========================
    # Message
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
    # آپلود ویدیو توسط ادمین
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

            # مثال:
            # episode 15
            # quality 720

            lines = caption.splitlines()

            episode_number = None

            for line in lines:

                if line.lower().startswith(
                    "episode "
                ):

                    number = line[
                        8:
                    ].strip()

                    if number.isdigit():

                        episode_number = number

            if episode_number:

                episodes = load_episodes()

                key = (
                    "ep_"
                    + episode_number
                )

                if key not in episodes:
                    episodes[key] = []


                # ذخیره کپشن کامل
                # همان چیزی که خودت نوشتی

                episodes[key].append(
                    {
                        "file_id":
                            file_id,

                        "type":
                            file_type,

                        "caption":
                            caption
                    }
                )

                save_episodes(
                    episodes
                )

                link = (
                    f"https://t.me/"
                    f"{BOT_USERNAME}"
                    f"?start=ep_"
                    f"{episode_number}"
                )

                count = len(
                    episodes[key]
                )

                send_message(
                    chat_id,
                    "✅ ویدیو ذخیره شد.\n\n"
                    "📺 قسمت: "
                    + episode_number
                    + "\n"
                    "🎬 تعداد فایل‌ها: "
                    + str(count)
                    + "\n\n"
                    "🔗 لینک قسمت:\n"
                    + link
                )

                return "OK"


    # =========================
    # Text
    # =========================

    text = message.get(
        "text",
        ""
    )


    # =========================
    # START
    # =========================

    if text.startswith(
        "/start"
    ):

        parts = text.split()

        if len(parts) > 1:

            parameter = parts[1]

            if parameter.startswith(
                "ep_"
            ):

                episode_number = (
                    parameter.replace(
                        "ep_",
                        ""
                    )
                )

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
                    "pending.json",
                    {}
                )

                pending[
                    str(user_id)
                ] = episode_number

                save_json(
                    "pending.json",
                    pending
                )


                # اسپانسرها

                if not check_all_sponsors(
                    user_id
                ):

                    send_message(
                        chat_id,
                        "🔒 برای دریافت فایل ابتدا "
                        "باید عضو کانال‌های زیر بشی:",
                        membership_keyboard()
                    )

                    return "OK"


                # مرحله ری‌اکشن

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


    # =========================
    # مدیریت اسپانسر
    # =========================

    if user_id == ADMIN_ID:


        # -------------------------
        # اضافه کردن اسپانسر
        # -------------------------

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
                    "نام کانال "
                    "https://t.me/channel"
                )

                return "OK"


            channel_id = parts[1]
            title = parts[2]
            url = parts[3]


            sponsors = load_sponsors()

            sponsors.append(
                {
                    "chat_id":
                        channel_id,

                    "title":
                        title,

                    "url":
                        url
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


        # -------------------------
        # نمایش اسپانسرها
        # -------------------------

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


        # -------------------------
        # حذف اسپانسر
        # -------------------------

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


            if 0 <= index < len(
                sponsors
            ):

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


@app.route("/")
def home():

    return "Bot is running."


# =========================
# Run
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
