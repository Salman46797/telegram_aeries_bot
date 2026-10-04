import os
import re
import time
import json
import hashlib
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, request

from supabase import create_client, Client


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()

# اگر SUPABASE_SERVICE_ROLE_KEY داشته باشی، برای خواندن دیتابیس
# از آن استفاده می‌شود؛ در غیر این صورت SUPABASE_KEY
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_KEY", "").strip()
)

MAIN_CHANNEL = "@altiustuistsnbol"
MAIN_CHANNEL_URL = "https://t.me/altiustuistsnbol"

BOT_USERNAME = "Seryyaltorki_bot"

ADMIN_ID = 5648301086

DELETE_AFTER = 30

PORT = int(os.getenv("PORT", "10000"))

CACHE_SYNC_SECONDS = 90


# =========================================================
# CHECK CONFIG
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL تنظیم نشده")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY یا SUPABASE_SERVICE_ROLE_KEY تنظیم نشده")


# =========================================================
# APP / SUPABASE
# =========================================================

app = Flask(__name__)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# =========================================================
# TELEGRAM
# =========================================================

TG = f"https://api.telegram.org/bot{BOT_TOKEN}"


def tg(method, data=None, timeout=30):
    try:
        r = requests.post(
            f"{TG}/{method}",
            json=data or {},
            timeout=timeout
        )

        try:
            result = r.json()
        except Exception:
            result = {
                "ok": False,
                "description": r.text
            }

        if not result.get("ok"):
            print("TELEGRAM ERROR:", method, result)

        return result

    except Exception as e:
        print("TELEGRAM REQUEST ERROR:", method, e)
        return {
            "ok": False,
            "description": str(e)
        }


# =========================================================
# GLOBAL CACHE
# =========================================================

EPISODES = {}
TYPE_INDEX = {}
SPONSORS = []

CACHE_LOCK = threading.RLock()
LAST_CACHE_SYNC = 0


# =========================================================
# PENDING
# =========================================================

PENDING = {}

PENDING_LOCK = threading.RLock()


# =========================================================
# BROADCAST STATE
# =========================================================

BROADCAST_MODE = {}

BROADCAST_LOCK = threading.RLock()


# =========================================================
# HELPERS
# =========================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value):
    if value is None:
        return ""

    value = str(value)

    value = value.replace("ي", "ی")
    value = value.replace("ى", "ی")
    value = value.replace("ك", "ک")
    value = value.replace("\u200c", "")
    value = value.replace("\u200f", "")
    value = value.replace("\u200e", "")
    value = value.strip()

    return value


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def stable_prefix(value):
    return hashlib.sha1(
        str(value).encode("utf-8")
    ).hexdigest()[:10]


def make_type_code(episode_key, file_type, index):
    return (
        f"t_{stable_prefix(episode_key)}_"
        f"{file_type}{index}"
    )


def episode_link(episode_key):
    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=ep_{episode_key}"
    )


def type_link(episode_key, file_type, index):
    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start={make_type_code(episode_key, file_type, index)}"
    )


def delete_message_later(chat_id, message_id):
    def worker():
        time.sleep(DELETE_AFTER)

        tg(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id
            }
        )

    threading.Thread(
        target=worker,
        daemon=True
    ).start()


def run_background(fn, *args, **kwargs):
    threading.Thread(
        target=fn,
        args=args,
        kwargs=kwargs,
        daemon=True
    ).start()


# =========================================================
# CAPTION ENTITIES
# =========================================================

def extract_caption(message):
    caption = message.get("caption") or ""

    entities = message.get("caption_entities")

    if not entities:
        entities = []

    return caption, entities


# =========================================================
# FILE EXTRACTION
# =========================================================

def extract_file(message):
    """
    خروجی:
    {
        type: video/document/audio/photo,
        file_id: ...,
        caption: ...,
        caption_entities: [...]
    }
    """

    caption, entities = extract_caption(message)

    if message.get("video"):
        return {
            "type": "video",
            "file_id": message["video"]["file_id"],
            "caption": caption,
            "caption_entities": entities
        }

    if message.get("document"):
        return {
            "type": "document",
            "file_id": message["document"]["file_id"],
            "caption": caption,
            "caption_entities": entities
        }

    if message.get("audio"):
        return {
            "type": "audio",
            "file_id": message["audio"]["file_id"],
            "caption": caption,
            "caption_entities": entities
        }

    if message.get("photo"):
        photo = message["photo"][-1]

        return {
            "type": "photo",
            "file_id": photo["file_id"],
            "caption": caption,
            "caption_entities": entities
        }

    return None


# =========================================================
# EPISODE PARSER
# =========================================================

def parse_series_episode(text):
    if not text:
        return None, None

    text = normalize_text(text)

    patterns = [
        r"سریال\s*[«\"“]?(.+?)[»\"”]?\s*[/\n|]\s*قسمت\s*[:：\-]?\s*(\d+)",
        r"سریال\s*[«\"“]?(.+?)[»\"”]?\s+قسمت\s*[:：\-]?\s*(\d+)",
        r"(.+?)\s*[/\n|]\s*قسمت\s*[:：\-]?\s*(\d+)"
    ]

    for pattern in patterns:
        m = re.search(
            pattern,
            text,
            flags=re.IGNORECASE | re.DOTALL
        )

        if m:
            series = normalize_text(m.group(1))
            episode_number = safe_int(m.group(2))

            if series:
                return series, episode_number

    return None, None


def make_episode_key(series_name, episode_number):
    clean = normalize_text(series_name)

    clean = re.sub(
        r"\s+",
        "_",
        clean
    )

    clean = re.sub(
        r"[^0-9A-Za-z_\-\u0600-\u06FF]",
        "",
        clean
    )

    return f"{clean}_{episode_number}"


# =========================================================
# SUPABASE HELPERS
# =========================================================

def db_get_all_episodes():
    try:
        result = (
            supabase
            .table("episodes")
            .select("*")
            .execute()
        )

        data = result.data or []

        print(
            f"[DB] episodes loaded: {len(data)}"
        )

        return data

    except Exception as e:
        print(
            "[DB] ERROR loading episodes:",
            repr(e)
        )

        return None


def db_get_episode_exact(episode_key):
    try:
        result = (
            supabase
            .table("episodes")
            .select("*")
            .eq("episode_key", episode_key)
            .limit(1)
            .execute()
        )

        data = result.data or []

        if data:
            print(
                "[DB] exact episode found:",
                episode_key
            )
            return data[0]

        print(
            "[DB] exact episode NOT found:",
            episode_key
        )

        return None

    except Exception as e:
        print(
            "[DB] exact lookup ERROR:",
            episode_key,
            repr(e)
        )

        return None


def db_get_episode_prefix(episode_key):
    """
    اگر لینک قدیمی به هر دلیلی ناقص شده باشد،
    با prefix هم امتحان می‌کنیم.
    """

    try:
        result = (
            supabase
            .table("episodes")
            .select("*")
            .like(
                "episode_key",
                f"{episode_key}%"
            )
            .limit(5)
            .execute()
        )

        data = result.data or []

        if len(data) == 1:
            print(
                "[DB] prefix episode found:",
                data[0].get("episode_key")
            )
            return data[0]

        if len(data) > 1:
            print(
                "[DB] prefix matched multiple episodes:",
                episode_key,
                len(data)
            )

        return None

    except Exception as e:
        print(
            "[DB] prefix lookup ERROR:",
            repr(e)
        )

        return None


def db_get_episode_by_series_number(series_name, episode_number):
    try:
        result = (
            supabase
            .table("episodes")
            .select("*")
            .eq("series_name", series_name)
            .eq("episode_number", episode_number)
            .limit(2)
            .execute()
        )

        data = result.data or []

        if len(data) == 1:
            return data[0]

        return None

    except Exception as e:
        print(
            "[DB] series/episode lookup ERROR:",
            repr(e)
        )

        return None


# =========================================================
# CACHE SYNC
# =========================================================

def sync_cache(force=False):
    global EPISODES
    global TYPE_INDEX
    global SPONSORS
    global LAST_CACHE_SYNC

    with CACHE_LOCK:

        if (
            not force
            and LAST_CACHE_SYNC
            and time.time() - LAST_CACHE_SYNC < CACHE_SYNC_SECONDS
        ):
            return True

        print("========== CACHE SYNC START ==========")

        episodes = db_get_all_episodes()

        if episodes is None:
            print(
                "CACHE SYNC FAILED: episodes SELECT failed"
            )
            return False

        new_episodes = {}
        new_type_index = {}

        for row in episodes:

            key = row.get("episode_key")

            if not key:
                continue

            files = row.get("files")

            if not isinstance(files, list):
                files = []

            row["files"] = files

            new_episodes[str(key)] = row

            for i, file_data in enumerate(files, start=1):

                if not isinstance(file_data, dict):
                    continue

                file_type = file_data.get("type")

                if not file_type:
                    continue

                code = make_type_code(
                    key,
                    file_type,
                    i
                )

                new_type_index[code] = (
                    str(key),
                    i - 1
                )

        # sponsors
        try:
            sponsor_result = (
                supabase
                .table("sponsors")
                .select("*")
                .execute()
            )

            new_sponsors = sponsor_result.data or []

        except Exception as e:
            print(
                "[DB] sponsors SELECT ERROR:",
                repr(e)
            )

            new_sponsors = []

        EPISODES = new_episodes
        TYPE_INDEX = new_type_index
        SPONSORS = new_sponsors

        LAST_CACHE_SYNC = time.time()

        print(
            "CACHE SYNC DONE:",
            len(EPISODES),
            "episodes |",
            len(TYPE_INDEX),
            "type links |",
            len(SPONSORS),
            "sponsors"
        )

        print("========== CACHE SYNC END ==========")

        return True


# =========================================================
# ROBUST EPISODE RESOLVER
# =========================================================

def resolve_episode(episode_key):
    """
    ترتیب:
    1. Cache exact
    2. DB exact
    3. DB prefix
    4. Full DB cache refresh
    5. دوباره cache
    """

    episode_key = str(episode_key).strip()

    print(
        "========== RESOLVE EPISODE =========="
    )

    print(
        "requested key:",
        repr(episode_key)
    )

    # 1
    with CACHE_LOCK:
        row = EPISODES.get(episode_key)

    if row:
        print(
            "FOUND IN CACHE:",
            episode_key
        )
        return row

    # 2
    row = db_get_episode_exact(
        episode_key
    )

    if row:
        with CACHE_LOCK:
            EPISODES[episode_key] = row

        print(
            "FOUND EXACT IN DATABASE"
        )

        return row

    # 3
    row = db_get_episode_prefix(
        episode_key
    )

    if row:
        real_key = row.get("episode_key")

        if real_key:
            with CACHE_LOCK:
                EPISODES[str(real_key)] = row

        print(
            "FOUND BY PREFIX:",
            real_key
        )

        return row

    # 4
    print(
        "Refreshing complete cache..."
    )

    sync_cache(
        force=True
    )

    # 5
    with CACHE_LOCK:
        row = EPISODES.get(episode_key)

    if row:
        print(
            "FOUND AFTER CACHE REFRESH"
        )
        return row

    print(
        "EPISODE NOT FOUND:",
        repr(episode_key)
    )

    print(
        "========== RESOLVE END =========="
    )

    return None


# =========================================================
# USER STORAGE
# =========================================================

def register_user(user):
    if not user:
        return

    user_id = user.get("id")

    if not user_id:
        return

    data = {
        "user_id": user_id,
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "username": user.get("username", ""),
        "last_seen": now_iso()
    }

    def worker():
        try:
            supabase.table(
                "bot_users"
            ).upsert(
                data,
                on_conflict="user_id"
            ).execute()

        except Exception as e:
            print(
                "[USER] register ERROR:",
                repr(e)
            )

    run_background(worker)


def mark_blocked(user_id):
    def worker():
        try:
            supabase.table(
                "bot_users"
            ).update(
                {
                    "is_blocked": True
                }
            ).eq(
                "user_id",
                user_id
            ).execute()

        except Exception as e:
            print(
                "[USER] block ERROR:",
                repr(e)
            )

    run_background(worker)


# =========================================================
# STATS
# =========================================================

def add_stat(
    event_type,
    user_id=None,
    episode_key=None,
    file_type=None,
    file_count=0
):
    data = {
        "event_type": event_type,
        "file_count": file_count,
        "created_at": now_iso()
    }

    if user_id is not None:
        data["user_id"] = user_id

    if episode_key:
        data["episode_key"] = episode_key

    if file_type:
        data["file_type"] = file_type

    def worker():
        try:
            supabase.table(
                "bot_stats"
            ).insert(data).execute()

        except Exception as e:
            print(
                "[STATS] ERROR:",
                repr(e)
            )

    run_background(worker)


# =========================================================
# MEMBERSHIP
# =========================================================

def is_member(user_id, channel):
    try:
        result = tg(
            "getChatMember",
            {
                "chat_id": channel,
                "user_id": user_id
            }
        )

        if not result.get("ok"):
            print(
                "MEMBERSHIP CHECK ERROR:",
                channel,
                result
            )
            return False

        status = (
            result
            .get("result", {})
            .get("status", "")
        )

        return status in (
            "creator",
            "administrator",
            "member"
        )

    except Exception as e:
        print(
            "MEMBERSHIP EXCEPTION:",
            repr(e)
        )

        return False


def get_required_channels():
    channels = [
        {
            "username": MAIN_CHANNEL,
            "title": "بالا پایین استانبول",
            "link": MAIN_CHANNEL_URL
        }
    ]

    with CACHE_LOCK:
        sponsors = list(SPONSORS)

    for sponsor in sponsors:

        username = (
            sponsor.get("channel_username")
            or sponsor.get("username")
            or sponsor.get("channel")
        )

        title = (
            sponsor.get("title")
            or sponsor.get("name")
            or username
        )

        link = (
            sponsor.get("link")
            or (
                f"https://t.me/"
                f"{str(username).lstrip('@')}"
            )
        )

        if username:
            channels.append(
                {
                    "username": username,
                    "title": title,
                    "link": link
                }
            )

    return channels


def check_membership(user_id):
    missing = []

    for channel in get_required_channels():

        if not is_member(
            user_id,
            channel["username"]
        ):
            missing.append(channel)

    return missing


# =========================================================
# INLINE KEYBOARDS
# =========================================================

def join_keyboard(missing):
    rows = []

    for channel in missing:
        rows.append(
            [
                {
                    "text": f"📢 عضویت در {channel['title']}",
                    "url": channel["link"]
                }
            ]
        )

    rows.append(
        [
            {
                "text": "✅ بررسی عضویت",
                "callback_data": "check_membership"
            }
        ]
    )

    return {
        "inline_keyboard": rows
    }


def reaction_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📢 مشاهده ۵ پست آخر",
                    "url": MAIN_CHANNEL_URL
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


# =========================================================
# SEND / EDIT
# =========================================================

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
        data["reply_markup"] = reply_markup

    return tg(
        "sendMessage",
        data
    )


def edit_message(
    chat_id,
    message_id,
    text,
    reply_markup=None
):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return tg(
        "editMessageText",
        data
    )


def answer_callback(callback_id, text=None):
    data = {
        "callback_query_id": callback_id
    }

    if text:
        data["text"] = text

    return tg(
        "answerCallbackQuery",
        data
    )


# =========================================================
# REACTION PROMPT
# =========================================================

REACTION_TEXT = """برای دریافت فایل، به ۵ پست آخر کانال @altiustuistsnbol ری‌اکشن ❤️ بزن.

بعد از انجام ری‌اکشن‌ها، روی «انجام شد ✅» بزن تا فایل برات ارسال بشه."""


def show_reaction_gate(chat_id, episode_key):
    with PENDING_LOCK:
        PENDING[chat_id] = {
            "episode_key": episode_key,
            "created_at": time.time()
        }

    send_message(
        chat_id,
        REACTION_TEXT,
        reaction_keyboard()
    )


# =========================================================
# FILE SENDING
# =========================================================

def send_file(chat_id, file_data):
    file_type = file_data.get("type")
    file_id = file_data.get("file_id")

    caption = file_data.get("caption", "")
    caption_entities = file_data.get(
        "caption_entities",
        []
    )

    payload = {
        "chat_id": chat_id,
        "caption": caption
    }

    if caption_entities:
        payload["caption_entities"] = caption_entities

    if file_type == "video":
        payload["video"] = file_id
        method = "sendVideo"

    elif file_type == "document":
        payload["document"] = file_id
        method = "sendDocument"

    elif file_type == "audio":
        payload["audio"] = file_id
        method = "sendAudio"

    elif file_type == "photo":
        payload["photo"] = file_id
        method = "sendPhoto"

    else:
        return {
            "ok": False
        }

    result = tg(
        method,
        payload
    )

    if result.get("ok"):
        message_id = (
            result
            .get("result", {})
            .get("message_id")
        )

        if message_id:
            delete_message_later(
                chat_id,
                message_id
            )

    return result


def deliver_episode(chat_id, episode):
    files = episode.get("files") or []

    if not files:
        send_message(
            chat_id,
            "❌ برای این قسمت هنوز فایلی ثبت نشده."
        )
        return

    key = episode.get("episode_key")

    add_stat(
        "download",
        user_id=chat_id,
        episode_key=key,
        file_count=len(files)
    )

    sent = 0

    for file_data in files:

        result = send_file(
            chat_id,
            file_data
        )

        if result.get("ok"):
            sent += 1

    warning = (
        "⚠️ فایل‌ها تا ۳۰ ثانیه دیگه حذف میشن.\n\n"
        "برای دریافت مجدد همین قسمت، دوباره روی لینک قسمت بزن."
    )

    send_message(
        chat_id,
        warning
    )

    add_stat(
        "files_sent",
        user_id=chat_id,
        episode_key=key,
        file_count=sent
    )


# =========================================================
# START HANDLER
# =========================================================

def handle_start(chat_id, user, parameter):
    register_user(user)

    user_id = user["id"]

    add_stat(
        "start",
        user_id=user_id
    )

    parameter = (parameter or "").strip()

    if not parameter:
        send_message(
            chat_id,
            "سلام 👋\n\n"
            "برای دریافت فایل، لینک قسمت موردنظرت رو باز کن."
        )
        return

    # =====================================================
    # TYPE LINK
    # =====================================================

    if parameter.startswith("t_"):

        with CACHE_LOCK:
            type_info = TYPE_INDEX.get(parameter)

        if not type_info:
            print(
                "TYPE LINK NOT IN CACHE:",
                parameter
            )

            sync_cache(
                force=True
            )

            with CACHE_LOCK:
                type_info = TYPE_INDEX.get(parameter)

        if not type_info:
            send_message(
                chat_id,
                "❌ لینک فایل قدیمی یا نامعتبر است."
            )
            return

        episode_key, file_index = type_info

        episode = resolve_episode(
            episode_key
        )

        if not episode:
            send_message(
                chat_id,
                "❌ این قسمت پیدا نشد یا حذف شده."
            )
            return

        files = episode.get("files") or []

        if (
            file_index < 0
            or file_index >= len(files)
        ):
            send_message(
                chat_id,
                "❌ فایل این لینک پیدا نشد."
            )
            return

        missing = check_membership(
            user_id
        )

        if missing:
            send_message(
                chat_id,
                "برای دریافت فایل، اول در کانال‌های زیر عضو شو:",
                join_keyboard(missing)
            )
            return

        with PENDING_LOCK:
            PENDING[chat_id] = {
                "episode_key": episode_key,
                "file_indexes": [file_index],
                "created_at": time.time()
            }

        show_reaction_gate(
            chat_id,
            episode_key
        )

        return

    # =====================================================
    # EPISODE LINK
    # =====================================================

    if parameter.startswith("ep_"):

        episode_key = parameter[3:]

        print(
            "START EPISODE KEY:",
            repr(episode_key)
        )

        episode = resolve_episode(
            episode_key
        )

        if not episode:
            send_message(
                chat_id,
                "❌ این قسمت پیدا نشد یا حذف شده."
            )
            return

        missing = check_membership(
            user_id
        )

        if missing:

            send_message(
                chat_id,
                "برای دریافت فایل، اول در کانال‌های زیر عضو شو:",
                join_keyboard(missing)
            )

            with PENDING_LOCK:
                PENDING[chat_id] = {
                    "episode_key": episode.get(
                        "episode_key"
                    ),
                    "created_at": time.time()
                }

            return

        show_reaction_gate(
            chat_id,
            episode.get("episode_key")
        )

        return

    send_message(
        chat_id,
        "❌ لینک نامعتبر است."
    )


# =========================================================
# CALLBACK HANDLER
# =========================================================

def handle_callback(query):
    callback_id = query.get("id")

    from_user = query.get("from") or {}
    user_id = from_user.get("id")

    message = query.get("message") or {}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    data = query.get("data", "")

    if not user_id or not chat_id:
        return

    answer_callback(
        callback_id
    )

    if data == "check_membership":

        missing = check_membership(
            user_id
        )

        if missing:
            edit_message(
                chat_id,
                message.get("message_id"),
                "❌ هنوز در همه کانال‌های لازم عضو نشدی.",
                join_keyboard(missing)
            )
            return

        with PENDING_LOCK:
            pending = PENDING.get(chat_id)

        if not pending:
            send_message(
                chat_id,
                "❌ درخواست قبلی پیدا نشد. دوباره لینک قسمت رو باز کن."
            )
            return

        show_reaction_gate(
            chat_id,
            pending["episode_key"]
        )

        return

    if data == "reaction_done":

        with PENDING_LOCK:
            pending = PENDING.get(chat_id)

        if not pending:
            send_message(
                chat_id,
                "❌ درخواست منقضی شده. دوباره لینک قسمت رو باز کن."
            )
            return

        missing = check_membership(
            user_id
        )

        if missing:
            send_message(
                chat_id,
                "❌ هنوز در همه کانال‌های لازم عضو نیستی.",
                join_keyboard(missing)
            )
            return

        # -------------------------------------------------
        # IMPORTANT:
        # Telegram API اجازه بررسی مطمئن ری‌اکشن اینجا را
        # نمی‌دهد؛ این مرحله طبق طراحی فعلی symbolic است.
        # -------------------------------------------------

        episode = resolve_episode(
            pending["episode_key"]
        )

        if not episode:
            send_message(
                chat_id,
                "❌ این قسمت پیدا نشد یا حذف شده."
            )
            return

        with PENDING_LOCK:
            PENDING.pop(
                chat_id,
                None
            )

        if pending.get("file_indexes"):

            files = episode.get("files") or []

            for index in pending["file_indexes"]:

                if (
                    0 <= index < len(files)
                ):
                    send_file(
                        chat_id,
                        files[index]
                    )

            send_message(
                chat_id,
                "⚠️ فایل تا ۳۰ ثانیه دیگه حذف میشه.\n\n"
                "برای دریافت مجدد، دوباره روی لینک قسمت بزن."
            )

        else:
            deliver_episode(
                chat_id,
                episode
            )

        return


# =========================================================
# SAVE EPISODE
# =========================================================

def save_episode_file(message):
    file_data = extract_file(
        message
    )

    if not file_data:
        return

    caption = file_data.get(
        "caption",
        ""
    )

    series_name, episode_number = (
        parse_series_episode(caption)
    )

    if not series_name or not episode_number:

        print(
            "CAPTION PARSE FAILED:",
            repr(caption)
        )

        send_message(
            ADMIN_ID,
            "❌ از کپشن نتونستم نام سریال و قسمت رو پیدا کنم.\n\n"
            "فرمت نمونه:\n"
            "سریال «نام سریال» / قسمت : 14"
        )

        return

    episode_key = make_episode_key(
        series_name,
        episode_number
    )

    print(
        "SAVE EPISODE:",
        episode_key
    )

    existing = db_get_episode_exact(
        episode_key
    )

    if existing:
        files = existing.get("files") or []

        if not isinstance(files, list):
            files = []

    else:
        files = []

    files.append(
        file_data
    )

    row = {
        "episode_key": episode_key,
        "series_name": series_name,
        "episode_number": episode_number,
        "files": files
    }

    try:

        result = (
            supabase
            .table("episodes")
            .upsert(
                row,
                on_conflict="episode_key"
            )
            .execute()
        )

        print(
            "[DB] episode saved:",
            result.data
        )

        sync_cache(
            force=True
        )

        send_message(
            ADMIN_ID,
            "✅ قسمت ذخیره شد.\n\n"
            f"🎬 {series_name}\n"
            f"🔢 قسمت: {episode_number}\n"
            f"📁 تعداد فایل‌ها: {len(files)}\n\n"
            f"🔗 {episode_link(episode_key)}"
        )

    except Exception as e:

        print(
            "[DB] SAVE ERROR:",
            repr(e)
        )

        send_message(
            ADMIN_ID,
            "❌ ذخیره قسمت انجام نشد.\n\n"
            f"{e}"
        )


# =========================================================
# SPONSORS
# =========================================================

def add_sponsor_command(text):
    parts = [
        p.strip()
        for p in text.split("|")
    ]

    if len(parts) != 3:
        return False

    username = parts[0]
    title = parts[1]
    link = parts[2]

    if not username.startswith("@"):
        username = "@" + username

    data = {
        "channel_username": username,
        "title": title,
        "link": link
    }

    try:
        result = (
            supabase
            .table("sponsors")
            .insert(data)
            .execute()
        )

        sync_cache(
            force=True
        )

        send_message(
            ADMIN_ID,
            "✅ اسپانسر اضافه شد."
        )

        return True

    except Exception as e:

        print(
            "[SPONSOR] ADD ERROR:",
            repr(e)
        )

        send_message(
            ADMIN_ID,
            f"❌ ذخیره اسپانسر انجام نشد.\n\n{e}"
        )

        return False


def send_sponsors(chat_id):
    with CACHE_LOCK:
        sponsors = list(SPONSORS)

    if not sponsors:
        send_message(
            chat_id,
            "📢 هیچ اسپانسری ثبت نشده."
        )
        return

    lines = [
        "📢 لیست اسپانسرها:",
        ""
    ]

    for sponsor in sponsors:

        sid = sponsor.get("id", "?")

        title = (
            sponsor.get("title")
            or sponsor.get("name")
            or "بدون نام"
        )

        username = (
            sponsor.get("channel_username")
            or sponsor.get("username")
            or sponsor.get("channel")
            or "-"
        )

        lines.append(
            f"🆔 {sid}\n"
            f"📢 {title}\n"
            f"🔗 {username}\n"
        )

    send_message(
        chat_id,
        "\n".join(lines)
    )


def remove_sponsor(sponsor_id):
    try:

        (
            supabase
            .table("sponsors")
            .delete()
            .eq(
                "id",
                safe_int(sponsor_id)
            )
            .execute()
        )

        sync_cache(
            force=True
        )

        send_message(
            ADMIN_ID,
            "✅ اسپانسر حذف شد."
        )

    except Exception as e:

        print(
            "[SPONSOR] REMOVE ERROR:",
            repr(e)
        )

        send_message(
            ADMIN_ID,
            f"❌ حذف اسپانسر انجام نشد.\n\n{e}"
        )


# =========================================================
# ADMIN PANEL
# =========================================================

def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🎬 مدیریت قسمت‌ها",
                    "callback_data": "admin_episodes"
                },
                {
                    "text": "📢 اسپانسرها",
                    "callback_data": "admin_sponsors"
                }
            ],
            [
                {
                    "text": "📢 پیام همگانی",
                    "callback_data": "admin_broadcast"
                },
                {
                    "text": "📊 آمار پیشرفته",
                    "callback_data": "admin_stats"
                }
            ],
            [
                {
                    "text": "📋 لیست قسمت‌ها",
                    "callback_data": "admin_list"
                },
                {
                    "text": "🗑 حذف قسمت",
                    "callback_data": "admin_delete"
                }
            ],
            [
                {
                    "text": "🗑 حذف همه قسمت‌ها",
                    "callback_data": "admin_delete_all"
                }
            ],
            [
                {
                    "text": "🔄 سینک دیتابیس",
                    "callback_data": "admin_sync"
                },
                {
                    "text": "⚡ وضعیت ربات",
                    "callback_data": "admin_status"
                }
            ]
        ]
    }


def show_admin_panel(chat_id):
    send_message(
        chat_id,
        "🛠 پنل مدیریت ربات\n\n"
        "یکی از گزینه‌های زیر رو انتخاب کن:",
        admin_keyboard()
    )


# =========================================================
# ADMIN CALLBACK
# =========================================================

def handle_admin_callback(query):
    user_id = (
        query.get("from") or {}
    ).get("id")

    if user_id != ADMIN_ID:
        return

    data = query.get("data", "")
    message = query.get("message") or {}

    chat_id = (
        message.get("chat") or {}
    ).get("id")

    callback_id = query.get("id")

    answer_callback(
        callback_id
    )

    if data == "admin_sync":

        ok = sync_cache(
            force=True
        )

        if ok:
            send_message(
                chat_id,
                f"✅ سینک انجام شد.\n\n"
                f"قسمت‌ها: {len(EPISODES)}\n"
                f"لینک فایل‌ها: {len(TYPE_INDEX)}\n"
                f"اسپانسرها: {len(SPONSORS)}"
            )
        else:
            send_message(
                chat_id,
                "❌ سینک دیتابیس انجام نشد. لاگ Render رو بررسی کن."
            )

        return

    if data == "admin_sponsors":
        send_sponsors(
            chat_id
        )
        return

    if data == "admin_broadcast":

        BROADCAST_MODE[chat_id] = True

        send_message(
            chat_id,
            "📢 پیام همگانی رو بفرست.\n\n"
            "متن، عکس، ویدیو یا فایل.\n\n"
            "برای لغو:\n"
            "/cancel_broadcast"
        )

        return

    if data == "admin_status":

        send_message(
            chat_id,
            "⚡ وضعیت ربات\n\n"
            f"قسمت‌ها: {len(EPISODES)}\n"
            f"لینک فایل‌ها: {len(TYPE_INDEX)}\n"
            f"اسپانسرها: {len(SPONSORS)}\n"
            f"Pending: {len(PENDING)}\n"
            f"Broadcast: {len(BROADCAST_MODE)}"
        )

        return

    if data == "admin_list":

        with CACHE_LOCK:
            rows = list(EPISODES.values())

        if not rows:
            send_message(
                chat_id,
                "📋 هیچ قسمتی ثبت نشده."
            )
            return

        lines = [
            "📋 قسمت‌های ثبت‌شده:",
            ""
        ]

        for row in rows[-50:]:

            lines.append(
                f"🎬 {row.get('series_name', '-')}\n"
                f"🔢 قسمت {row.get('episode_number', '-')}\n"
                f"📁 {len(row.get('files') or [])} فایل\n"
                f"🔗 {episode_link(row.get('episode_key'))}\n"
            )

        send_message(
            chat_id,
            "\n".join(lines)
        )

        return

    if data == "admin_delete":

        send_message(
            chat_id,
            "🗑 برای حذف قسمت این دستور رو بفرست:\n\n"
            "/delete_episode episode_key"
        )

        return

    if data == "admin_delete_all":

        try:

            (
                supabase
                .table("episodes")
                .delete()
                .neq(
                    "episode_key",
                    "__never_match__"
                )
                .execute()
            )

            sync_cache(
                force=True
            )

            send_message(
                chat_id,
                "✅ همه قسمت‌ها حذف شدند."
            )

        except Exception as e:

            send_message(
                chat_id,
                f"❌ حذف همه قسمت‌ها انجام نشد.\n\n{e}"
            )

        return

    if data == "admin_stats":

        send_stats(
            chat_id
        )

        return


# =========================================================
# STATS REPORT
# =========================================================

def send_stats(chat_id):

    try:

        result = (
            supabase
            .table("bot_stats")
            .select("*")
            .execute()
        )

        rows = result.data or []

    except Exception as e:

        print(
            "[STATS REPORT ERROR]",
            repr(e)
        )

        send_message(
            chat_id,
            f"❌ دریافت آمار ناموفق بود.\n\n{e}"
        )

        return

    now = datetime.now(timezone.utc)

    today = now.date()

    month_start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    starts_today = 0
    starts_month = 0

    downloads_today = 0
    downloads_month = 0
    downloads_all = 0

    files_month = 0

    episode_counts = {}

    broadcast_ok = 0
    broadcast_fail = 0

    for row in rows:

        event = row.get("event_type", "")

        created = row.get("created_at")

        dt = None

        try:
            if created:
                dt = datetime.fromisoformat(
                    str(created).replace(
                        "Z",
                        "+00:00"
                    )
                )
        except Exception:
            pass

        is_today = (
            dt is not None
            and dt.date() == today
        )

        is_month = (
            dt is not None
            and dt >= month_start
        )

        if event == "start":

            if is_today:
                starts_today += 1

            if is_month:
                starts_month += 1

        if event == "download":

            downloads_all += 1

            if is_today:
                downloads_today += 1

            if is_month:
                downloads_month += 1

            key = row.get("episode_key")

            if key:
                episode_counts[key] = (
                    episode_counts.get(key, 0) + 1
                )

        if event == "files_sent" and is_month:
            files_month += safe_int(
                row.get("file_count"),
                0
            )

        if event == "broadcast_success":
            broadcast_ok += 1

        if event == "broadcast_fail":
            broadcast_fail += 1

    top = sorted(
        episode_counts.items(),
        key=lambda x: x[1],
        reverse=True
    )[:10]

    top_text = ""

    for i, (key, count) in enumerate(
        top,
        start=1
    ):
        top_text += (
            f"{i}. {key} — {count}\n"
        )

    if not top_text:
        top_text = "نداریم"

    try:

        users_result = (
            supabase
            .table("bot_users")
            .select("*")
            .execute()
        )

        users = users_result.data or []

    except Exception as e:

        print(
            "[STATS USERS ERROR]",
            repr(e)
        )

        users = []

    total_users = len(users)

    active_users = sum(
        1
        for u in users
        if not u.get("is_blocked", False)
    )

    new_today = 0
    new_month = 0

    for u in users:

        created = u.get("created_at")

        try:
            dt = datetime.fromisoformat(
                str(created).replace(
                    "Z",
                    "+00:00"
                )
            )

            if dt.date() == today:
                new_today += 1

            if dt >= month_start:
                new_month += 1

        except Exception:
            pass

    text = (
        "📊 آمار پیشرفته\n\n"
        f"👥 کل کاربران: {total_users}\n"
        f"🟢 کاربران فعال: {active_users}\n"
        f"🆕 کاربران جدید امروز: {new_today}\n"
        f"🆕 کاربران جدید این ماه: {new_month}\n\n"
        f"▶️ /start امروز: {starts_today}\n"
        f"▶️ /start این ماه: {starts_month}\n\n"
        f"📥 دانلود امروز: {downloads_today}\n"
        f"📥 دانلود این ماه: {downloads_month}\n"
        f"📥 دانلود کل: {downloads_all}\n"
        f"📁 فایل ارسال‌شده این ماه: {files_month}\n\n"
        "🏆 ۱۰ قسمت برتر ۳۰ روز اخیر:\n"
        f"{top_text}\n"
        f"📢 Broadcast موفق: {broadcast_ok}\n"
        f"❌ Broadcast ناموفق: {broadcast_fail}"
    )

    send_message(
        chat_id,
        text
    )


# =========================================================
# DELETE EPISODE
# =========================================================

def delete_episode(episode_key):

    try:

        result = (
            supabase
            .table("episodes")
            .delete()
            .eq(
                "episode_key",
                episode_key
            )
            .execute()
        )

        sync_cache(
            force=True
        )

        send_message(
            ADMIN_ID,
            "✅ قسمت حذف شد."
        )

    except Exception as e:

        print(
            "[DELETE] ERROR:",
            repr(e)
        )

        send_message(
            ADMIN_ID,
            f"❌ حذف قسمت انجام نشد.\n\n{e}"
        )


# =========================================================
# BROADCAST
# =========================================================

def get_all_users():
    try:

        result = (
            supabase
            .table("bot_users")
            .select("user_id,is_blocked")
            .execute()
        )

        return result.data or []

    except Exception as e:

        print(
            "[BROADCAST] users ERROR:",
            repr(e)
        )

        return []


def broadcast_worker(from_chat_id, message_id):

    users = get_all_users()

    success = 0
    failed = 0

    for user in users:

        user_id = user.get("user_id")

        if not user_id:
            continue

        if user.get("is_blocked"):
            continue

        result = tg(
            "copyMessage",
            {
                "chat_id": user_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id
            }
        )

        if result.get("ok"):

            success += 1

            add_stat(
                "broadcast_success",
                user_id=user_id
            )

        else:

            failed += 1

            add_stat(
                "broadcast_fail",
                user_id=user_id
            )

            description = str(
                result.get(
                    "description",
                    ""
                )
            )

            if (
                "403" in description
                or "blocked" in description.lower()
                or "deactivated" in description.lower()
            ):
                mark_blocked(
                    user_id
                )

    send_message(
        ADMIN_ID,
        "📢 پیام همگانی تمام شد.\n\n"
        f"✅ موفق: {success}\n"
        f"❌ ناموفق: {failed}"
    )


# =========================================================
# ADMIN MESSAGE
# =========================================================

def handle_admin_message(message):

    chat = message.get("chat") or {}

    chat_id = chat.get("id")

    if chat_id != ADMIN_ID:
        return False

    text = message.get("text") or ""

    # broadcast mode
    if BROADCAST_MODE.get(chat_id):

        if text == "/cancel_broadcast":

            BROADCAST_MODE.pop(
                chat_id,
                None
            )

            send_message(
                chat_id,
                "❌ پیام همگانی لغو شد."
            )

            return True

        BROADCAST_MODE.pop(
            chat_id,
            None
        )

        run_background(
            broadcast_worker,
            chat_id,
            message.get("message_id")
        )

        send_message(
            chat_id,
            "📢 ارسال پیام همگانی در پس‌زمینه شروع شد."
        )

        return True

    # commands

    if text == "/start":

        show_admin_panel(
            chat_id
        )

        return True

    if text == "/sponsors":

        send_sponsors(
            chat_id
        )

        return True

    if text.startswith("/add_sponsor"):

        rest = text[len("/add_sponsor"):].strip()

        add_sponsor_command(
            rest
        )

        return True

    if text.startswith("/remove_sponsor"):

        parts = text.split()

        if len(parts) < 2:

            send_message(
                chat_id,
                "فرمت:\n/remove_sponsor ID"
            )

            return True

        remove_sponsor(
            parts[1]
        )

        return True

    if text.startswith("/delete_episode"):

        parts = text.split(
            maxsplit=1
        )

        if len(parts) < 2:

            send_message(
                chat_id,
                "فرمت:\n/delete_episode episode_key"
            )

            return True

        delete_episode(
            parts[1].strip()
        )

        return True

    if text == "/delete_all":

        try:

            (
                supabase
                .table("episodes")
                .delete()
                .neq(
                    "episode_key",
                    "__never_match__"
                )
                .execute()
            )

            sync_cache(
                force=True
            )

            send_message(
                chat_id,
                "✅ همه قسمت‌ها حذف شدند."
            )

        except Exception as e:

            send_message(
                chat_id,
                f"❌ خطا:\n{e}"
            )

        return True

    if text == "/broadcast":

        BROADCAST_MODE[chat_id] = True

        send_message(
            chat_id,
            "📢 پیام همگانی رو بفرست.\n\n"
            "برای لغو:\n"
            "/cancel_broadcast"
        )

        return True

    if text == "/cancel_broadcast":

        BROADCAST_MODE.pop(
            chat_id,
            None
        )

        send_message(
            chat_id,
            "❌ پیام همگانی لغو شد."
        )

        return True

    if text == "/sync":

        sync_cache(
            force=True
        )

        send_message(
            chat_id,
            "🔄 سینک انجام شد."
        )

        return True

    if text == "/stats":

        send_stats(
            chat_id
        )

        return True

    if text == "/status":

        send_message(
            chat_id,
            f"⚡ وضعیت ربات\n\n"
            f"قسمت‌ها: {len(EPISODES)}\n"
            f"فایل‌ها: {len(TYPE_INDEX)}\n"
            f"اسپانسرها: {len(SPONSORS)}\n"
            f"Pending: {len(PENDING)}"
        )

        return True

    # اگر پیام فایل/ویدیو/عکس است
    if (
        message.get("video")
        or message.get("document")
        or message.get("audio")
        or message.get("photo")
    ):
        save_episode_file(
            message
        )
        return True

    return False


# =========================================================
# UPDATE HANDLER
# =========================================================

def process_update(update):

    try:

        if update.get("callback_query"):

            query = update["callback_query"]

            user_id = (
                query.get("from") or {}
            ).get("id")

            if user_id == ADMIN_ID:

                handle_admin_callback(
                    query
                )

            else:

                handle_callback(
                    query
                )

            return

        message = update.get("message")

        if not message:
            return

        user = message.get("from") or {}

        user_id = user.get("id")

        if user_id == ADMIN_ID:

            handled = handle_admin_message(
                message
            )

            if handled:
                return

        # register normal user
        register_user(
            user
        )

        text = message.get("text") or ""

        # /start
        if text.startswith("/start"):

            parts = text.split(
                maxsplit=1
            )

            parameter = (
                parts[1].strip()
                if len(parts) > 1
                else ""
            )

            handle_start(
                message["chat"]["id"],
                user,
                parameter
            )

            return

    except Exception as e:

        print(
            "PROCESS UPDATE ERROR:",
            repr(e)
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
        return "ok"

    run_background(
        process_update,
        update
    )

    return "ok"


# =========================================================
# HEALTH
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "Telegram bot is running.\n"
        f"Episodes: {len(EPISODES)}\n"
        f"Type links: {len(TYPE_INDEX)}\n"
        f"Sponsors: {len(SPONSORS)}"
    )


@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return {
        "ok": True,
        "episodes": len(EPISODES),
        "type_links": len(TYPE_INDEX),
        "sponsors": len(SPONSORS)
    }


# =========================================================
# STARTUP
# =========================================================

def startup():

    print(
        "================================"
    )

    print(
        "BOT STARTING..."
    )

    print(
        "MAIN CHANNEL:",
        MAIN_CHANNEL
    )

    print(
        "ADMIN:",
        ADMIN_ID
    )

    print(
        "================================"
    )

    sync_cache(
        force=True
    )

    print(
        "INITIAL CACHE:",
        len(EPISODES),
        "episodes"
    )


# =========================================================
# RUN
# =========================================================

startup()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=PORT
    )
