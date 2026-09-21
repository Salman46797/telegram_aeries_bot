import os
import re
import json
import random
import string
import threading
import time
import requests
from flask import Flask, request

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

WEBHOOK_URL = "https://telegram-aeries-bot.onrender.com/webhook"
BOT_USERNAME = "Seryyaltorki_bot"

app = Flask(__name__)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}


# =========================================================
# TELEGRAM API
# =========================================================

def telegram(method, data=None):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        r = requests.post(
            url,
            json=data or {},
            timeout=30
        )

        return r.json()

    except Exception as e:

        print("Telegram error:", e)

        return {
            "ok": False
        }


def send_message(chat_id, text, keyboard=None):

    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }

    if keyboard:

        data["reply_markup"] = keyboard

    return telegram(
        "sendMessage",
        data
    )


def delete_message(chat_id, message_id):

    try:

        return telegram(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id
            }
        )

    except Exception as e:

        print("Delete error:", e)

        return None


# =========================================================
# SUPABASE
# =========================================================

def db_get(table, params=None):

    try:

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params=params or {},
            timeout=30
        )

        if r.status_code != 200:

            print("DB GET:", r.text)

            return []

        return r.json()

    except Exception as e:

        print("DB GET ERROR:", e)

        return []


def db_insert(table, data):

    try:

        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={
                **HEADERS,
                "Prefer": "return=representation"
            },
            json=data,
            timeout=30
        )

        print("DB INSERT:", r.status_code, r.text)

        if r.status_code in [200, 201]:

            return r.json()

        return None

    except Exception as e:

        print("DB INSERT ERROR:", e)

        return None


def db_patch(table, params, data):

    try:

        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={
                **HEADERS,
                "Prefer": "return=representation"
            },
            params=params,
            json=data,
            timeout=30
        )

        print("DB PATCH:", r.status_code, r.text)

        return r.status_code in [200, 204]

    except Exception as e:

        print("DB PATCH ERROR:", e)

        return False


def db_delete(table, params):

    try:

        r = requests.delete(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params=params,
            timeout=30
        )

        return r.status_code in [200, 204]

    except Exception as e:

        print("DB DELETE ERROR:", e)

        return False


# =========================================================
# RANDOM CODE
# =========================================================

def random_code(length=6):

    chars = string.ascii_letters + string.digits

    return "".join(
        random.choice(chars)
        for _ in range(length)
    )


def code_exists(code):

    rows = db_get(
        "episodes",
        {
            "select": "id,start_code,files"
        }
    )

    for row in rows:

        if row.get("start_code") == code:

            return True

        files = row.get("files") or []

        for item in files:

            if isinstance(item, dict):

                if item.get("type_code") == code:

                    return True

    return False


def unique_code():

    while True:

        code = random_code()

        if not code_exists(code):

            return code


# =========================================================
# EPISODES
# =========================================================

def get_episode_by_key(key):

    rows = db_get(
        "episodes",
        {
            "episode_key": f"eq.{key}",
            "limit": "1"
        }
    )

    if rows:

        return rows[0]

    return None


def get_all_episodes():

    return db_get(
        "episodes",
        {
            "select": "*"
        }
    )


def ensure_type_codes(episode):

    files = episode.get("files") or []

    changed = False

    existing_codes = []

    for item in files:

        if not isinstance(item, dict):

            continue

        code = item.get("type_code")

        if code:

            existing_codes.append(code)

    grouped = {}

    for item in files:

        if not isinstance(item, dict):

            continue

        file_type = item.get(
            "file_type",
            "نامشخص"
        )

        if file_type not in grouped:

            grouped[file_type] = None

        if item.get("type_code"):

            grouped[file_type] = item["type_code"]

    for file_type in grouped:

        if not grouped[file_type]:

            while True:

                code = unique_code()

                if code not in existing_codes:

                    break

            grouped[file_type] = code

            existing_codes.append(code)

            changed = True

    for item in files:

        if not isinstance(item, dict):

            continue

        file_type = item.get(
            "file_type",
            "نامشخص"
        )

        code = grouped[file_type]

        if item.get("type_code") != code:

            item["type_code"] = code

            changed = True

    if changed:

        db_patch(
            "episodes",
            {
                "id": f"eq.{episode['id']}"
            },
            {
                "files": files
            }
        )

        episode["files"] = files

    return episode


def get_episode_by_type_code(code):

    # لینک‌های قدیمی
    rows = db_get(
        "episodes",
        {
            "start_code": f"eq.{code}",
            "limit": "1"
        }
    )

    if rows:

        return rows[0], None

    # لینک‌های نوع فایل
    rows = db_get(
        "episodes",
        {
            "select": "*"
        }
    )

    for episode in rows:

        files = episode.get("files") or []

        for item in files:

            if isinstance(item, dict):

                if item.get("type_code") == code:

                    return episode, item.get(
                        "file_type",
                        "نامشخص"
                    )

    return None, None


def get_type_links(episode):

    episode = ensure_type_codes(
        episode
    )

    result = {}

    for item in episode.get("files") or []:

        if not isinstance(item, dict):

            continue

        file_type = item.get(
            "file_type",
            "نامشخص"
        )

        code = item.get(
            "type_code"
        )

        if code:

            result[file_type] = code

    return result


# =========================================================
# PENDING
# =========================================================

def set_pending(user_id, episode_key, file_type):

    value = f"{episode_key}|||{file_type}"

    old = db_get(
        "pending",
        {
            "user_id": f"eq.{user_id}"
        }
    )

    if old:

        db_patch(
            "pending",
            {
                "user_id": f"eq.{user_id}"
            },
            {
                "episode_key": value
            }
        )

    else:

        db_insert(
            "pending",
            {
                "user_id": user_id,
                "episode_key": value
            }
        )


def get_pending(user_id):

    rows = db_get(
        "pending",
        {
            "user_id": f"eq.{user_id}",
            "limit": "1"
        }
    )

    if rows:

        return rows[0]

    return None


def delete_pending(user_id):

    db_delete(
        "pending",
        {
            "user_id": f"eq.{user_id}"
        }
    )


# =========================================================
# SPONSORS
# =========================================================

def get_sponsors():

    return db_get(
        "sponsors",
        {
            "select": "*",
            "order": "id.asc"
        }
    )


def add_sponsor(chat_id, title, url):

    return db_insert(
        "sponsors",
        {
            "chat_id": chat_id,
            "title": title,
            "url": url
        }
    )


def remove_sponsor(sponsor_id):

    return db_delete(
        "sponsors",
        {
            "id": f"eq.{sponsor_id}"
        }
    )


# =========================================================
# MEMBERSHIP
# =========================================================

def is_member(chat_id, user_id):

    result = telegram(
        "getChatMember",
        {
            "chat_id": chat_id,
            "user_id": user_id
        }
    )

    if not result.get("ok"):

        return False

    status = result["result"].get(
        "status"
    )

    return status in [
        "member",
        "administrator",
        "creator"
    ]


def all_channels_joined(user_id):

    # کانال اصلی
    if not is_member(
        CHANNEL_ID,
        user_id
    ):

        return False

    # اسپانسرها
    sponsors = get_sponsors()

    for sponsor in sponsors:

        if not is_member(
            sponsor["chat_id"],
            user_id
        ):

            return False

    return True


# =========================================================
# JOIN MESSAGE
# =========================================================

def show_join_message(chat_id):

    sponsors = get_sponsors()

    buttons = []

    for sponsor in sponsors:

        buttons.append(
            [
                {
                    "text": sponsor["title"],
                    "url": sponsor["url"]
                }
            ]
        )

    buttons.append(
        [
            {
                "text": "عضو شدم ✅",
                "callback_data": "check_join"
            }
        ]
    )

    return send_message(
        chat_id,
        "📢 برای دریافت فایل ابتدا در همه کانال‌های زیر عضو شو:\n\n"
        "بعد از عضویت روی «عضو شدم ✅» بزن.",
        {
            "inline_keyboard": buttons
        }
    )


# =========================================================
# POSTS STEP
# =========================================================

def show_posts_message(chat_id):

    return send_message(
        chat_id,
        "❤️ برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ببین 👇\n\n"
        f"{CHANNEL_URL}\n\n"
        "بعد روی «انجام شد ✅» بزن.",
        {
            "inline_keyboard": [
                [
                    {
                        "text": "📢 مشاهده ۵ پست آخر",
                        "url": CHANNEL_URL
                    }
                ],
                [
                    {
                        "text": "انجام شد ✅",
                        "callback_data": "done_posts"
                    }
                ]
            ]
        }
    )


# =========================================================
# SEND FILES
# =========================================================

def send_file(chat_id, item):

    file_id = item.get(
        "file_id"
    )

    if not file_id:

        return None

    file_type = item.get(
        "type",
        "video"
    )

    caption = item.get(
        "caption",
        ""
    )

    if file_type == "document":

        result = telegram(
            "sendDocument",
            {
                "chat_id": chat_id,
                "document": file_id,
                "caption": caption
            }
        )

    else:

        result = telegram(
            "sendVideo",
            {
                "chat_id": chat_id,
                "video": file_id,
                "caption": caption,
                "supports_streaming": True
            }
        )

    if result.get("ok"):

        message = result["result"]

        message_id = message["message_id"]

        threading.Thread(
            target=delete_later,
            args=(
                chat_id,
                message_id
            ),
            daemon=True
        ).start()

    return result


def delete_later(chat_id, message_id):

    time.sleep(30)

    delete_message(
        chat_id,
        message_id
    )


def send_selected_type(
    chat_id,
    episode,
    file_type
):

    files = episode.get(
        "files"
    ) or []

    selected = []

    for item in files:

        if not isinstance(item, dict):

            continue

        if item.get(
            "file_type",
            "نامشخص"
        ) == file_type:

            selected.append(
                item
            )

    if not selected:

        send_message(
            chat_id,
            "❌ فایلی برای این نوع پیدا نشد."
        )

        return

    for item in selected:

        send_file(
            chat_id,
            item
        )


# =========================================================
# ADMIN LINKS
# =========================================================

def send_episode_links(
    chat_id,
    episode
):

    links = get_type_links(
        episode
    )

    if not links:

        send_message(
            chat_id,
            "❌ لینکی برای این قسمت ساخته نشد."
        )

        return

    series = episode.get(
        "series_name",
        ""
    )

    number = episode.get(
        "episode_number",
        ""
    )

    text = (
        f"✅ قسمت {number} ذخیره شد.\n\n"
        f"🪴 سریال: {series}\n\n"
    )

    for file_type, code in links.items():

        link = (
            f"https://t.me/"
            f"{BOT_USERNAME}"
            f"?start={code}"
        )

        text += (
            f"🎬 {file_type}\n"
            f"{link}\n\n"
        )

    send_message(
        chat_id,
        text
    )


# =========================================================
# SAVE EPISODE
# =========================================================

def save_episode(
    series,
    episode_number,
    file_id,
    file_type,
    telegram_type,
    caption
):

    key = (
        f"{series}__"
        f"{episode_number}"
    )

    episode = get_episode_by_key(
        key
    )

    new_file = {
        "file_id": file_id,
        "type": telegram_type,
        "file_type": file_type,
        "caption": caption
    }

    if episode:

        files = episode.get(
            "files"
        ) or []

        files.append(
            new_file
        )

        episode["files"] = files

        episode = ensure_type_codes(
            episode
        )

        db_patch(
            "episodes",
            {
                "id": f"eq.{episode['id']}"
            },
            {
                "files": episode["files"]
            }
        )

        return episode

    start_code = unique_code()

    new_file["type_code"] = unique_code()

    data = {
        "episode_key": key,
        "series_name": series,
        "episode_number": episode_number,
        "files": [
            new_file
        ],
        "start_code": start_code
    }

    result = db_insert(
        "episodes",
        data
    )

    if result:

        return result[0]

    return None


# =========================================================
# DELETE EPISODE
# =========================================================

def delete_episode(series, number):

    key = f"{series}__{number}"

    return db_delete(
        "episodes",
        {
            "episode_key": f"eq.{key}"
        }
    )


def delete_all():

    rows = get_all_episodes()

    for row in rows:

        db_delete(
            "episodes",
            {
                "id": f"eq.{row['id']}"
            }
        )


# =========================================================
# ADMIN CAPTION PARSER
# =========================================================

def parse_caption(caption):

    if not caption:

        return None

    series_match = re.search(
        r"سریال\s*[«\"]?(.+?)[»\"]?\s*$",
        caption,
        re.MULTILINE
    )

    if not series_match:

        series_match = re.search(
            r"سریال\s*[«\"]?(.+?)[»\"]?",
            caption
        )

    episode_match = re.search(
        r"قسمت\s*[:：]?\s*(\d+)",
        caption
    )

    if not episode_match:

        return None

    episode_number = int(
        episode_match.group(1)
    )

    series = None

    lines = caption.splitlines()

    for line in lines:

        if "سریال" in line:

            series = re.sub(
                r".*?سریال\s*",
                "",
                line
            )

            series = series.strip(
                " «»\"'"
            )

            break

    if not series:

        return None

    file_type = "نامشخص"

    if "زبان اصلی" in caption:

        file_type = "زبان اصلی"

    elif "زیرنویس فوری" in caption:

        file_type = "زیرنویس فوری"

    elif "زیرنویس مووی باز" in caption:

        file_type = "زیرنویس مووی باز"

    return (
        series,
        episode_number,
        file_type
    )


# =========================================================
# HANDLE MESSAGE
# =========================================================

def handle_message(message):

    if not message:

        return

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    user = message.get(
        "from",
        {}
    )

    user_id = user.get(
        "id"
    )

    text = message.get(
        "text",
        ""
    )

    # =====================================================
    # /START
    # =====================================================

    if text.startswith("/start"):

        parts = text.split(
            maxsplit=1
        )

        if len(parts) < 2:

            send_message(
                chat_id,
                "سلام 👋"
            )

            return

        code = parts[1].strip()

        episode, file_type = (
            get_episode_by_type_code(
                code
            )
        )

        if not episode:

            send_message(
                chat_id,
                "❌ لینک فایل معتبر نیست یا قسمت پیدا نشد."
            )

            return

        episode = ensure_type_codes(
            episode
        )

        if not file_type:

            file_type = "همه"

        set_pending(
            user_id,
            episode["episode_key"],
            file_type
        )

        if all_channels_joined(
            user_id
        ):

            show_posts_message(
                chat_id
            )

        else:

            show_join_message(
                chat_id
            )

        return

    # =====================================================
    # ADMIN COMMANDS
    # =====================================================

    if user_id == ADMIN_ID:

        if text == "/testdb":

            result = db_get(
                "episodes",
                {
                    "select": "id",
                    "limit": "1"
                }
            )

            send_message(
                chat_id,
                f"✅ اتصال ربات به Supabase برقرار است.\n\n"
                f"نتیجه: {result}"
            )

            return

        if text == "/sponsors":

            sponsors = get_sponsors()

            if not sponsors:

                send_message(
                    chat_id,
                    "❌ هیچ اسپانسری ثبت نشده."
                )

                return

            out = "📢 اسپانسرها:\n\n"

            for s in sponsors:

                out += (
                    f"ID: {s['id']}\n"
                    f"{s['title']}\n"
                    f"{s['url']}\n\n"
                )

            send_message(
                chat_id,
                out
            )

            return

        if text.startswith(
            "/remove_sponsor"
        ):

            parts = text.split()

            if len(parts) != 2:

                send_message(
                    chat_id,
                    "فرمت:\n/remove_sponsor ID"
                )

                return

            try:

                sponsor_id = int(
                    parts[1]
                )

                if remove_sponsor(
                    sponsor_id
                ):

                    send_message(
                        chat_id,
                        "✅ اسپانسر حذف شد."
                    )

                else:

                    send_message(
                        chat_id,
                        "❌ حذف انجام نشد."
                    )

            except:

                send_message(
                    chat_id,
                    "❌ ID اشتباه است."
                )

            return

        if text.startswith(
            "/add_sponsor"
        ):

            raw = text.replace(
                "/add_sponsor",
                "",
                1
            ).strip()

            parts = [
                x.strip()
                for x in raw.split("|")
            ]

            if len(parts) != 3:

                send_message(
                    chat_id,
                    "فرمت درست:\n\n"
                    "/add_sponsor @channel | نام کانال | https://t.me/channel"
                )

                return

            sponsor_chat = parts[0]

            title = parts[1]

            url = parts[2]

            result = add_sponsor(
                sponsor_chat,
                title,
                url
            )

            if result:

                send_message(
                    chat_id,
                    "✅ اسپانسر با موفقیت ذخیره شد."
                )

            else:

                send_message(
                    chat_id,
                    "❌ ذخیره اسپانسر انجام نشد."
                )

            return

        if text.startswith(
            "/delete_episode"
        ):

            raw = text.replace(
                "/delete_episode",
                "",
                1
            ).strip()

            parts = [
                x.strip()
                for x in raw.split("|")
            ]

            if len(parts) != 2:

                send_message(
                    chat_id,
                    "فرمت:\n"
                    "/delete_episode اسم سریال | شماره قسمت"
                )

                return

            series = parts[0]

            try:

                number = int(
                    parts[1]
                )

            except:

                send_message(
                    chat_id,
                    "❌ شماره قسمت اشتباه است."
                )

                return

            if delete_episode(
                series,
                number
            ):

                send_message(
                    chat_id,
                    "✅ قسمت حذف شد."
                )

            else:

                send_message(
                    chat_id,
                    "❌ حذف انجام نشد."
                )

            return

        if text == "/delete_all":

            delete_all()

            send_message(
                chat_id,
                "✅ همه قسمت‌ها حذف شدند."
            )

            return

    # =====================================================
    # ADMIN FILE UPLOAD
    # =====================================================

    if user_id == ADMIN_ID:

        video = message.get(
            "video"
        )

        document = message.get(
            "document"
        )

        if video or document:

            caption = message.get(
                "caption",
                ""
            )

            parsed = parse_caption(
                caption
            )

            if not parsed:

                send_message(
                    chat_id,
                    "❌ کپشن قابل شناسایی نیست.\n\n"
                    "مثال:\n"
                    "🪴 سریال «بالا پایین استانبول»\n"
                    "🪷 قسمت : 14\n"
                    "⚡ زیرنویس فوری\n"
                    "🎍 کیفیت : 1080"
                )

                return

            series, episode_number, file_type = (
                parsed
            )

            if video:

                file_id = video["file_id"]

                telegram_type = "video"

            else:

                file_id = document["file_id"]

                telegram_type = "document"

            episode = save_episode(
                series,
                episode_number,
                file_id,
                file_type,
                telegram_type,
                caption
            )

            if episode:

                send_message(
                    chat_id,
                    "✅ فایل با موفقیت ذخیره شد."
                )

                send_episode_links(
                    chat_id,
                    episode
                )

            else:

                send_message(
                    chat_id,
                    "❌ ذخیره فایل انجام نشد."
                )

            return


# =========================================================
# CALLBACK
# =========================================================

def handle_callback(callback):

    data = callback.get(
        "data",
        ""
    )

    user_id = callback["from"]["id"]

    message = callback.get(
        "message"
    )

    if not message:

        return

    chat_id = message["chat"]["id"]

    message_id = message["message_id"]

    # =====================================================
    # CHECK JOIN
    # =====================================================

    if data == "check_join":

        if not all_channels_joined(
            user_id
        ):

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": "❌ هنوز عضویت همه کانال‌ها تأیید نشده.",
                    "show_alert": True
                }
            )

            return

        # جواب دکمه
        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id": callback["id"],
                "text": "✅ عضویت تأیید شد."
            }
        )

        # =================================================
        # پیام عضویت کاملاً حذف شود
        # =================================================

        delete_message(
            chat_id,
            message_id
        )

        # =================================================
        # مرحله ری‌اکشن
        # =================================================

        show_posts_message(
            chat_id
        )

        return

    # =====================================================
    # DONE POSTS
    # =====================================================

    if data == "done_posts":

        pending = get_pending(
            user_id
        )

        if not pending:

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": "❌ درخواست فعال پیدا نشد.",
                    "show_alert": True
                }
            )

            return

        if not all_channels_joined(
            user_id
        ):

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": "❌ عضویت کانال‌ها کامل نیست.",
                    "show_alert": True
                }
            )

            return

        stored = pending.get(
            "episode_key",
            ""
        )

        if "|||" in stored:

            episode_key, file_type = (
                stored.split(
                    "|||",
                    1
                )
            )

        else:

            episode_key = stored

            file_type = "همه"

        episode = get_episode_by_key(
            episode_key
        )

        if not episode:

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": "❌ قسمت پیدا نشد.",
                    "show_alert": True
                }
            )

            return

        # =================================================
        # تأیید موفق
        # =================================================

        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id": callback["id"],
                "text": "✅ تأیید شد."
            }
        )

        # پیام ری‌اکشن کاملاً حذف شود
        delete_message(
            chat_id,
            message_id
        )

        # pending حذف شود
        delete_pending(
            user_id
        )

        # =================================================
        # ارسال فایل
        # =================================================

        if file_type == "همه":

            send_selected_type(
                chat_id,
                episode,
                "نامشخص"
            )

        else:

            send_selected_type(
                chat_id,
                episode,
                file_type
            )

        return


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return "Bot is running."


@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    try:

        update = request.get_json(
            force=True
        )

        if not update:

            return "OK"

        if "message" in update:

            handle_message(
                update["message"]
            )

        elif "callback_query" in update:

            handle_callback(
                update["callback_query"]
            )

    except Exception as e:

        print(
            "WEBHOOK ERROR:",
            e
        )

    return "OK"


# =========================================================
# SET WEBHOOK
# =========================================================

def setup_webhook():

    result = telegram(
        "setWebhook",
        {
            "url": WEBHOOK_URL,
            "allowed_updates": [
                "message",
                "channel_post",
                "callback_query"
            ],
            "max_connections": 40
        }
    )

    print(
        "WEBHOOK:",
        result
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    print("BOT STARTING...")

    setup_webhook()

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                10000
            )
        )
    )
