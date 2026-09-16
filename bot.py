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

EPISODES_FILE = "episodes.json"
SPONSORS_FILE = "sponsors.json"
PENDING_FILE = "pending.json"

app = Flask(__name__)


# =========================
# Telegram
# =========================

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


# =========================
# JSON
# =========================

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


def send_reaction_message(chat_id, episode_key):

    return send_message(
        chat_id,
        "برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ری‌اکشن ❤️ بزن 👇\n\n"
        + CHANNEL_URL,
        reaction_keyboard()
    )


# =========================
# Caption
# =========================

def extract_episode_number(caption):

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

    # فرمت:
    # 🪴 سریال «عشق و تخت»

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

    # فرمت قدیمی:
    # سریال | قسمت | کیفیت

    parts = [
        x.strip()
        for x in caption.split("|")
    ]

    if len(parts) >= 2 and parts[0]:
        return parts[0]

    return None


def extract_subtitle_type(caption):

    text = caption.lower()

    if "زبان اصلی" in text:
        return "زبان اصلی"

    if "زیرنویس فوری" in text:
        return "زیرنویس فوری"

    if "زیرنویس مووی‌باز" in text:
        return "زیرنویس مووی‌باز"

    if "زیرنویس مووی باز" in text:
        return "زیرنویس مووی‌باز"

    if "زیرنویس moviebaz" in text:
        return "زیرنویس مووی‌باز"

    return "نامشخص"


# =========================
# Safe Episode Key
# =========================

def make_series_key(series_name):

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


def normalize_episode(episode):

    if isinstance(episode, dict):
        return [episode]

    if isinstance(episode, list):
        return episode

    return []


# =========================
# Delete after 30 seconds
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
# Send Episode
# =========================

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

    sent_message_ids = []

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

            sent_message_ids.append(
                result["result"]["message_id"]
            )

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

    print("UPDATE:", update)

    # =====================
    # Callback
    # =====================

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

        # -----------------
        # Check
