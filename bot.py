import os
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL = "@altiustuistsnbol"

EPISODES = {
    "ep_1": 1,
    "ep_2": 2,
    "ep_3": 3,
    "ep_4": 4,
    "ep_5": 5,
}

app = Flask(__name__)


def telegram(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = requests.post(url, data=data, timeout=20)
    return response.json()


def send_message(chat_id, text):
    return telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


@app.route("/")
def home():
    return "Bot is running!"


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(silent=True)

    if not update:
        return "OK"

    if "message" not in update:
        return "OK"

    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text.startswith("/start"):

        parts = text.split(maxsplit=1)

        if len(parts) == 2:
            episode = parts[1]

            if episode in EPISODES:
                telegram(
                    "copyMessage",
                    {
                        "chat_id": chat_id,
                        "from_chat_id": CHANNEL,
                        "message_id": EPISODES[episode]
                    }
                )
                return "OK"

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "قسمت ۱",
                        "url": "https://t.me/Seryyaltorki_bot?start=ep_1"
                    },
                    {
                        "text": "قسمت ۲",
                        "url": "https://t.me/Seryyaltorki_bot?start=ep_2"
                    }
                ],
                [
                    {
                        "text": "قسمت ۳",
                        "url": "https://t.me/Seryyaltorki_bot?start=ep_3"
                    },
                    {
                        "text": "قسمت ۴",
                        "url": "https://t.me/Seryyaltorki_bot?start=ep_4"
                    }
                ],
                [
                    {
                        "text": "قسمت ۵",
                        "url": "https://t.me/Seryyaltorki_bot?start=ep_5"
                    }
                ]
            ]
        }

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "🎬 سریال بالا پایین استانبول\n\nقسمت موردنظرت رو انتخاب کن:",
                "reply_markup": str(keyboard).replace("'", '"')
            }
        )

        return "OK"

    send_message(
        chat_id,
        "برای شروع روی /start بزن."
    )

    return "OK"


if __name__ == "__main__":

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    port = int(os.environ.get("PORT", 10000))

    render_url = os.environ.get("RENDER_EXTERNAL_URL")

    if not render_url:
        raise RuntimeError("RENDER_EXTERNAL_URL is missing")

    webhook_url = render_url.rstrip("/") + "/webhook"

    result = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
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
