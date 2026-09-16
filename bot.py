import os
import json
import re
import time
import hashlib
import threading
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 5648301086

BOT_USERNAME = "Seryyaltorki_bot"
CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

RENDER_URL = "https://telegram-aeries-bot.onrender.com"

EPISODES_FILE = "episodes.json"
SPONSORS_FILE = "sponsors.json"
PENDING_FILE = "pending.json"

app = Flask(__name__)


def telegram(method, data):
    try:
        return requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
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
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


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

    return result["result"]["status"] in [
        "member",
        "administrator",
        "creator"
    ]


def check_all_sponsors(user_id):

    if not is_member(user_id, CHANNEL_ID):
        return False

    for sponsor in load_sponsors():

        if not is_member(
            user_id,
            sponsor["chat_id"]
        ):
            return False

    return True


def membership_keyboard():

    buttons = [[
        {
            "text": "📢 عضویت در کانال اصلی",
            "url": CHANNEL_URL
        }
    ]]

    for sponsor in load_sponsors():

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


def send_reaction_message(chat_id):

    return send_message(
        chat_id,
        "برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ری‌اکشن ❤️ بزن 👇\n\n"
        + CHANNEL_URL,
        reaction_keyboard()
    )


# -------------------------
# Caption parsing
# -------------------------

def extract_episode_number(caption):

    match = re.search(
        r"(?:قسمت|episode|ep)\s*[:：\-]?\s*(\d+)",
        caption,
        re.IGNORECASE
    )

    if match:
        return match.group(1)

    return None


def extract_series_name(caption):

    match = re.search(
        r"سریال\s*[«\"]?\s*(.+?)\s*[»\"]?\s*$",
        caption,
        re.MULTILINE
    )

    if match:
        name = match.group(1).strip()
        name = name.strip("«»\"' ")

        if name:
            return name

    # فرمت قدیمی
    parts = [
        x.strip()
        for x in caption.split("|")
    ]

    if len(parts) >= 2:
        return parts[0]

    return None


def extract_subtitle_type(caption):

    if "زبان اصلی" in caption:
        return "زبان اصلی"

    if "زیرنویس فوری" in caption:
        return "زیرنویس فوری"

    if "زیرنویس مووی‌باز" in caption:
        return "زیرنویس مووی‌باز"

    if "زیرنویس مووی باز" in caption:
        return "زیرنویس مووی‌باز"

    return "نامشخص"


# -------------------------
# Safe link
# -------------------------

def make_series_key(series_name):

    series_hash = hashlib.sha1(
        series_name.strip().lower().encode("utf-8")
    ).hexdigest()[:10]

    return "s" + series_hash


def make_episode_key(series_name, episode_number):

    return (
        "ep_"
        + make_series_key(series_name)
        + "_"
        + str(episode_number)
    )


def normalize_episode(data):

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        return [data]

    return []


# -------------------------
# Send episode
# -------------------------

def send_episode(chat_id, episode_key):

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

    sent_ids = []

    for item in files:

        file_id = item.get("file_id")
        file_type = item.get("type")
        caption = item.get("caption", "")

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
            sent_ids.append(
                result["result"]["message_id"]
            )

    if sent_ids:

        threading.Thread(
            target=delete_after_30_seconds,
            args=(chat_id, sent_ids),
            daemon=True
        ).start()


def delete_after_30_seconds(chat_id, message_ids):

    time.sleep(30)

    for message_id in message_ids:

        delete_message(
            chat_id,
            message_id
        )

    send_message(
        chat_id,
        "⏰ زمان دانلود تمام شد.\n\n"
        "🔗 دانلود مجدد:\n"
        + RENDER_URL.replace(
            "https://telegram-aeries-bot.onrender.com",
            "https://t.me/" + BOT_USERNAME
        )
    )


# -------------------------
# Webhook
# -------------------------

@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json()

    if not update:
        return "OK"

    print("UPDATE:", update)

    # Callback
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

        if data == "check_join":

            if check_all_sponsors(user_id):

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

                send_reaction_message(chat_id)

            else:

                send_message(
                    chat_id,
                    "❌ هنوز عضو همه کانال‌ها نشدی."
                )

            return "OK"

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

    if "message" not in update:
        return "OK"

    message = update["message"]

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]

    # -------------------------
    # Admin upload
    # -------------------------

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

            subtitle_type = extract_subtitle_type(
                caption
            )

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

                if len(files) >= 4:

                    send_message(
                        chat_id,
                        "❌ این قسمت قبلاً ۴ فایل دارد."
                    )

                    return "OK"

                files.append({
                    "file_id": file_id,
                    "type": file_type,
                    "caption": caption,
                    "subtitle_type": subtitle_type
                })

                episodes[episode_key] = files

                save_episodes(episodes)

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
                    "🫧 نوع: "
                    + subtitle_type
                    + "\n"
                    "📦 فایل‌ها: "
                    + str(len(files))
                    + "/4\n\n"
                    "🔗 لینک:\n"
                    + link
                )

                return "OK"

    text = message.get(
        "text",
        ""
    )

    # -------------------------
    # Start
    # -------------------------

    if text.startswith("/start"):

        parts = text.split()

        if len(parts) > 1:

            episode_key = parts[1]

            episodes = load_episodes()

            if episode_key not in episodes:

                send_message(
                    chat_id,
                    "❌ این قسمت حذف شده یا وجود ندارد."
                )

                return "OK"

            pending = load_pending()

            pending[str(user_id)] = episode_key

            save_pending(pending)

            if not check_all_sponsors(user_id):

                send_message(
                    chat_id,
                    "🔒 برای دریافت فایل ابتدا "
                    "باید عضو کانال‌ها بشی.",
                    membership_keyboard()
                )

                return "OK"

            send_reaction_message(chat_id)

            return "OK"

        send_message(
            chat_id,
            "سلام 👋\n"
            "لینک قسمت موردنظرت رو باز کن."
        )

        return "OK"

    # -------------------------
    # Admin commands
    # -------------------------

    if user_id == ADMIN_ID:

        # DELETE ALL

        if text == "/delete_all":

            count = len(load_episodes())

            save_episodes({})
            save_pending({})

            send_message(
                chat_id,
                "✅ همه قسمت‌های ذخیره‌شده حذف شدند.\n\n"
                "🗑 تعداد قسمت‌ها: "
                + str(count)
            )

            return "OK"

        # DELETE EPISODE

        if text.startswith("/delete_episode"):

            parts = text.split()

            if len(parts) < 2:

                send_message(
                    chat_id,
                    "فرمت:\n/delete_episode ep_..."
                )

                return "OK"

            episodes = load_episodes()

            deleted = []

            for key in parts[1:]:

                if key in episodes:

                    del episodes[key]
                    deleted.append(key)

            save_episodes(episodes)

            if deleted:

                send_message(
                    chat_id,
                    "✅ حذف شد:\n\n"
                    + "\n".join(deleted)
                )

            else:

                send_message(
                    chat_id,
                    "❌ هیچ‌کدام پیدا نشد."
                )

            return "OK"

        # ADD SPONSOR

        if text.startswith("/add_sponsor"):

            parts = text.split(
                maxsplit=3
            )

            if len(parts) < 4:

                send_message(
                    chat_id,
                    "فرمت:\n"
                    "/add_sponsor @channel "
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

            sponsors.append({
                "chat_id": channel_id,
                "title": title,
                "url": url
            })

            save_sponsors(sponsors)

            send_message(
                chat_id,
                "✅ اسپانسر اضافه شد."
            )

            return "OK"

        # SPONSORS

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

        # REMOVE SPONSOR

        if text.startswith("/remove_sponsor"):

            parts = text.split()

            if (
                len(parts) < 2
                or not parts[1].isdigit()
            ):

                send_message(
                    chat_id,
                    "مثال:\n/remove_sponsor 2"
                )

                return "OK"

            index = int(parts[1]) - 1

            sponsors = load_sponsors()

            if 0 <= index < len(sponsors):

                sponsors.pop(index)

                save_sponsors(sponsors)

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
        raise RuntimeError("BOT_TOKEN is missing")

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    webhook_url = RENDER_URL.rstrip("/") + "/webhook"

    result = requests.post(
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/setWebhook",
        data={
            "url": webhook_url
        },
        timeout=20
    )

    print("Webhook:", result.text)

    app.run(
        host="0.0.0.0",
        port=port
    )
