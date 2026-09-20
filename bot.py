import os
import json
import re
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

app = Flask(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is missing")

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# =========================================================
# HTTP SESSION
# =========================================================

session = requests.Session()

TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
)


def telegram(method, data=None):
    try:
        response = session.post(
            f"{TELEGRAM_API}/{method}",
            data=data or {},
            timeout=12
        )

        return response.json()

    except Exception as e:
        print("Telegram error:", e)
        return {}


# =========================================================
# TELEGRAM HELPERS
# =========================================================

def send_message(chat_id, text, reply_markup=None):

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


def delete_message(chat_id, message_id):

    return telegram(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


def answer_callback(callback_id):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
    )


# =========================================================
# SUPABASE - EPISODES
# =========================================================

def get_episode(episode_key):

    try:

        result = (
            supabase
            .table("episodes")
            .select("episode_key, files")
            .eq("episode_key", episode_key)
            .maybe_single()
            .execute()
        )

        if result.data:
            return result.data.get("files", [])

    except Exception as e:
        print("Get episode error:", e)

    return []


def save_episode(episode_key, files):

    try:

        supabase.table("episodes").upsert({
            "episode_key": episode_key,
            "files": files
        }).execute()

        return True

    except Exception as e:
        print("Save episode error:", e)
        return False


def episode_exists(episode_key):

    try:

        result = (
            supabase
            .table("episodes")
            .select("episode_key")
            .eq("episode_key", episode_key)
            .maybe_single()
            .execute()
        )

        return bool(result.data)

    except Exception as e:
        print("Episode exists error:", e)

        return False


def delete_episode_from_db(episode_key):

    try:

        supabase \
            .table("episodes") \
            .delete() \
            .eq("episode_key", episode_key) \
            .execute()

        return True

    except Exception as e:
        print("Delete episode error:", e)

        return False


def delete_all_episodes():

    try:

        supabase \
            .table("episodes") \
            .delete() \
            .neq("episode_key", "") \
            .execute()

        return True

    except Exception as e:
        print("Delete all episodes error:", e)

        return False


def count_episodes():

    try:

        result = (
            supabase
            .table("episodes")
            .select("episode_key")
            .execute()
        )

        return len(result.data or [])

    except Exception as e:
        print("Count episodes error:", e)

        return 0


# =========================================================
# SUPABASE - PENDING
# =========================================================

def set_pending(user_id, episode_key):

    try:

        supabase.table("pending").upsert({
            "user_id": str(user_id),
            "episode_key": episode_key
        }).execute()

        return True

    except Exception as e:
        print("Set pending error:", e)

        return False


def get_pending(user_id):

    try:

        result = (
            supabase
            .table("pending")
            .select("episode_key")
            .eq("user_id", str(user_id))
            .maybe_single()
            .execute()
        )

        if result.data:
            return result.data.get("episode_key")

    except Exception as e:
        print("Get pending error:", e)

    return None


def clear_pending(user_id):

    try:

        supabase \
            .table("pending") \
            .delete() \
            .eq("user_id", str(user_id)) \
            .execute()

    except Exception as e:
        print("Clear pending error:", e)


def clear_all_pending():

    try:

        supabase \
            .table("pending") \
            .delete() \
            .neq("user_id", "") \
            .execute()

    except Exception as e:
        print("Clear pending error:", e)


# =========================================================
# SUPABASE - SPONSORS
# =========================================================

_sponsors_cache = []
_sponsors_cache_time = 0
_sponsors_lock = threading.Lock()

SPONSOR_CACHE_SECONDS = 60


def load_sponsors(force=False):

    global _sponsors_cache
    global _sponsors_cache_time

    now = time.time()

    if (
        not force
        and now - _sponsors_cache_time
        < SPONSOR_CACHE_SECONDS
    ):
        return _sponsors_cache

    with _sponsors_lock:

        now = time.time()

        if (
            not force
            and now - _sponsors_cache_time
            < SPONSOR_CACHE_SECONDS
        ):
            return _sponsors_cache

        try:

            result = (
                supabase
                .table("sponsors")
                .select("id,chat_id,title,url")
                .order("id")
                .execute()
            )

            _sponsors_cache = result.data or []
            _sponsors_cache_time = time.time()

        except Exception as e:

            print("Sponsors error:", e)

    return _sponsors_cache


def add_sponsor_db(
    channel_id,
    title,
    url
):

    global _sponsors_cache_time

    try:

        supabase.table("sponsors").insert({
            "chat_id": channel_id,
            "title": title,
            "url": url
        }).execute()

        _sponsors_cache_time = 0

        return True

    except Exception as e:

        print("Add sponsor error:", e)

        return False


def remove_sponsor_db(index):

    global _sponsors_cache_time

    sponsors = load_sponsors()

    if not 0 <= index < len(sponsors):
        return False

    sponsor_id = sponsors[index]["id"]

    try:

        supabase \
            .table("sponsors") \
            .delete() \
            .eq("id", sponsor_id) \
            .execute()

        _sponsors_cache_time = 0

        return True

    except Exception as e:

        print("Remove sponsor error:", e)

        return False


# =========================================================
# MEMBERSHIP
# =========================================================

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

    return status in [
        "member",
        "administrator",
        "creator"
    ]


def check_all_sponsors(user_id):

    # Main channel
    if not is_member(
        user_id,
        CHANNEL_ID
    ):
        return False

    # Sponsor channels
    sponsors = load_sponsors()

    for sponsor in sponsors:

        if not is_member(
            user_id,
            sponsor["chat_id"]
        ):
            return False

    return True


# =========================================================
# KEYBOARDS
# =========================================================

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


# =========================================================
# CAPTION PARSING
# =========================================================

def extract_episode_number(caption):

    match = re.search(
        r"(?:قسمت|episode|ep)"
        r"\s*[:：\-]?\s*(\d+)",
        caption,
        re.IGNORECASE
    )

    if match:
        return match.group(1)

    return None


def extract_series_name(caption):

    match = re.search(
        r"سریال\s*[«\"]?"
        r"\s*(.+?)"
        r"\s*[»\"]?\s*$",
        caption,
        re.MULTILINE
    )

    if match:

        name = match.group(1).strip()

        name = name.strip(
            "«»\"' "
        )

        if name:
            return name

    # Old format
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


# =========================================================
# EPISODE KEYS
# =========================================================

def make_series_key(series_name):

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


# =========================================================
# REAL REACTION SYSTEM
# =========================================================

def normalize_emoji(emoji):

    if not emoji:
        return ""

    return emoji.replace(
        "\ufe0f",
        ""
    )


def store_channel_post(message_id):

    try:

        supabase.table("channel_posts").upsert({
            "message_id": int(message_id)
        }).execute()

        # فقط ۵ پست آخر را نگه می‌داریم
        result = (
            supabase
            .table("channel_posts")
            .select("message_id")
            .order("created_at", desc=True)
            .execute()
        )

        posts = result.data or []

        if len(posts) > 5:

            old_ids = [
                x["message_id"]
                for x in posts[5:]
            ]

            for old_id in old_ids:

                supabase \
                    .table("channel_posts") \
                    .delete() \
                    .eq(
                        "message_id",
                        old_id
                    ) \
                    .execute()

                supabase \
                    .table("reactions") \
                    .delete() \
                    .eq(
                        "message_id",
                        old_id
                    ) \
                    .execute()

    except Exception as e:

        print(
            "Store channel post error:",
            e
        )


def store_reaction(
    user_id,
    message_id,
    reacted
):

    try:

        supabase.table("reactions").upsert({
            "user_id": int(user_id),
            "message_id": int(message_id),
            "reacted": bool(reacted)
        }).execute()

    except Exception as e:

        print(
            "Store reaction error:",
            e
        )


def has_five_reactions(user_id):

    try:

        posts_result = (
            supabase
            .table("channel_posts")
            .select("message_id")
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )

        posts = posts_result.data or []

        if len(posts) < 5:
            return False

        post_ids = [
            int(x["message_id"])
            for x in posts
        ]

        result = (
            supabase
            .table("reactions")
            .select("message_id,reacted")
            .eq("user_id", int(user_id))
            .in_("message_id", post_ids)
            .execute()
        )

        reacted_ids = {
            int(x["message_id"])
            for x in (result.data or [])
            if x.get("reacted") is True
        }

        return all(
            post_id in reacted_ids
            for post_id in post_ids
        )

    except Exception as e:

        print(
            "Check reactions error:",
            e
        )

        return False


# =========================================================
# SEND EPISODE
# =========================================================

def send_one_file(
    chat_id,
    item
):

    file_id = item.get("file_id")
    file_type = item.get("type")
    caption = item.get("caption", "")

    if not file_id:
        return None

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

        return None

    if result.get("ok"):

        return result["result"]["message_id"]

    return None


def send_episode(
    chat_id,
    episode_key
):

    files = normalize_episode(
        get_episode(episode_key)
    )

    if not files:

        send_message(
            chat_id,
            "❌ این قسمت حذف شده یا وجود ندارد."
        )

        return

    # ارسال همزمان فایل‌ها
    sent_ids = []

    with ThreadPoolExecutor(
        max_workers=min(
            4,
            len(files)
        )
    ) as executor:

        futures = [
            executor.submit(
                send_one_file,
                chat_id,
                item
            )
            for item in files
        ]

        for future in futures:

            try:

                message_id = future.result()

                if message_id:
                    sent_ids.append(
                        message_id
                    )

            except Exception as e:

                print(
                    "Send file error:",
                    e
                )

    if sent_ids:

        threading.Thread(
            target=delete_after_30_seconds,
            args=(
                chat_id,
                sent_ids
            ),
            daemon=True
        ).start()


def delete_after_30_seconds(
    chat_id,
    message_ids
):

    time.sleep(30)

    # حذف همزمان
    with ThreadPoolExecutor(
        max_workers=min(
            4,
            len(message_ids)
        )
    ) as executor:

        for message_id in message_ids:

            executor.submit(
                delete_message,
                chat_id,
                message_id
            )

    send_message(
        chat_id,
        "⏰ زمان دانلود تمام شد.\n\n"
        "🔗 دانلود مجدد:\n"
        "https://t.me/"
        + BOT_USERNAME
    )


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

            channel_post = update[
                "channel_post"
            ]

            chat = channel_post.get(
                "chat",
                {}
            )

            if (
                chat.get("username")
                == CHANNEL_ID.replace(
                    "@",
                    ""
                )
            ):

                store_channel_post(
                    channel_post["message_id"]
                )

            return "OK"

        # =================================================
        # MESSAGE REACTION
        # =================================================

        if "message_reaction" in update:

            reaction = update[
                "message_reaction"
            ]

            chat = reaction.get(
                "chat",
                {}
            )

            if (
                chat.get("username")
                == CHANNEL_ID.replace(
                    "@",
                    ""
                )
            ):

                user = reaction.get(
                    "user"
                )

                if not user:
                    return "OK"

                user_id = user["id"]

                message_id = reaction[
                    "message_id"
                ]

                new_reactions = reaction.get(
                    "new_reaction",
                    []
                )

                heart = False

                for item in new_reactions:

                    if item.get("type") == "emoji":

                        emoji = normalize_emoji(
                            item.get(
                                "emoji",
                                ""
                            )
                        )

                        if emoji == "❤":

                            heart = True

                store_reaction(
                    user_id,
                    message_id,
                    heart
                )

            return "OK"

        # =================================================
        # CALLBACK
   
