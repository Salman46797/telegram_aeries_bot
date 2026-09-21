import os
import re
import random
import string
import threading
import time
import requests
from concurrent.futures import ThreadPoolExecutor
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
# CACHE
# =========================================================

EPISODES_CACHE = None
SPONSORS_CACHE = None

CACHE_LOCK = threading.Lock()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(method, data=None):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:

        r = requests.post(
            url,
            json=data or {},
            timeout=20
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

    return telegram(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


# =========================================================
# SUPABASE
# =========================================================

def db_get(table, params=None):

    try:

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params=params or {},
            timeout=20
        )

        if r.status_code != 200:

            print(
                "DB GET ERROR:",
                r.status_code,
                r.text
            )

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
            timeout=20
        )

        print(
            "DB INSERT:",
            r.status_code
        )

        if r.status_code in [200, 201]:

            return r.json()

        print(r.text)

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
            timeout=20
        )

        if r.status_code not in [200, 204]:

            print(
                "DB PATCH ERROR:",
                r.status_code,
                r.text
            )

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
            timeout=20
        )

        return r.status_code in [200, 204]

    except Exception as e:

        print("DB DELETE ERROR:", e)

        return False


# =========================================================
# CACHE HELPERS
# =========================================================

def refresh_episodes_cache():

    global EPISODES_CACHE

    rows = db_get(
        "episodes",
        {
            "select": "*",
            "order": "id.asc"
        }
    )

    with CACHE_LOCK:

        EPISODES_CACHE = rows

    return rows


def get_episodes():

    global EPISODES_CACHE

    with CACHE_LOCK:

        if EPISODES_CACHE is not None:

            return EPISODES_CACHE

    return refresh_episodes_cache()


def refresh_sponsors_cache():

    global SPONSORS_CACHE

    rows = db_get(
        "sponsors",
        {
            "select": "*",
            "order": "id.asc"
        }
    )

    with CACHE_LOCK:

        SPONSORS_CACHE = rows

    return rows


def get_sponsors():

    global SPONSORS_CACHE

    with CACHE_LOCK:

        if SPONSORS_CACHE is not None:

            return SPONSORS_CACHE

    return refresh_sponsors_cache()


# =========================================================
# RANDOM CODE
# =========================================================

def random_code(length=6):

    chars = (
        string.ascii_letters +
        string.digits
    )

    return "".join(
        random.choice(chars)
        for _ in range(length)
    )


def code_exists(code):

    for episode in get_episodes():

        if episode.get(
            "start_code"
        ) == code:

            return True

        for item in episode.get(
            "files"
        ) or []:

            if isinstance(item, dict):

                if item.get(
                    "type_code"
                ) == code:

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

    for episode in get_episodes():

        if episode.get(
            "episode_key"
        ) == key:

            return episode

    return None


def ensure_type_codes(episode):

    files = episode.get(
        "files"
    ) or []

    changed = False

    used_codes = set()

    for ep in get_episodes():

        if ep.get("start_code"):

            used_codes.add(
                ep["start_code"]
            )

        for item in ep.get(
            "files"
        ) or []:

            if isinstance(item, dict):

                if item.get(
                    "type_code"
                ):

                    used_codes.add(
                        item["type_code"]
                    )

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

            grouped[file_type] = (
                item["type_code"]
            )

    for file_type in grouped:

        if not grouped[file_type]:

            while True:

                code = random_code()

                if code not in used_codes:

                    break

            grouped[file_type] = code

            used_codes.add(code)

            changed = True

    for item in files:

        if not isinstance(item, dict):

            continue

        file_type = item.get(
            "file_type",
            "نامشخص"
        )

        code = grouped[file_type]

        if item.get(
            "type_code"
        ) != code:

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

        with CACHE_LOCK:

            global EPISODES_CACHE

            if EPISODES_CACHE is not None:

                for i, ep in enumerate(
                    EPISODES_CACHE
                ):

                    if ep.get("id") == episode.get("id"):

                        EPISODES_CACHE[i] = episode

                        break

    return episode


def get_episode_by_type_code(code):

    for episode in get_episodes():

        if episode.get(
            "start_code"
        ) == code:

            return episode, None

        for item in episode.get(
            "files"
        ) or []:

            if not isinstance(
                item,
                dict
            ):

                continue

            if item.get(
                "type_code"
            ) == code:

                return (
                    episode,
                    item.get(
                        "file_type",
                        "نامشخص"
                    )
                )

    return None, None


def get_type_links(episode):

    episode = ensure_type_codes(
        episode
    )

    result = {}

    for item in episode.get(
        "files"
    ) or []:

        if not isinstance(
            item,
            dict
        ):

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

def set_pending(
    user_id,
    episode_key,
    file_type
):

    value = (
        f"{episode_key}|||"
        f"{file_type}"
    )

    old = db_get(
        "pending",
        {
            "user_id": f"eq.{user_id}",
            "limit": "1"
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

def add_sponsor(
    chat_id,
    title,
    url
):

    result = db_insert(
        "sponsors",
        {
            "chat_id": chat_id,
            "title": title,
            "url": url
        }
    )

    refresh_sponsors_cache()

    return result


def remove_sponsor(
    sponsor_id
):

    result = db_delete(
        "sponsors",
        {
            "id": f"eq.{sponsor_id}"
        }
    )

    refresh_sponsors_cache()

    return result


# =========================================================
# MEMBERSHIP
# =========================================================

def check_one_channel(
    channel,
    user_id
):

    result = telegram(
        "getChatMember",
        {
            "chat_id": channel,
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

    channels = [
        CHANNEL_ID
    ]

    for sponsor in get_sponsors():

        channels.append(
            sponsor["chat_id"]
        )

    # چک کانال‌ها همزمان
    with ThreadPoolExecutor(
        max_workers=min(
            10,
            len(channels)
        )
    ) as executor:

        results = list(
            executor.map(
                lambda channel:
                    check_one_channel(
                        channel,
                        user_id
                    ),
                channels
            )
        )

    return all(results)


# =========================================================
# JOIN MESSAGE
# =========================================================

def show_join_message(
    chat_id
):

    sponsors = get_sponsors()

    buttons = []

    # کانال اصلی
    buttons.append(
        [
            {
                "text": "📢 کانال اصلی",
                "url": CHANNEL_URL
            }
        ]
    )

    # اسپانسرها
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
        "📣 برای استفاده از ربات و دریافت فایل :\n\n"
        "1️⃣ ابتدا عضو کانال های زیر بشید\n"
        "2️⃣ سپس رو دکمه عضو شدم کلیک کنید",
        {
            "inline_keyboard": buttons
        }
    )


# =========================================================
# REACTION MESSAGE
# =========================================================

def show_posts_message(
    chat_id
):

    return send_message(
        chat_id,
        "لطفا جهت دریافت فایل ابتدا 5 پست اخیر "
        "کانال @altiustuistsnbol را ری اکت بزنید "
        "و سپس برگردید و دکمه انجام دادم را کلیک کنید ♥️",
        {
            "inline_keyboard": [
                [
                    {
                        "text": "📢 رفتن به کانال",
                        "url": CHANNEL_URL
                    }
                ],
                [
                    {
                        "text": "انجام دادم ✅",
                        "callback_data": "done_posts"
                    }
                ]
            ]
        }
    )


# =========================================================
# SEND FILE
# =========================================================

def delete_later(
    chat_id,
    message_id
):

    time.sleep(30)

    delete_message(
        chat_id,
        message_id
    )


def send_file(
    chat_id,
    item
):

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

        message_id = result[
            "result"
        ]["message_id"]

        threading.Thread(
            target=delete_later,
            args=(
                chat_id,
                message_id
            ),
            daemon=True
        ).start()

    return result


def send_selected_type(
    chat_id,
    episode,
    file_type
):

    files = []

    for item in episode.get(
        "files"
    ) or []:

        if not isinstance(
            item,
            dict
        ):

            continue

        if item.get(
            "file_type",
            "نامشخص"
        ) == file_type:

            files.append(item)

    if not files:

        send_message(
            chat_id,
            "❌ فایلی برای این نوع پیدا نشد."
        )

        return

    # ارسال پشت سرهم برای حفظ ترتیب کیفیت‌ها
    for item in files:

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

        refresh_episodes_cache()

        return episode

    start_code = unique_code()

    new_file[
        "type_code"
    ] = unique_code()

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

        refresh_episodes_cache()

        return result[0]

    return None


# =========================================================
# DELETE EPISODE
# =========================================================

def delete_episode(
    series,
    number
):

    key = (
        f"{series}__"
        f"{number}"
    )

    result = db_delete(
        "episodes",
        {
            "episode_key": f"eq.{key}"
        }
    )

    refresh_episodes_cache()

    return result


def delete_all():

    result = db_delete(
        "episodes",
        {
            "id": "gt.0"
        }
    )

    refresh_episodes_cache()

    return result


# =========================================================
# PARSE CAPTION
# =========================================================

def parse_caption(
    caption
):

    if not caption:

        return None

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

    for line in caption.splitlines():

        if "سریال" in line:

            series = re.sub(
                r".*?سریال\s*",
                "",
                line
            ).strip(
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

def handle_message(
    message
):

    if not message:

        return

    chat_id = message.get(
        "chat",
        {}
    ).get(
        "id"
    )

    user_id = message.get(
        "from",
        {}
    ).get(
        "id"
    )

    text = message.get(
        "text",
        ""
    )

    # =====================================================
    # START
    # =====================================================

    if text.startswith(
        "/start"
    ):

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

        # اگر از قبل عضو همه کانال‌هاست
        # مستقیم برو مرحله ری‌اکشن
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
    # ADMIN
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
                "✅ اتصال ربات به Supabase برقرار است.\n\n"
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

            for sponsor in sponsors:

                out += (
                    f"ID: {sponsor['id']}\n"
                    f"{sponsor['title']}\n"
                    f"{sponsor['url']}\n\n"
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

            result = add_sponsor(
                parts[0],
                parts[1],
                parts[2]
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
                parts[0],
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
    # ADMIN FILE
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

                file_id = video[
                    "file_id"
                ]

                telegram_type = "video"

            else:

                file_id = document[
                    "file_id"
                ]

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

def handle_callback(
    callback
):

    data = callback.get(
        "data",
        ""
    )

    user_id = callback[
        "from"
    ]["id"]

    message = callback.get(
        "message"
    )

    if not message:

        return

    chat_id = message[
        "chat"
    ]["id"]

    message_id = message[
        "message_id"
    ]

    # =====================================================
    # CHECK JOIN
    # =====================================================

    if data == "check_join":

        # پاسخ سریع به دکمه
        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id": callback["id"],
                "text": "در حال بررسی عضویت..."
            }
        )

        if not all_channels_joined(
            user_id
        ):

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback["id"],
                    "text": "❌ هنوز عضویت کامل نیست.",
                    "show_alert": True
                }
            )

            return

        # تأیید شد
        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id": callback["id"],
                "text": "✅ عضویت تأیید شد."
            }
        )

        # پیام عضویت کاملاً حذف شود
        delete_message(
            chat_id,
            message_id
        )

        # مرحله ری‌اکشن
        show_posts_message(
            chat_id
        )

        return

    # =====================================================
    # DONE POSTS
    # =====================================================

    if data == "done_posts":

        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id": callback["id"],
                "text": "در حال آماده‌سازی فایل..."
            }
        )

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

        # دوباره عضویت بررسی شود
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

        # پیام ری‌اکشن حذف شود
        delete_message(
            chat_id,
            message_id
        )

        # pending حذف شود
        delete_pending(
            user_id
        )

        # ارسال فایل
        if file_type == "همه":

            files = episode.get(
                "files"
            ) or []

            types = []

            for item in files:

                if not isinstance(
                    item,
                    dict
                ):

                    continue

                ft = item.get(
                    "file_type",
                    "نامشخص"
                )

                if ft not in types:

                    types.append(ft)

            for ft in types:

                send_selected_type(
                    chat_id,
                    episode,
                    ft
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

    print(
        "BOT STARTING..."
    )

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
