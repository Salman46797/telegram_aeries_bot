import os
import json
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

EPISODES_FILE = "episodes.json"
SPONSORS_FILE = "sponsors.json"

app = Flask(__name__)


def telegram(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        return requests.post(url, data=data, timeout=20).json()
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


def load_json(filename, default):
    if not os.path.exists(filename):
        return default

    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_episodes():
    return load_json(EPISODES_FILE, {})


def save_episodes(data):
    save_json(EPISODES_FILE, data)


def load_sponsors():
    return load_json(SPONSORS_FILE, [])


def save_sponsors(data):
    save_json(SPONSORS_FILE, data)


# بررسی عضویت
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

    return status in ["member", "administrator", "creator"]


# دکمه عضویت
def membership_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📢 عضویت در کانال",
                    "url": CHANNEL_URL
                }
            ],
            [
                {
                    "text": "عضو شدم ✅",
                    "callback_data": "check_join"
                }
            ]
        ]
    }


# پیام ری‌اکشن
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


def send_reaction_message(chat_id, episode_number):
    text = (
        "برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ری‌اکشن (❤️) بزن 👇\n\n"
        f"{CHANNEL_URL}"
    )

    result = send_message(
        chat_id,
        text,
        reaction_keyboard()
    )

    # ذخیره پیام مرحله ری‌اکشن برای حذف بعدی
    return result


def send_episode(chat_id, episode_number):
    episodes = load_episodes()

    key = "ep_" + str(episode_number)

    if key not in episodes:
        send_message(chat_id, "❌ این قسمت هنوز آپلود نشده.")
        return

    episode = episodes[key]

    if episode["type"] == "document":
        telegram(
            "sendDocument",
            {
                "chat_id": chat_id,
                "document": episode["file_id"]
            }
        )

    elif episode["type"] == "video":
        telegram(
            "sendVideo",
            {
                "chat_id": chat_id,
                "video": episode["file_id"]
            }
        )


@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json()

    if not update:
        return "OK"


    # =========================
    # دکمه‌ها
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
                "callback_query_id": callback["id"]
            }
        )


        # -------------------------
        # عضو شدم
        # -------------------------

        if data == "check_join":

            if is_member(user_id, CHANNEL_ID):

                # حذف پیام عضویت
                delete_message(
                    chat_id,
                    message_id
                )

                # فرستادن مرحله ری‌اکشن
                episode_number = callback.get(
                    "message", {}
                ).get(
                    "text", ""
                )

                # قسمت را از callback جدا می‌کنیم
                pending = load_json(
                    "pending.json",
                    {}
                )

                ep = pending.get(str(user_id))

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
                    "❌ هنوز عضو کانال نشدی.\n"
                    "اول عضو شو و دوباره «عضو شدم ✅» رو بزن."
                )

            return "OK"


        # -------------------------
        # انجام شد
        # -------------------------

        if data == "reaction_done":

            pending = load_json(
                "pending.json",
                {}
            )

            ep = pending.get(str(user_id))

            if not ep:
                send_message(
                    chat_id,
                    "❌ لینک قسمت پیدا نشد."
                )
                return "OK"

            # حذف پیام ری‌اکشن
            delete_message(
                chat_id,
                message_id
            )

            # ارسال فایل
            send_episode(
                chat_id,
                ep
            )

            return "OK"


        return "OK"


    # =========================
    # پیام معمولی
    # =========================

    if "message" not in update:
        return "OK"

    message = update["message"]

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]


    # =========================
    # آپلود فایل توسط ادمین
    # =========================

    if user_id == ADMIN_ID:

        file_id = None
        file_type = None

        if "document" in message:
            file_id = message["document"]["file_id"]
            file_type = "document"

        elif "video" in message:
            file_id = message["video"]["file_id"]
            file_type = "video"

        if file_id:

            caption = message.get(
                "caption",
                ""
            ).strip().lower()

            if caption.startswith("episode "):

                episode_number = caption.replace(
                    "episode ",
                    ""
                ).strip()

                if episode_number.isdigit():

                    episodes = load_episodes()

                    key = "ep_" + episode_number

                    episodes[key] = {
                        "file_id": file_id,
                        "type": file_type
                    }

                    save_episodes(episodes)

                    link = (
                        "https://t.me/Seryyaltorki_bot"
                        "?start=ep_" + episode_number
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


    # =========================
    # START
    # =========================

    text = message.get(
        "text",
        ""
    )

    if text.startswith("/start"):

        parts = text.split()

        if len(parts) > 1:

            parameter = parts[1]

            if parameter.startswith("ep_"):

                episode_number = parameter.replace(
                    "ep_",
                    ""
                )

                episodes = load_episodes()

                if (
                    "ep_" + episode_number
                ) not in episodes:

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

                pending[str(user_id)] = episode_number

                save_json(
                    "pending.json",
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


                # اگر عضو بود → مستقیم مرحله ری‌اکشن
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
    # مدیریت اسپانسرها
    # =========================

    if user_id == ADMIN_ID:

        # اضافه کردن اسپانسر
        if text.startswith("/add_sponsor"):

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
                    "chat_id": channel_id,
                    "title": title,
                    "url": url
                }
            )

            save_sponsors(sponsors)

            send_message(
                chat_id,
                "✅ اسپانسر اضافه شد."
            )

            return "OK"


        # نمایش اسپانسرها
        if text == "/sponsors":

            sponsors = load_sponsors()

            if not sponsors:

                send_message(
                    chat_id,
                    "فعلاً اسپانسری وجود ندارد."
                )

                return "OK"

            result = "📢 اسپانسرهای فعال:\n\n"

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


        # حذف اسپانسر
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

            index = int(parts[1]) - 1

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
