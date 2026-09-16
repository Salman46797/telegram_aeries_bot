import os
import requests
from flask import Flask, request

# =========================
# تنظیمات ربات
# =========================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

CHANNEL = "@altiustuistsnbol"

# شماره پیام هر قسمت در کانال
# مثال: ep_1 یعنی قسمت 1
EPISODES = {
    "ep_1": 1,
    "ep_2": 2,
    "ep_3": 3,
    "ep_4": 4,
    "ep_5": 5,
}

app = Flask(__name__)


# =========================
# ارسال درخواست به تلگرام
# =========================

def telegram(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    return requests.post(url, data=data).json()


# =========================
# ارسال پیام
# =========================

def send_message(chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:
        data["reply_markup"] = keyboard

    return telegram("sendMessage", data)


# =========================
# منوی قسمت‌ها
# =========================

def episode_menu():

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "قسمت 1", "url": "https://t.me/Seryyaltorki_bot?start=ep_1"},
                {"text": "قسمت 2", "url": "https://t.me/Seryyaltorki_bot?start=ep_2"}
            ],
            [
                {"text": "قسمت 3", "url": "https://t.me/Seryyaltorki_bot?start=ep_3"},
                {"text": "قسمت 4", "url": "https://t.me/Seryyaltorki_bot?start=ep_4"}
            ],
            [
                {"text": "قسمت 5", "url": "https://t.me/Seryyaltorki_bot?start=ep_5"}
            ]
        ]
    }

    return keyboard


# =========================
# دریافت آپدیت تلگرام
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():

    update = request.get_json()

    if not update:
        return "OK"

    message = update.get("message")

    if not message:
        return "OK"

    chat_id = message["chat"]["id"]

    text = message.get("text", "")

    # -------------------------
    # دستور Start
    # -------------------------

    if text.startswith("/start"):

        parts = text.split(maxsplit=1)

        if len(parts) == 1:

            send_message(
                chat_id,
                "🎬 قسمت مورد نظرت رو انتخاب کن:",
                episode_menu()
            )

        else:

            episode = parts[1]

            if episode in EPISODES:

                message_id = EPISODES[episode]

                telegram(
                    "copyMessage",
                    {
                        "chat_id": chat_id,
                        "from_chat_id": CHANNEL,
                        "message_id": message_id
                    }
                )

            else:

                send_message(
                    chat_id,
                    "❌ این قسمت پیدا نشد."
                )

    return "OK"


# =========================
# صفحه اصلی برای Render
# =========================

@app.route("/")
def home():
    return "Bot is running!"


# =========================
# اجرای برنامه
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    render_url = os.environ.get("RENDER_EXTERNAL_URL")

    if BOT_TOKEN and render_url:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            params={"url": f"{render_url}/webhook"}
        )

    app.run(
        host="0.0.0.0",
        port=port
    )
