import os
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL = "@altiustuistsnbol"

# شماره پست‌های واقعی کانال را بعداً اینجا وارد می‌کنیم
EPISODES = {
    "ep_1": 1,
    "ep_2": 2,
    "ep_3": 3,
    "ep_4": 4,
    "ep_5": 5,
}

app = Flask(__name__)


def telegram(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = requests.post(url, data=data, timeout=20)
    return response.json()


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return telegram("sendMessage", data)


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json()

    if not update:
        return "OK"

    # پیام معمولی
    if "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        # /start
        if text.startswith("/start"):
            parts = text.split(" ", 1)

            # اگر لینک مستقیم قسمت باشد
            if len(parts) > 1:
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
                    return "OK"

            # منوی اصلی
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "قسمت ۱", "url": "https://t.me/Seryyaltorki_bot?start=ep_1"},
                        {"text": "قسمت ۲", "url": "https://t.me/Seryyaltorki_bot?start=ep_2"}
                    ],
                    [
                        {"text": "قسمت ۳", "url": "https://t.me/Seryyaltorki_bot?start=ep_3"},
                        {"text": "قسمت ۴", "url": "https://t.me/Seryyaltorki_bot?start=ep_4"}
                    ],
                    [
                        {"text": "قسمت ۵", "url": "https://t.me/Seryyaltorki_bot?start=ep_5"}
                    ]
                ]
            }

            send_message(
                chat_id,
                "🎬 سریال بالا پایین استانبول\n\nقسمت موردنظر رو انتخاب کن:",
                keyboard
            )

            return "OK"

        send_message(
            chat_id,
            "برای شروع روی /start بزن."
        )

    return "OK"


@app.route("/")
def home():
    return "Bot is running!"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    webhook_url = "https://telegram-aeries-bot.onrender.com/webhook"

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    result = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        params={"url": webhook_url},
        timeout=20
    )

    print("Webhook:", result.text)

    app.run(
        host="0.0.0.0",
        port=port
    )
