import os
import json
import requests
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 5648301086

app = Flask(__name__)

# فایل ذخیره اطلاعات قسمت‌ها
DB_FILE = "episodes.json"


def load_episodes():
    if not os.path.exists(DB_FILE):
        return {}

    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_episodes(episodes):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(episodes, f, ensure_ascii=False, indent=2)


def telegram(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = requests.post(url, data=data, timeout=30)
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

    # دریافت پیام
    if "message" not in update:
        return "OK"

    message = update["message"]
    chat_id = message["chat"]["id"]

    # اگر فایل ارسال شده
    if "document" in message or "video" in message:

        if chat_id != ADMIN_ID:
            send_message(
                chat_id,
                "❌ شما اجازه آپلود فایل ندارید."
            )
            return "OK"

        episodes = load_episodes()

        caption = message.get("caption", "").strip()

        # فرمت کپشن:
        # episode 15
        if caption.lower().startswith("episode "):

            episode_number = caption.split(" ", 1)[1].strip()

            if "document" in message:
                file_id = message["document"]["file_id"]
                file_type = "document"
            else:
                file_id = message["video"]["file_id"]
                file_type = "video"

            episodes[episode_number] = {
                "file_id": file_id,
                "type": file_type
            }

            save_episodes(episodes)

            send_message(
                chat_id,
                f"✅ قسمت {episode_number} ذخیره شد.\n\n"
                f"🔗 لینک:\n"
                f"https://t.me/Seryyaltorki_bot?start=ep_{episode_number}"
            )

        else:
            send_message(
                chat_id,
                "❌ کپشن فایل باید این شکلی باشه:\n\n"
                "episode 15"
            )

        return "OK"

    # پیام متنی
    text = message.get("text", "").strip()

    # /start
    if text.startswith("/start"):

        parts = text.split(maxsplit=1)

        # لینک مستقیم قسمت
        if len(parts) == 2:

            parameter = parts[1]

            if parameter.startswith("ep_"):

                episode_number = parameter.replace("ep_", "", 1)

                episodes = load_episodes()

                if episode_number not in episodes:
                    send_message(
                        chat_id,
                        f"❌ قسمت {episode_number} هنوز آپلود نشده."
                    )
                    return "OK"

                episode = episodes[episode_number]

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

                return "OK"

        send_message(
            chat_id,
            "🎬 سلام!\n\n"
            "برای دریافت قسمت موردنظر، لینک همان قسمت را باز کن."
        )

        return "OK"

    # دستور لیست قسمت‌ها برای ادمین
    if text == "/episodes":

        if chat_id != ADMIN_ID:
            return "OK"

        episodes = load_episodes()

        if not episodes:
            send_message(
                chat_id,
                "هنوز هیچ قسمتی آپلود نشده."
            )
            return "OK"

        result = "📋 قسمت‌های موجود:\n\n"

        for number in sorted(episodes, key=lambda x: int(x)):
            result += (
                f"قسمت {number}:\n"
                f"https://t.me/Seryyaltorki_bot?start=ep_{number}\n\n"
            )

        send_message(chat_id, result)

        return "OK"

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
