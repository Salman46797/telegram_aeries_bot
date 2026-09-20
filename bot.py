import os
import re
import json
import time
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, request
from supabase import create_client


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

ADMIN_ID = 5648301086

BOT_USERNAME = "Seryyaltorki_bot"

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

RENDER_URL = "https://telegram-aeries-bot.onrender.com"

DELETE_AFTER = 30

app = Flask(__name__)

session = requests.Session()

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# =========================================================
# TELEGRAM API
# =========================================================

def telegram(method, data=None):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:

        response = session.post(
            url,
            data=data or {},
            timeout=25
        )

        result = response.json()

        print(
            "TELEGRAM:",
            method,
            result
        )

        return result

    except Exception as e:

        print(
            "Telegram error:",
            repr(e)
        )

        return None


def send_message(
    chat_id,
    text,
    reply_markup=None
):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:

        data["reply_markup"] = json.dumps(
            reply_markup,
            ensure_ascii=False
        )

    return telegram(
        "sendMessage",
        data
    )


def delete_message(
    chat_id,
    message_id
):

    return telegram(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


def answer_callback(
    callback_id,
    text=None
):

    data = {
        "callback_query_id": callback_id
    }

    if text:
        data["text"] = text

    return telegram(
        "answerCallbackQuery",
        data
    )


# =========================================================
# SUPABASE - EPISODES
# =========================================================

def get_episode(
    episode_key
):

    try:

        result = (
            supabase
            .table("episodes")
            .select("*")
            .eq(
                "episode_key",
                episode_key
            )
            .limit(1)
            .execute()
        )

        if result.data:

            return result.data[0]

        return None

    except Exception as e:

        print(
            "get_episode error:",
            repr(e)
        )

        return None


def episode_exists(
    episode_key
):

    return get_episode(
        episode_key
    ) is not None


def normalize_files(
    value
):

    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, dict):

        if "files" in value:
            return normalize_files(
                value["files"]
            )

        return [value]

    if isinstance(value, str):

        try:

            decoded = json.loads(
                value
            )

            return normalize_files(
                decoded
            )

        except Exception:

            return []

    return []


def get_episode_files(
    episode_key
):

    row = get_episode(
        episode_key
    )

    if not row:
        return []

    return normalize_files(
        row.get("files")
    )


def save_episode(
    episode_key,
    files
):

    try:

        result = (
            supabase
            .table("episodes")
            .upsert(
                {
                    "episode_key": episode_key,
                    "files": files
                },
                on_conflict="episode_key"
            )
            .execute()
        )

        print(
            "SAVE EPISODE:",
            result.data
        )

        return True

    except Exception as e:

        print(
            "save_episode error:",
            repr(e)
        )

        return False


def delete_episode_from_db(
    episode_key
):

    try:

        (
            supabase
            .table("episodes")
            .delete()
            .eq(
                "episode_key",
                episode_key
            )
            .execute()
        )

        return True

    except Exception as e:

        print(
            "delete_episode error:",
            repr(e)
        )

        return False


def delete_all_episodes():

    try:

        (
            supabase
            .table("episodes")
            .delete()
            .neq(
                "episode_key",
                ""
            )
            .execute()
        )

        return True

    except Exception as e:

        print(
            "delete_all_episodes error:",
            repr(e)
        )

        return False


def count_episodes():

    try:

        result = (
            supabase
            .table("episodes")
            .select(
                "episode_key",
                count="exact"
            )
            .execute()
        )

        return result.count or 0

    except Exception as e:

        print(
            "count error:",
            repr(e)
        )

        return 0


# =========================================================
# SUPABASE - PENDING
# =========================================================

def set_pending(
    user_id,
    episode_key
):

    try:

        (
            supabase
            .table("pending")
            .upsert(
                {
                    "user_id": str(user_id),
                    "episode_key": episode_key
                },
                on_conflict="user_id"
            )
            .execute()
        )

        return True

    except Exception as e:

        print(
            "set_pending error:",
            repr(e)
        )

        return False


def get_pending(
    user_id
):

    try:

        result = (
            supabase
            .table("pending")
            .select("episode_key")
            .eq(
                "user_id",
                str(user_id)
            )
            .limit(1)
            .execute()
        )

        if result.data:

            return result.data[0].get(
                "episode_key"
            )

        return None

    except Exception as e:

        print(
            "get_pending error:",
            repr(e)
        )

        return None


def clear_pending(
    user_id
):

    try:

        (
            supabase
            .table("pending")
            .delete()
            .eq(
                "user_id",
                str(user_id)
            )
            .execute()
        )

        return True

    except Exception as e:

        print(
            "clear_pending error:",
            repr(e)
        )

        return False


def clear_all_pending():

    try:

        (
            supabase
            .table("pending")
            .delete()
            .neq(
                "user_id",
                ""
            )
            .execute()
        )

        return True

    except Exception as e:

        print(
            "clear_all_pending error:",
            repr(e)
        )

        return False


# =========================================================
# SPONSORS
# =========================================================

sponsor_cache = []
sponsor_cache_time = 0

SPONSOR_CACHE_SECONDS = 60


def load_sponsors(
    force=False
):

    global sponsor_cache
    global sponsor_cache_time

    now = time.time()

    if (
        not force
        and sponsor_cache
        and now - sponsor_cache_time
        < SPONSOR_CACHE_SECONDS
    ):

        return sponsor_cache

    try:

        result = (
            supabase
            .table("sponsors")
            .select("*")
            .order(
                "id"
            )
            .execute()
        )

        sponsor_cache = result.data or []

        sponsor_cache_time = now

        return sponsor_cache

    except Exception as e:

        print(
            "load_sponsors error:",
            repr(e)
        )

        return sponsor_cache


def add_sponsor_db(
    chat_id,
    title,
    url
):

    global sponsor_cache

    try:

        (
            supabase
            .table("sponsors")
            .insert(
                {
                    "chat_id": chat_id,
                    "title": title,
                    "url": url
                }
            )
            .execute()
        )

        sponsor_cache = []

        return True

    except Exception as e:

        print(
            "add sponsor error:",
            repr(e)
        )

        return False


def remove_sponsor_db(
    index
):

    global sponsor_cache

    sponsors = load_sponsors(
        force=True
    )

    if (
        index < 0
        or index >= len(sponsors)
    ):

        return False

    sponsor_id = sponsors[index]["id"]

    try:

        (
            supabase
            .table("sponsors")
            .delete()
            .eq(
                "id",
                sponsor_id
            )
            .execute()
        )

        sponsor_cache = []

        return True

    except Exception as e:

        print(
            "remove sponsor error:",
            repr(e)
        )

        return False


# =========================================================
# MEMBERSHIP
# =========================================================

def is_member(
    chat_id,
    user_id
):

    try:

        result = telegram(
            "getChatMember",
            {
                "chat_id": chat_id,
                "user_id": user_id
            }
        )

        if not result:
            return False

        if not result.get("ok"):
            return False

        status = (
            result
            .get("result", {})
            .get("status")
        )

        return status in [
            "creator",
            "administrator",
            "member"
        ]

    except Exception as e:

        print(
            "membership error:",
            repr(e)
        )

        return False


def check_all_sponsors(
    user_id
):

    # کانال اصلی

    if not is_member(
        CHANNEL_ID,
        user_id
    ):

        return False

    # اسپانسرها

    sponsors = load_sponsors()

    for sponsor in sponsors:

        if not is_member(
            sponsor["chat_id"],
            user_id
        ):

            return False

    return True


# =========================================================
# KEYBOARDS
# =========================================================

def membership_keyboard():

    rows = []

    # کانال اصلی

    rows.append([
        {
            "text": "📺 کانال اصلی",
            "url": CHANNEL_URL
        }
    ])

    sponsors = load_sponsors()

    for sponsor in sponsors:

        rows.append([
            {
                "text": sponsor["title"],
                "url": sponsor["url"]
            }
        ])

    rows.append([
        {
            "text": "عضو شدم ✅",
            "callback_data": "check_join"
        }
    ])

    return {
        "inline_keyboard": rows
    }


def reaction_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "📺 رفتن به کانال",
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
    chat_id
):

    text = (
        "❤️ برای دریافت فایل موردنظرت، "
        "۵ پست آخر این کانال رو ری‌اکشن (❤️) بزن 👇\n\n"
        f"{CHANNEL_URL}\n\n"
        "بعد از انجام، روی «انجام شد ✅» بزن."
    )

    return send_message(
        chat_id,
        text,
        reaction_keyboard()
    )


# =========================================================
# CAPTION PARSING
# =========================================================

def extract_episode_number(
    caption
):

    patterns = [

        r"🪷\s*قسمت\s*[:：]?\s*(\d+)",

        r"قسمت\s*[:：]?\s*(\d+)",

        r"episode\s*[:：]?\s*(\d+)",

        r"ep\s*[:：]?\s*(\d+)"

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            caption,
            re.IGNORECASE
        )

        if match:

            return int(
                match.group(1)
            )

    return None


def extract_series_name(
    caption
):

    patterns = [

        r"🪴\s*سریال\s*[«\"](.*?)[»\"]",

        r"سریال\s*[«\"](.*?)[»\"]",

        r"🪴\s*سریال\s*[:：]?\s*(.+)",

        r"سریال\s*[:：]?\s*(.+)"

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            caption,
            re.IGNORECASE
        )

        if match:

            name = match.group(1).strip()

            name = name.split(
                "\n"
            )[0].strip()

            return name

    return None


def extract_subtitle_type(
    caption
):

    match = re.search(
        r"🫧\s*(.+)",
        caption
    )

    if match:

        return match.group(1).strip()

    return ""


# =========================================================
# EPISODE KEY
# =========================================================

def make_series_key(
    series_name
):

    series_hash = hashlib.sha1(
        series_name
        .strip()
        .lower()
        .encode("utf-8")
    ).hexdigest()[:10]

    return "s" + series_hash


def make_episode_key(
    series_name,
    episode_number
):

    return (
        "ep_"
        + make_series_key(
            series_name
        )
        + "_"
        + str(episode_number)
    )


# =========================================================
# CHANNEL POSTS
# =========================================================

def store_channel_post(
    message_id
):

    try:

        (
            supabase
            .table("channel_posts")
            .upsert(
                {
                    "message_id": message_id
                },
                on_conflict="message_id"
            )
            .execute()
        )

        # فقط ۵ پست آخر را نگه می‌داریم

        result = (
            supabase
            .table("channel_posts")
            .select("message_id,created_at")
            .order(
                "created_at",
                desc=True
            )
            .execute()
        )

        posts = result.data or []

        if len(posts) > 5:

            for old in posts[5:]:

                try:

                    (
                        supabase
                        .table("channel_posts")
                        .delete()
                        .eq(
                            "message_id",
                            old["message_id"]
                        )
                        .execute()
                    )

                except Exception:
                    pass

        return True

    except Exception as e:

        print(
            "store_channel_post error:",
            repr(e)
        )

        return False


def get_last_five_posts():

    try:

        result = (
            supabase
            .table("channel_posts")
            .select("message_id")
            .order(
                "created_at",
                desc=True
            )
            .limit(5)
            .execute()
        )

        return [
            x["message_id"]
            for x in (
                result.data or []
            )
        ]

    except Exception as e:

        print(
            "get_last_five_posts error:",
            repr(e)
        )

        return []


def store_reaction(
    user_id,
    message_id,
    reacted=True
):

    try:

        (
            supabase
            .table("reactions")
            .upsert(
                {
                    "user_id": int(user_id),
                    "message_id": int(message_id),
                    "reacted": reacted
                },
                on_conflict="user_id,message_id"
            )
            .execute()
        )

        return True

    except Exception as e:

        print(
            "store_reaction error:",
            repr(e)
        )

        return False


def has_five_reactions(
    user_id
):

    posts = get_last_five_posts()

    if len(posts) < 5:

        return False

    try:

        result = (
            supabase
            .table("reactions")
            .select("message_id")
            .eq(
                "user_id",
                int(user_id)
            )
            .eq(
                "reacted",
                True
            )
            .in_(
                "message_id",
                posts
            )
            .execute()
        )

        reacted_ids = {
            int(x["message_id"])
            for x in (
                result.data or []
            )
        }

        return all(
            post_id in reacted_ids
            for post_id in posts
        )

    except Exception as e:

        print(
            "has_five_reactions error:",
            repr(e)
        )

        return False


# =========================================================
# SEND EPISODE
# =========================================================

def send_one_file(
    chat_id,
    file_data
):

    file_id = file_data.get(
        "file_id"
    )

    file_type = file_data.get(
        "type",
        "video"
    )

    caption = file_data.get(
        "caption",
        ""
    )

    if not file_id:
        return None

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

    if not result:
        return None

    if not result.get("ok"):
        return None

    return (
        result
        .get("result", {})
        .get("message_id")
    )


def delete_after_delay(
    chat_id,
    message_ids
):

    time.sleep(
        DELETE_AFTER
    )

    for message_id in message_ids:

        try:

            delete_message(
                chat_id,
                message_id
            )

        except Exception as e:

            print(
                "delete media error:",
                repr(e)
            )


def send_episode(
    chat_id,
    episode_key
):

    files = get_episode_files(
        episode_key
    )

    if not files:

        send_message(
            chat_id,
            "❌ فایل این قسمت پیدا نشد."
        )

        return

    sent_ids = []

    # ارسال سریع و همزمان

    with ThreadPoolExecutor(
        max_workers=4
    ) as executor:

        futures = []

        for file_data in files:

            futures.append(
                executor.submit(
                    send_one_file,
                    chat_id,
                    file_data
                )
            )

        for future in futures:

            try:

                message_id = future.result()

                if message_id:

                    sent_ids.append(
                        message_id
                    )

            except Exception as e:

                print(
                    "send file error:",
                    repr(e)
                )

   if not sent_ids:
       
send_message(
    chat_id,
    "❌ ارسال فایل ناموفق بود."
            )

        return

    send_message(
        chat_id,
        "⏳ فایل‌ها تا ۳۰ ثانیه قابل دریافت هستند.\n"
        "بعد از حذف، لینک دریافت مجدد برات میاد."
    )

    # حذف فایل‌ها بعد از ۳۰ ثانیه

    threading.Thread(
        target=delete_after_delay,
        args=(
            chat_id,
            sent_ids
        ),
        daemon=True
    ).start()

    # ساخت لینک دریافت دوباره

    def send_again_link():

        time.sleep(
            DELETE_AFTER + 1
        )

        link = (
            f"https://t.me/"
            f"{BOT_USERNAME}"
            f"?start={episode_key}"
        )

        send_message(
            chat_id,
            "🔄 برای دریافت دوباره فایل، "
            "روی لینک زیر بزن:\n\n"
            f"{link}"
        )

    threading.Thread(
        target=send_again_link,
        daemon=True
    ).start()


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = request.get_json(
        silent=True
    )

    if not update:
        return "OK"

    try:

        print(
            "UPDATE:",
            update
        )

        # =================================================
        # CHANNEL POST
        # =================================================

        if "channel_post" in update:

            post = update[
                "channel_post"
            ]

            chat = post.get(
                "chat",
                {}
            )

            username = chat.get(
                "username"
            )

            if (
                username
                and username.lower()
                == CHANNEL_ID.lstrip("@").lower()
            ):

                message_id = post.get(
                    "message_id"
                )

                if message_id:

                    store_channel_post(
                        message_id
                    )

            return "OK"

        # =================================================
        # MESSAGE REACTION
        # =================================================

        if "message_reaction" in update:

            reaction = update[
                "message_reaction"
            ]

            user = reaction.get(
                "user"
            )

            message_id = reaction.get(
                "message_id"
            )

            if user and message_id:

                user_id = user.get(
                    "id"
                )

                reactions = reaction.get(
                    "new_reaction",
                    []
                )

                has_heart = False

                for item in reactions:

                    emoji = item.get(
                        "emoji"
                    )

                    if emoji == "❤️":

                        has_heart = True
                        break

                store_reaction(
                    user_id,
                    message_id,
                    has_heart
                )

            return "OK"

        # =================================================
        # CALLBACK QUERY
        # =================================================

        if "callback_query" in update:

            callback = update[
                "callback_query"
            ]

            callback_id = callback.get(
                "id"
            )

            data = callback.get(
                "data",
                ""
            )

            user = callback.get(
                "from",
                {}
            )

            user_id = user.get(
                "id"
            )

            message = callback.get(
                "message",
                {}
            )

            chat = message.get(
                "chat",
                {}
            )

            chat_id = chat.get(
                "id"
            )

            if callback_id:

                answer_callback(
                    callback_id
                )

            # ---------------------------------------------
            # بررسی عضویت
            # ---------------------------------------------

            if data == "check_join":

                pending = get_pending(
                    user_id
                )

                if not pending:

                    send_message(
                        chat_id,
                        "❌ درخواست قسمت پیدا نشد.\n"
                        "دوباره لینک قسمت رو باز کن."
                    )

                    return "OK"

                if not check_all_sponsors(
                    user_id
                ):

                    send_message(
                        chat_id,
                        "❌ هنوز عضو همه کانال‌ها نشدی.",
                        membership_keyboard()
                    )

                    return "OK"

                send_reaction_message(
                    chat_id
                )

                return "OK"

            # ---------------------------------------------
            # بررسی ری‌اکشن
            # ---------------------------------------------

            if data == "reaction_done":

                pending = get_pending(
                    user_id
                )

                if not pending:

                    send_message(
                        chat_id,
                        "❌ درخواست قسمت پیدا نشد."
                    )

                    return "OK"

                if not check_all_sponsors(
                    user_id
                ):

                    send_message(
                        chat_id,
                        "❌ هنوز عضو همه کانال‌ها نشدی.",
                        membership_keyboard()
                    )

                    return "OK"

                if not has_five_reactions(
                    user_id
                ):

                    send_message(
                        chat_id,
                        "❌ هنوز هر ۵ پست آخر رو ❤️ ری‌اکشن نکردی.\n\n"
                        "بعد از ری‌اکشن زدن دوباره "
                        "«انجام شد ✅» رو بزن."
                    )

                    return "OK"

                clear_pending(
                    user_id
                )

                send_episode(
                    chat_id,
                    pending
                )

                return "OK"

            return "OK"

        # =================================================
        # MESSAGE
        # =================================================

        if "message" in update:

            message = update[
                "message"
            ]

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

            # =================================================
            # START
            # =================================================

            if text.startswith(
                "/start"
            ):

                parts = text.split(
                    maxsplit=1
                )

                payload = ""

                if len(parts) > 1:

                    payload = parts[1].strip()

                if not payload:

                    send_message(
                        chat_id,
                        "👋 سلام!\n\n"
                        "برای دریافت قسمت، "
                        "لینک قسمت موردنظر رو باز کن."
                    )

                    return "OK"

                if not payload.startswith(
                    "ep_"
                ):

                    send_message(
                        chat_id,
                        "❌ لینک قسمت نامعتبره."
                    )

                    return "OK"

                if not episode_exists(
                    payload
                ):

                    send_message(
                        chat_id,
                        "❌ این قسمت پیدا نشد یا حذف شده."
                    )

                    return "OK"

                set_pending(
                    user_id,
                    payload
                )

                if not check_all_sponsors(
                    user_id
                ):

                    send_message(
                        chat_id,
                        "📢 برای دریافت قسمت، "
                        "اول عضو کانال‌های زیر شو:",
                        membership_keyboard()
                    )

                    return "OK"

                send_reaction_message(
                    chat_id
                )

                return "OK"

            # =================================================
            # ADMIN
            # =================================================

            if user_id == ADMIN_ID:

                # ---------------------------------------------
                # DELETE ALL
                # ---------------------------------------------

                if text == "/delete_all":

                    delete_all_episodes()

                    clear_all_pending()

                    send_message(
                        chat_id,
                        "✅ همه قسمت‌های ذخیره‌شده حذف شدند."
                    )

                    return "OK"

                # ---------------------------------------------
                # EPISODES
                # ---------------------------------------------

                if text == "/episodes":

                    count = count_episodes()

                    send_message(
                        chat_id,
                        f"📚 تعداد قسمت‌های ذخیره‌شده: {count}"
                    )

                    return "OK"

                # ---------------------------------------------
                # SPONSORS
                # ---------------------------------------------

                if text == "/sponsors":

                    sponsors = load_sponsors(
                        force=True
                    )

                    if not sponsors:

                        send_message(
                            chat_id,
                            "📭 هیچ اسپانسری ثبت نشده."
                        )

                        return "OK"

                    lines = [
                        "📢 لیست اسپانسرها:"
                    ]

                    for i, sponsor in enumerate(
                        sponsors,
                        start=1
                    ):

                        lines.append(
                            f"\n{i}. {sponsor['title']}\n"
                            f"🆔 {sponsor['chat_id']}\n"
                            f"🔗 {sponsor['url']}"
                        )

                    send_message(
                        chat_id,
                        "\n".join(lines)
                    )

                    return "OK"

                # ---------------------------------------------
                # ADD SPONSOR
                # ---------------------------------------------

                if text.startswith(
                    "/add_sponsor"
                ):

                    parts = text.split(
                        maxsplit=3
                    )

                    if len(parts) < 4:

                        send_message(
                            chat_id,
                            "فرمت درست:\n\n"
                            "/add_sponsor @channel نام https://t.me/channel"
                        )

                        return "OK"

                    sponsor_chat_id = parts[1]
                    sponsor_title = parts[2]
                    sponsor_url = parts[3]

                    if add_sponsor_db(
                        sponsor_chat_id,
                        sponsor_title,
                        sponsor_url
                    ):

                        send_message(
                            chat_id,
                            "✅ اسپانسر اضافه شد."
                        )

                    else:

                        send_message(
                            chat_id,
                            "❌ اضافه کردن اسپانسر ناموفق بود."
                        )

                    return "OK"

                # ---------------------------------------------
                # REMOVE SPONSOR
                # ---------------------------------------------

                if text.startswith(
                    "/remove_sponsor"
                ):

                    parts = text.split()

                    if len(parts) != 2:

                        send_message(
                            chat_id,
                            "فرمت درست:\n\n"
                            "/remove_sponsor شماره"
                        )

                        return "OK"

                    try:

                        index = int(
                            parts[1]
                        ) - 1

                    except ValueError:

                        send_message(
                            chat_id,
                            "❌ شماره نامعتبره."
                        )

                        return "OK"

                    if remove_sponsor_db(
                        index
                    ):

                        send_message(
                            chat_id,
                            "✅ اسپانسر حذف شد."
                        )

                    else:

                        send_message(
                            chat_id,
                            "❌ اسپانسر پیدا نشد."
                        )

                    return "OK"

                # ---------------------------------------------
                # DELETE EPISODE
                # ---------------------------------------------

                if text.startswith(
                    "/delete_episode"
                ):

                    parts = text.split()

                    if len(parts) != 2:

                        send_message(
                            chat_id,
                            "فرمت درست:\n\n"
                            "/delete_episode ep_..."
                        )

                        return "OK"

                    if delete_episode_from_db(
                        parts[1]
                    ):

                        send_message(
                            chat_id,
                            "✅ قسمت حذف شد."
                        )

                    else:

                        send_message(
                            chat_id,
                            "❌ حذف قسمت ناموفق بود."
                        )

                    return "OK"

                # =================================================
                # ADMIN FILE UPLOAD
                # =================================================

                video = message.get(
                    "video"
                )

                document = message.get(
                    "document"
                )

                if video or document:

                    caption = (
                        message.get(
                            "caption"
                        )
                        or ""
                    ).strip()

                    if not caption:

                        send_message(
                            chat_id,
                            "❌ کپشن فایل خالیه."
                        )

                        return "OK"

                    series_name = extract_series_name(
                        caption
                    )

                    episode_number = extract_episode_number(
                        caption
                    )

                    if not series_name:

                        send_message(
                            chat_id,
                            "❌ اسم سریال پیدا نشد.\n\n"
                            "مثال:\n"
                            "🪴 سریال «عشق و تخت»"
                        )

                        return "OK"

                    if episode_number is None:

                        send_message(
                            chat_id,
                            "❌ شماره قسمت پیدا نشد.\n\n"
                            "مثال:\n"
                            "🪷 قسمت : 2"
                        )

                        return "OK"

                    if video:

                        file_id = video.get(
                            "file_id"
                        )

                        file_type = "video"

                    else:

                        file_id = document.get(
                            "file_id"
                        )

                        file_type = "document"

                    episode_key = make_episode_key(
                        series_name,
                        episode_number
                    )

                    files = get_episode_files(
                        episode_key
                    )

                    if len(files) >= 4:

                        send_message(
                            chat_id,
                            "❌ این قسمت قبلاً ۴ فایل دارد."
                        )

                        return "OK"

                    new_file = {
                        "file_id": file_id,
                        "type": file_type,
                        "caption": caption,
                        "subtitle": extract_subtitle_type(
                            caption
                        )
                    }

                    files.append(
                        new_file
                    )

                    if save_episode(
                        episode_key,
                        files
                    ):

                        link = (
                            f"https://t.me/"
                            f"{BOT_USERNAME}"
                            f"?start={episode_key}"
                        )

                        send_message(
                            chat_id,
                            "✅ فایل ذخیره شد.\n\n"
                            f"📺 سریال: {series_name}\n"
                            f"🪷 قسمت: {episode_number}\n"
                            f"📦 فایل: {len(files)}/4\n\n"
                            f"🔗 لینک دریافت:\n"
                            f"{link}"
                        )

                    else:

                        send_message(
                            chat_id,
                            "❌ ذخیره در Supabase ناموفق بود."
                        )

                    return "OK"

            return "OK"

        return "OK"

    except Exception as e:

        print(
            "WEBHOOK ERROR:",
            repr(e)
        )

        return "OK"


# =========================================================
# HOME
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return "Bot is running"


# =========================================================
# SET WEBHOOK
# =========================================================

def setup_webhook():

    webhook_url = (
        RENDER_URL
        + "/webhook"
    )

    result = telegram(
        "setWebhook",
        {
            "url": webhook_url,
            "allowed_updates": json.dumps([
                "message",
                "callback_query",
                "channel_post",
                "message_reaction"
            ])
        }
    )

    print(
        "SET WEBHOOK RESULT:",
        result
    )


# =========================================================
# START
# ====================================================
setup_webhook()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                10000
            )
        )
    )
