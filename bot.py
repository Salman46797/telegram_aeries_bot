import os, re, time, hashlib, threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, request, jsonify
from supabase import create_client

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_KEY", "").strip()
)

ADMIN_ID = 5648301086

CHANNEL_ID = "@altiustuistsnbol"
CHANNEL_URL = "https://t.me/altiustuistsnbol"

DELETE_AFTER = 30
CACHE_SYNC_SECONDS = 90

BOT_USERNAME = ""

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is missing")


supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ============================================================
# MEMORY / CACHE
# ============================================================

EPISODES = {}

EPISODE_TOKEN_INDEX = {}
FILE_TOKEN_INDEX = {}
LEGACY_TOKEN_INDEX = {}

CACHE_LOCK = threading.RLock()
CACHE_LAST_SYNC = 0.0

PENDING = {}
PENDING_LOCK = threading.RLock()

BROADCAST_MODE = set()
BROADCAST_CANCEL = threading.Event()

POOL = ThreadPoolExecutor(max_workers=8)


# ============================================================
# TELEGRAM API
# ============================================================

def tg(method, data=None, timeout=25):
    try:
        r = requests.post(
            f"{TG_API}/{method}",
            json=data or {},
            timeout=timeout
        )

        try:
            return r.json()
        except Exception:
            return {
                "ok": False,
                "description": r.text
            }

    except Exception as e:
        print(f"[TG ERROR] {method}: {e}")
        return {
            "ok": False,
            "description": str(e)
        }


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    return tg("sendMessage", data)


def copy_message(chat_id, from_chat_id, message_id):
    return tg(
        "copyMessage",
        {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id
        }
    )


def delete_message(chat_id, message_id):
    return tg(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id
        }
    )


def edit_message(chat_id, message_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }

    if reply_markup is not None:
        data["reply_markup"] = reply_markup

    return tg("editMessageText", data)


def answer_callback(callback_id, text=None, alert=False):
    data = {
        "callback_query_id": callback_id,
        "show_alert": alert
    }

    if text:
        data["text"] = text

    return tg("answerCallbackQuery", data)


def get_chat_member(chat_id, user_id):
    return tg(
        "getChatMember",
        {
            "chat_id": chat_id,
            "user_id": user_id
        }
    )


def get_me():
    return tg("getMe")


# ============================================================
# SEND FILE
# ============================================================

def send_file(chat_id, file_data):

    file_type = file_data.get("type", "video")
    file_id = file_data.get("file_id")

    caption = file_data.get("caption") or ""
    entities = file_data.get("caption_entities") or []

    if not file_id:
        return {
            "ok": False,
            "description": "missing file_id"
        }

    if file_type == "document":

        data = {
            "chat_id": chat_id,
            "document": file_id
        }

        if caption:
            data["caption"] = caption

        if entities:
            data["caption_entities"] = entities

        return tg("sendDocument", data)

    if file_type == "audio":

        data = {
            "chat_id": chat_id,
            "audio": file_id
        }

        if caption:
            data["caption"] = caption

        if entities:
            data["caption_entities"] = entities

        return tg("sendAudio", data)

    if file_type == "photo":

        data = {
            "chat_id": chat_id,
            "photo": file_id
        }

        if caption:
            data["caption"] = caption

        if entities:
            data["caption_entities"] = entities

        return tg("sendPhoto", data)

    data = {
        "chat_id": chat_id,
        "video": file_id,
        "supports_streaming": True
    }

    if caption:
        data["caption"] = caption

    if entities:
        data["caption_entities"] = entities

    return tg("sendVideo", data)


# ============================================================
# TEXT / EPISODE KEYS
# ============================================================

def norm_text(text):
    return (
        (text or "")
        .replace("ي", "ی")
        .replace("ك", "ک")
        .strip()
    )


def series_id(series):
    return hashlib.sha1(
        norm_text(series).lower().encode("utf-8")
    ).hexdigest()[:10]


def episode_key(series, number):
    return f"ep_s{series_id(series)}_{int(number)}"


def preview_key(series, number):
    return f"preview__{episode_key(series, number)}"


# ============================================================
# SAFE TELEGRAM DEEP LINKS
# ============================================================

def safe_episode_token(key):
    return (
        "e_" +
        hashlib.sha256(
            str(key).encode("utf-8")
        ).hexdigest()[:20]
    )


def safe_file_token(key, file_type, index):
    raw = f"{key}|{file_type}|{index}"

    return (
        "f_" +
        hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()[:20]
    )


def legacy_stable_type_token(key, file_type):
    return (
        "t_" +
        hashlib.sha1(
            f"{key}|{file_type}".encode("utf-8")
        ).hexdigest()[:12]
    )


def bot_link(token):

    if not BOT_USERNAME:
        return ""

    return f"https://t.me/{BOT_USERNAME}?start={token}"


# ============================================================
# CAPTION PARSER
# ============================================================

def parse_caption(caption):

    c = norm_text(caption)

    m_series = re.search(
        r"سریال\s*[«\"]([^»\"]+)[»\"]",
        c,
        re.I
    )

    if not m_series:
        m_series = re.search(
            r"سریال\s*[:：\-]?\s*(.+?)(?:\n|$)",
            c,
            re.I
        )

    m_episode = re.search(
        r"قسمت\s*[:：\-]?\s*(\d+)",
        c,
        re.I
    )

    if not m_series or not m_episode:
        return None

    series = m_series.group(1).strip()
    number = int(m_episode.group(1))

    return {
        "series_name": series,
        "episode_number": number,
        "episode_key": episode_key(series, number)
    }


def is_preview(caption):

    return bool(
        re.search(
            r"پیش[\s‌-]*نمایش|\bpreview\b",
            caption or "",
            re.I
        )
    )


def file_type_from_caption(caption):

    c = norm_text(caption)

    if "زبان اصلی" in c:
        return "زبان اصلی"

    if "زیرنویس فوری" in c:
        return "زیرنویس فوری"

    if (
        "زیرنویس مووی باز" in c
        or "زیرنویس مووی‌باز" in c
    ):
        return "زیرنویس مووی باز"

    for line in c.splitlines():

        line = line.strip()

        if not line:
            continue

        if line.startswith(
            ("🪴", "🪷", "🫧", "🎍")
        ):
            continue

        if "سریال" in line:
            continue

        if "قسمت" in line:
            continue

        if "کیفیت" in line:
            continue

        return line[:80]

    return "سایر"


def enrich_files(files, key):

    result = []

    for index, file_data in enumerate(files or []):

        item = dict(file_data)

        item["file_type"] = (
            item.get("file_type")
            or file_type_from_caption(
                item.get("caption", "")
            )
        )

        item["type_code"] = (
            item.get("type_code")
            or legacy_stable_type_token(
                key,
                item["file_type"]
            )
        )

        result.append(item)

    return result


# ============================================================
# DATABASE - EPISODES
# ============================================================

def db_all_episodes():

    rows = []

    start = 0
    page = 1000

    while True:

        try:

            response = (
                supabase
                .table("episodes")
                .select("*")
                .range(start, start + page - 1)
                .execute()
            )

            part = response.data or []

        except Exception as e:

            print("[DB] episodes SELECT ERROR:", e)
            return None

        rows.extend(part)

        if len(part) < page:
            break

        start += page

    return rows


def rebuild_token_indexes():

    episode_index = {}
    file_index = {}
    legacy_index = {}

    with CACHE_LOCK:

        for key, row in EPISODES.items():

            episode_token = safe_episode_token(key)

            episode_index[episode_token] = key

            files = enrich_files(
                row.get("files") or [],
                key
            )

            grouped = {}

            for index, file_data in enumerate(files):

                file_type = file_data.get(
                    "file_type",
                    "سایر"
                )

                token = safe_file_token(
                    key,
                    file_type,
                    index
                )

                file_index[token] = (
                    key,
                    index
                )

                legacy_token = (
                    file_data.get("type_code")
                    or legacy_stable_type_token(
                        key,
                        file_type
                    )
                )

                legacy_index.setdefault(
                    legacy_token,
                    []
                ).append(index)

                grouped.setdefault(
                    file_type,
                    []
                ).append(index)

            for file_type, indexes in grouped.items():

                stable_token = legacy_stable_type_token(
                    key,
                    file_type
                )

                legacy_index[stable_token] = (
                    key,
                    indexes
                )

        EPISODE_TOKEN_INDEX.clear()
        EPISODE_TOKEN_INDEX.update(
            episode_index
        )

        FILE_TOKEN_INDEX.clear()
        FILE_TOKEN_INDEX.update(
            file_index
        )

        LEGACY_TOKEN_INDEX.clear()
        LEGACY_TOKEN_INDEX.update(
            legacy_index
        )


def sync_cache(force=False):

    global CACHE_LAST_SYNC

    if (
        not force
        and time.time() - CACHE_LAST_SYNC
        < CACHE_SYNC_SECONDS
    ):
        return True

    rows = db_all_episodes()

    if rows is None:
        return False

    new_cache = {}

    for row in rows:

        key = row.get("episode_key")

        if key:
            new_cache[key] = row

    with CACHE_LOCK:

        EPISODES.clear()
        EPISODES.update(new_cache)

        CACHE_LAST_SYNC = time.time()

    rebuild_token_indexes()

    print(
        f"[CACHE] synced {len(new_cache)} episodes"
    )

    return True


def get_episode(key):

    with CACHE_LOCK:
        row = EPISODES.get(key)

    if row:
        return row

    try:

        response = (
            supabase
            .table("episodes")
            .select("*")
            .eq("episode_key", key)
            .limit(1)
            .execute()
        )

        if response.data:

            row = response.data[0]

            with CACHE_LOCK:
                EPISODES[key] = row

            rebuild_token_indexes()

            return row

    except Exception as e:

        print("[DB] get episode:", e)

    return None


def save_episode(
    key,
    series,
    number,
    files
):

    files = enrich_files(
        files,
        key
    )

    payload = {
        "episode_key": key,
        "series_name": series,
        "episode_number": int(number),
        "files": files
    }

    try:

        response = (
            supabase
            .table("episodes")
            .upsert(
                payload,
                on_conflict="episode_key"
            )
            .execute()
        )

        with CACHE_LOCK:
            EPISODES[key] = payload

        rebuild_token_indexes()

        return response

    except Exception as e:

        print("[DB] save episode:", e)
        return None


def delete_episode(key):

    try:

        response = (
            supabase
            .table("episodes")
            .delete()
            .eq("episode_key", key)
            .execute()
        )

        with CACHE_LOCK:
            EPISODES.pop(key, None)

        rebuild_token_indexes()

        return response

    except Exception as e:

        print("[DB] delete episode:", e)
        return None


def delete_all_episodes():

    try:

        rows = db_all_episodes()

        if rows is None:
            return None

        for row in rows:

            key = row.get("episode_key")

            if key:

                supabase \
                    .table("episodes") \
                    .delete() \
                    .eq("episode_key", key) \
                    .execute()

        with CACHE_LOCK:
            EPISODES.clear()

        rebuild_token_indexes()

        return True

    except Exception as e:

        print("[DB] delete all:", e)
        return None


# ============================================================
# SPONSORS
# ============================================================

def sponsor_rows():

    try:

        return (
            supabase
            .table("sponsors")
            .select("*")
            .order("id")
            .execute()
            .data
            or []
        )

    except Exception as e:

        print("[DB] sponsors:", e)
        return []


def add_sponsor(
    channel,
    title,
    url
):

    payloads = [

        {
            "channel_username": channel,
            "title": title,
            "link": url
        },

        {
            "chat_id": channel,
            "title": title,
            "url": url
        }
    ]

    for payload in payloads:

        try:

            return (
                supabase
                .table("sponsors")
                .insert(payload)
                .execute()
            )

        except Exception as e:

            print(
                "[DB] sponsor schema attempt:",
                e
            )

    return None


def sponsor_chat(sponsor):

    return (
        sponsor.get("channel_username")
        or sponsor.get("chat_id")
        or sponsor.get("username")
    )


def sponsor_url(sponsor):

    value = (
        sponsor.get("link")
        or sponsor.get("url")
    )

    if value:
        return value

    chat = sponsor_chat(sponsor)

    if chat:

        return (
            "https://t.me/"
            + str(chat).lstrip("@")
        )

    return ""


def remove_sponsor(sponsor_id):

    try:

        return (
            supabase
            .table("sponsors")
            .delete()
            .eq("id", int(sponsor_id))
            .execute()
        )

    except Exception as e:

        print("[DB] remove sponsor:", e)
        return None


# ============================================================
# USERS
# ============================================================

def register_user(user):

    user_id = user.get("id")

    if not user_id:
        return

    try:

        supabase \
            .table("bot_users") \
            .upsert(
                {
                    "user_id": int(user_id),
                    "first_name": user.get(
                        "first_name"
                    ) or "",
                    "last_name": user.get(
                        "last_name"
                    ) or "",
                    "username": user.get(
                        "username"
                    ) or "",
                    "is_blocked": False,
                    "last_seen": datetime.now(
                        timezone.utc
                    ).isoformat()
                },
                on_conflict="user_id"
            ) \
            .execute()

    except Exception as e:

        print("[DB] register user:", e)


def mark_blocked(user_id):

    try:

        supabase \
            .table("bot_users") \
            .update(
                {
                    "is_blocked": True
                }
            ) \
            .eq(
                "user_id",
                int(user_id)
            ) \
            .execute()

    except Exception as e:

        print("[DB] blocked:", e)


def db_all_users():

    rows = []

    start = 0
    page = 1000

    while True:

        try:

            part = (
                supabase
                .table("bot_users")
                .select("*")
                .range(
                    start,
                    start + page - 1
                )
                .execute()
                .data
                or []
            )

        except Exception as e:

            print(
                "[DB] users SELECT ERROR:",
                e
            )

            return None

        rows.extend(part)

        if len(part) < page:
            break

        start += page

    return rows


# ============================================================
# STATS
# ============================================================

def stat(
    user_id,
    event,
    episode=None,
    file_type=None,
    file_count=0
):

    try:

        supabase \
            .table("bot_stats") \
            .insert(
                {
                    "user_id": int(user_id),
                    "event_type": event,
                    "episode_key": episode,
                    "file_type": file_type,
                    "file_count": int(file_count)
                }
            ) \
            .execute()

    except Exception as e:

        print("[DB] stat:", e)


def db_all_stats():

    rows = []

    start = 0
    page = 1000

    while True:

        try:

            part = (
                supabase
                .table("bot_stats")
                .select("*")
                .range(
                    start,
                    start + page - 1
                )
                .execute()
                .data
                or []
            )

        except Exception as e:

            print(
                "[DB] stats SELECT ERROR:",
                e
            )

            return None

        rows.extend(part)

        if len(part) < page:
            break

        start += page

    return rows


def parse_dt(value):

    try:

        return datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        )

    except Exception:
        return None


def stats_report():

    now = datetime.now(timezone.utc)

    day_start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    month_start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    thirty_days = now - timedelta(days=30)

    users = db_all_users()

    if users is None:
        return None

    events = db_all_stats()

    if events is None:
        return None

    downloads = [
        x for x in events
        if x.get("event_type") == "download"
    ]

    downloads_day = [
        x for x in downloads
        if (
            parse_dt(x.get("created_at"))
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ) >= day_start
    ]

    downloads_month = [
        x for x in downloads
        if (
            parse_dt(x.get("created_at"))
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ) >= month_start
    ]

    downloads_30 = [
        x for x in downloads
        if (
            parse_dt(x.get("created_at"))
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ) >= thirty_days
    ]

    top = {}

    for event in downloads_30:

        key = event.get(
            "episode_key",
            "نامشخص"
        )

        top[key] = (
            top.get(key, 0)
            + int(
                event.get("file_count")
                or 0
            )
        )

    new_day = sum(
        1
        for user in users
        if (
            parse_dt(
                user.get("created_at")
            )
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ) >= day_start
    )

    new_month = sum(
        1
        for user in users
        if (
            parse_dt(
                user.get("created_at")
            )
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ) >= month_start
    )

    starts_day = sum(
        1
        for event in events
        if (
            event.get("event_type") == "start"
            and (
                parse_dt(
                    event.get("created_at")
                )
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ) >= day_start
        )
    )

    starts_month = sum(
        1
        for event in events
        if (
            event.get("event_type") == "start"
            and (
                parse_dt(
                    event.get("created_at")
                )
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ) >= month_start
        )
    )

    active = sum(
        1
        for user in users
        if (
            not user.get("is_blocked")
            and (
                parse_dt(
                    user.get("last_seen")
                )
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ) >= thirty_days
        )
    )

    files_month = sum(
        int(
            event.get("file_count")
            or 0
        )
        for event in downloads_month
    )

    return {
        "total_users": len(users),
        "active": active,
        "new_day": new_day,
        "new_month": new_month,
        "starts_day": starts_day,
        "starts_month": starts_month,
        "downloads_day": len(
            downloads_day
        ),
        "downloads_month": len(
            downloads_month
        ),
        "downloads_all": len(
            downloads
        ),
        "files_month": files_month,
        "top": sorted(
            top.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]
    }


# ============================================================
# MEMBERSHIP
# ============================================================

def is_member(chat_id, user_id):

    response = get_chat_member(
        chat_id,
        user_id
    )

    if not response.get("ok"):
        return False

    status = (
        response
        .get("result", {})
        .get("status")
    )

    return status in (
        "creator",
        "administrator",
        "member"
    )


def missing_memberships(user_id):

    missing = []

    if not is_member(
        CHANNEL_ID,
        user_id
    ):

        missing.append(
            {
                "title": "کانال اصلی",
                "chat": CHANNEL_ID,
                "link": CHANNEL_URL
            }
        )

    for sponsor in sponsor_rows():

        chat = sponsor_chat(sponsor)

        if not chat:
            continue

        if not is_member(
            chat,
            user_id
        ):

            missing.append(
                {
                    "title": sponsor.get(
                        "title"
                    ) or "کانال اسپانسر",
                    "chat": chat,
                    "link": sponsor_url(
                        sponsor
                    )
                }
            )

    return missing


def join_keyboard(missing):

    rows = []

    for item in missing:

        link = item.get("link")

        if link:

            rows.append(
                [
                    {
                        "text":
                            f"عضویت در {item['title']}",
                        "url": link
                    }
                ]
            )

    rows.append(
        [
            {
                "text": "عضو شدم ✅",
                "callback_data": "check_join"
            }
        ]
    )

    return {
        "inline_keyboard": rows
    }


# ============================================================
# REACTION GATE
# ============================================================

def reaction_keyboard():

    return {
        "inline_keyboard": [

            [
                {
                    "text": "انجام شد ✅",
                    "callback_data": "reaction_done"
                }
            ],

            [
                {
                    "text": "📢 مشاهده ۵ پست آخر",
                    "url": CHANNEL_URL
                }
            ]
        ]
    }


def set_pending(
    user_id,
    key,
    indexes=None
):

    with PENDING_LOCK:

        PENDING[int(user_id)] = {
            "episode_key": key,
            "indexes": indexes
        }


def get_pending(user_id):

    with PENDING_LOCK:
        return PENDING.get(
            int(user_id)
        )


def clear_pending(user_id):

    with PENDING_LOCK:
        PENDING.pop(
            int(user_id),
            None
        )


def clear_pending_all():

    with PENDING_LOCK:
        PENDING.clear()


def show_reaction_gate(
    chat_id,
    user_id
):

    send_message(
        chat_id,
        "برای دریافت فایل، به ۵ پست آخر کانال @altiustuistsnbol ری‌اکشن ❤️ بزن.\n\n"
        "بعد از انجام ری‌اکشن‌ها، روی «انجام شد ✅» بزن تا فایل برات ارسال بشه.",
        reaction_keyboard()
    )


# ============================================================
# DELIVERY
# ============================================================

def delete_later(
    chat_id,
    message_ids
):

    time.sleep(DELETE_AFTER)

    for message_id in message_ids:

        delete_message(
            chat_id,
            message_id
        )


def deliver(
    user_id,
    chat_id
):

    pending = get_pending(user_id)

    if not pending:

        send_message(
            chat_id,
            "❌ لینک قسمت پیدا نشد. دوباره لینک قسمت رو باز کن."
        )

        return

    key = pending["episode_key"]
    indexes = pending.get("indexes")

    row = get_episode(key)

    if not row:

        sync_cache(force=True)

        row = get_episode(key)

    if not row:

        clear_pending(user_id)

        send_message(
            chat_id,
            "❌ این قسمت پیدا نشد یا حذف شده."
        )

        return

    files = enrich_files(
        row.get("files") or [],
        key
    )

    if indexes is not None:

        files = [
            file_data
            for index, file_data
            in enumerate(files)
            if index in indexes
        ]

    if not files:

        clear_pending(user_id)

        send_message(
            chat_id,
            "❌ فایل این لینک پیدا نشد."
        )

        return

    clear_pending(user_id)

    # هشدار قبل از فایل‌ها
    send_message(
        chat_id,
        f"⚠️ فایل‌ها تا {DELETE_AFTER} ثانیه دیگه حذف میشن.\n"
        "🔄 برای دانلود مجدد، همین لینک رو دوباره باز کن."
    )

    sent_ids = []

    for file_data in files:

        response = send_file(
            chat_id,
            file_data
        )

        if (
            response.get("ok")
            and response.get("result", {}).get(
                "message_id"
            )
        ):

            sent_ids.append(
                response["result"]["message_id"]
            )

    if not sent_ids:

        send_message(
            chat_id,
            "❌ ارسال فایل انجام نشد. دوباره امتحان کن."
        )

        return

    POOL.submit(
        stat,
        user_id,
        "download",
        key,
        files[0].get("file_type"),
        len(sent_ids)
    )

    POOL.submit(
        delete_later,
        chat_id,
        sent_ids
    )


# ============================================================
# LINK RESOLUTION
# ============================================================

def resolve_episode_token(token):

    token = (token or "").strip()

    # --------------------------------------------------------
    # New episode link
    # --------------------------------------------------------

    if re.fullmatch(
        r"e_[0-9a-f]{20}",
        token
    ):

        with CACHE_LOCK:

            key = EPISODE_TOKEN_INDEX.get(
                token
            )

        if key:
            return key

        sync_cache(force=True)

        with CACHE_LOCK:

            key = EPISODE_TOKEN_INDEX.get(
                token
            )

        return key

    # --------------------------------------------------------
    # Old raw episode links
    # --------------------------------------------------------

    if token.startswith("ep_"):

        row = get_episode(token)

        if row:
            return token

        return None

    # --------------------------------------------------------
    # New file link
    # --------------------------------------------------------

    if re.fullmatch(
        r"f_[0-9a-f]{20}",
        token
    ):

        with CACHE_LOCK:

            result = FILE_TOKEN_INDEX.get(
                token
            )

        if result:
            key, index = result

            return key, [index]

        sync_cache(force=True)

        with CACHE_LOCK:

            result = FILE_TOKEN_INDEX.get(
                token
            )

        if result:

            key, index = result

            return key, [index]

        return None

    # --------------------------------------------------------
    # Old stable type links
    # --------------------------------------------------------

    if token.startswith("t_"):

        with CACHE_LOCK:

            result = LEGACY_TOKEN_INDEX.get(
                token
            )

        if result:
            return result

        sync_cache(force=True)

        with CACHE_LOCK:

            result = LEGACY_TOKEN_INDEX.get(
                token
            )

        if result:
            return result

        return None

    return None


# ============================================================
# ADMIN PANEL
# ============================================================

def admin_keyboard():

    return {
        "inline_keyboard": [

            [
                {
                    "text": "🎬 مدیریت قسمت‌ها",
                    "callback_data": "adm_episodes"
                },
                {
                    "text": "📢 اسپانسرها",
                    "callback_data": "adm_sponsors"
                }
            ],

            [
                {
                    "text": "📢 پیام همگانی",
                    "callback_data": "adm_broadcast"
                },
                {
                    "text": "📊 آمار پیشرفته",
                    "callback_data": "adm_stats"
                }
            ],

            [
                {
                    "text": "📋 لیست قسمت‌ها",
                    "callback_data": "adm_list"
                },
                {
                    "text": "🗑 حذف قسمت",
                    "callback_data": "adm_delete"
                }
            ],

            [
                {
                    "text": "🗑 حذف همه قسمت‌ها",
                    "callback_data": "adm_delete_all"
                },
                {
                    "text": "🔄 سینک دیتابیس",
                    "callback_data": "adm_sync"
                }
            ],

            [
                {
                    "text": "⚡ وضعیت ربات",
                    "callback_data": "adm_status"
                }
            ]
        ]
    }


def admin_panel(chat_id):

    send_message(
        chat_id,
        "پنل مدیریت ربات 👑",
        admin_keyboard()
    )


def send_long(
    chat_id,
    text
):

    if len(text) <= 3900:

        send_message(
            chat_id,
            text
        )

        return

    buffer = ""

    for line in text.split("\n"):

        if (
            len(buffer)
            + len(line)
            + 1
            > 3900
        ):

            if buffer:
                send_message(
                    chat_id,
                    buffer
                )

            buffer = line

        else:

            buffer += (
                "\n"
                if buffer
                else ""
            ) + line

    if buffer:

        send_message(
            chat_id,
            buffer
        )


def list_episodes_text():

    sync_cache(force=True)

    rows = [
        row
        for row in EPISODES.values()
        if not str(
            row.get("episode_key", "")
        ).startswith("preview__")
    ]

    rows.sort(
        key=lambda x: (
            str(
                x.get(
                    "series_name",
                    ""
                )
            ),
            int(
                x.get(
                    "episode_number",
                    0
                )
                or 0
            )
        )
    )

    if not rows:
        return "هیچ قسمتی ذخیره نشده."

    lines = [
        "📋 قسمت‌های ذخیره‌شده:\n"
    ]

    for row in rows:

        key = row["episode_key"]

        lines.append(
            f"• {row.get('series_name')} — قسمت {row.get('episode_number')}\n"
            f"{bot_link(safe_episode_token(key))}"
        )

    return "\n\n".join(lines)


# ============================================================
# STATS MESSAGE
# ============================================================

def send_stats(chat_id):

    stats = stats_report()

    if stats is None:

        send_message(
            chat_id,
            "❌ آمار خوانده نشد.\n"
            "جدول‌های bot_users و bot_stats و ستون created_at رو بررسی کن."
        )

        return

    lines = [

        "📊 آمار پیشرفته\n",

        f"👤 کل کاربران: {stats['total_users']}",

        f"🟢 فعال ۳۰ روز اخیر: {stats['active']}",

        f"🆕 کاربر جدید امروز: {stats['new_day']}",

        f"🆕 کاربر جدید این ماه: {stats['new_month']}",

        f"▶️ /start امروز: {stats['starts_day']}",

        f"▶️ /start این ماه: {stats['starts_month']}",

        "",

        f"📥 دانلود امروز: {stats['downloads_day']}",

        f"📥 دانلود این ماه: {stats['downloads_month']}",

        f"📥 دانلود کل: {stats['downloads_all']}",

        f"🎬 فایل ارسال‌شده این ماه: {stats['files_month']}",

        "",

        "🔥 ۱۰ قسمت برتر ۳۰ روز اخیر:"
    ]

    if stats["top"]:

        for index, item in enumerate(
            stats["top"],
            1
        ):

            key, count = item

            lines.append(
                f"{index}. {key} — {count} فایل"
            )

    else:

        lines.append(
            "موردی ثبت نشده."
        )

    send_message(
        chat_id,
        "\n".join(lines)
    )


# ============================================================
# BROADCAST
# ============================================================

def broadcast_start(chat_id):

    BROADCAST_CANCEL.clear()

    BROADCAST_MODE.add(chat_id)

    send_message(
        chat_id,
        "📢 پیام همگانی فعال شد.\n\n"
        "پیام، عکس، ویدیو، فایل یا صوت موردنظر رو همینجا بفرست.\n\n"
        "برای لغو:\n"
        "/cancel_broadcast"
    )


def broadcast_message(
    admin_chat,
    message
):

    try:

        users = db_all_users() or []

    except Exception as e:

        send_message(
            admin_chat,
            f"❌ کاربران خوانده نشدند:\n{e}"
        )

        BROADCAST_MODE.discard(
            admin_chat
        )

        return

    success = 0
    failed = 0

    for user in users:

        if BROADCAST_CANCEL.is_set():
            break

        user_id = user.get("user_id")

        if not user_id:
            continue

        if user.get("is_blocked"):
            continue

        response = copy_message(
            user_id,
            admin_chat,
            message.get("message_id")
        )

        if response.get("ok"):

            success += 1

        else:

            failed += 1

            if response.get(
                "error_code"
            ) == 403:

                mark_blocked(
                    user_id
                )

        time.sleep(0.04)

    POOL.submit(
        stat,
        ADMIN_ID,
        "broadcast_success",
        None,
        None,
        success
    )

    POOL.submit(
        stat,
        ADMIN_ID,
        "broadcast_fail",
        None,
        None,
        failed
    )

    send_message(
        admin_chat,
        "📢 پیام همگانی تمام شد.\n\n"
        f"✅ موفق: {success}\n"
        f"❌ ناموفق: {failed}"
    )

    BROADCAST_MODE.discard(
        admin_chat
    )

    BROADCAST_CANCEL.clear()


# ============================================================
# ADMIN FILE UPLOAD
# ============================================================

def admin_file(message):

    if not (
        message.get("video")
        or message.get("document")
        or message.get("audio")
        or message.get("photo")
    ):

        return False

    caption = (
        message.get("caption")
        or ""
    )

    parsed = parse_caption(
        caption
    )

    if not parsed:

        send_message(
            ADMIN_ID,
            "❌ کپشن قابل تشخیص نیست.\n\n"
            "نمونه:\n"
            "🪴 سریال «عشق و تخت»\n"
            "🪷 قسمت : 2\n"
            "🫧 زبان اصلی\n"
            "🎍 کیفیت : 1080"
        )

        return True

    if message.get("video"):

        file_kind = "video"
        file_object = message["video"]

    elif message.get("document"):

        file_kind = "document"
        file_object = message["document"]

    elif message.get("audio"):

        file_kind = "audio"
        file_object = message["audio"]

    else:

        file_kind = "photo"
        file_object = message["photo"][-1]

    file_data = {

        "type": file_kind,

        "file_id":
            file_object.get(
                "file_id"
            ),

        "caption":
            caption,

        "caption_entities":
            message.get(
                "caption_entities"
            ) or [],

        "file_type":
            file_type_from_caption(
                caption
            )
    }

    if is_preview(caption):

        key = preview_key(
            parsed["series_name"],
            parsed["episode_number"]
        )

    else:

        key = parsed["episode_key"]

    existing = get_episode(key)

    files = (
        existing.get("files") or []
        if existing
        else []
    )

    files.append(
        file_data
    )

    saved = save_episode(
        key,
        parsed["series_name"],
        parsed["episode_number"],
        files
    )

    if saved is None:

        send_message(
            ADMIN_ID,
            "❌ ذخیره در Supabase انجام نشد."
        )

        return True

    files = enrich_files(
        files,
        key
    )

    groups = {}

    for index, file_data in enumerate(files):

        groups.setdefault(
            file_data["file_type"],
            []
        ).append(index)

    label = (
        "پیش‌نمایش"
        if is_preview(caption)
        else "قسمت"
    )

    lines = [

        f"✅ {label} ذخیره شد.",
        "",
        f"🪴 سریال: {parsed['series_name']}",
        f"🪷 قسمت: {parsed['episode_number']}",
        ""
    ]

    for file_type, indexes in groups.items():

        # یک لینک برای هر نوع فایل
        token = safe_file_token(
            key,
            file_type,
            indexes[0]
        )

        lines.extend(
            [
                f"🎬 {file_type}",
                bot_link(token),
                ""
            ]
        )

    lines.extend(
        [
            f"🔗 لینک مستقیم {label}:",
            bot_link(
                safe_episode_token(key)
            )
        ]
    )

    send_message(
        ADMIN_ID,
        "\n".join(lines)
    )

    return True


# ============================================================
# ADMIN COMMANDS
# ============================================================

def admin_command(
    chat_id,
    user_id,
    text
):

    if user_id != ADMIN_ID:
        return False

    text = text.strip()

    if text == "/start":

        admin_panel(chat_id)

        return True

    # --------------------------------------------------------
    # Sponsors
    # --------------------------------------------------------

    if text == "/sponsors":

        rows = sponsor_rows()

        if not rows:

            send_message(
                chat_id,
                "هیچ اسپانسری ثبت نشده."
            )

            return True

        parts = []

        for sponsor in rows:

            parts.append(
                f"ID: {sponsor.get('id')}\n"
                f"{sponsor.get('title') or 'بدون عنوان'}\n"
                f"{sponsor_chat(sponsor) or ''}\n"
                f"{sponsor_url(sponsor) or ''}"
            )

        send_message(
            chat_id,
            "📢 اسپانسرها:\n\n"
            + "\n\n".join(parts)
        )

        return True

    # --------------------------------------------------------
    # Add sponsor
    # --------------------------------------------------------

    if text.startswith(
        "/add_sponsor"
    ):

        raw = text[
            len("/add_sponsor"):
        ].strip()

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

            return True

        channel, title, url = parts

        result = add_sponsor(
            channel,
            title,
            url
        )

        send_message(
            chat_id,
            "✅ اسپانسر اضافه شد."
            if result is not None
            else "❌ ذخیره اسپانسر انجام نشد."
        )

        return True

    # --------------------------------------------------------
    # Remove sponsor
    # --------------------------------------------------------

    if text.startswith(
        "/remove_sponsor"
    ):

        parts = text.split()

        if (
            len(parts) != 2
            or not parts[1].isdigit()
        ):

            send_message(
                chat_id,
                "فرمت درست:\n\n"
                "/remove_sponsor ID"
            )

            return True

        result = remove_sponsor(
            int(parts[1])
        )

        send_message(
            chat_id,
            "✅ اسپانسر حذف شد."
            if result is not None
            else "❌ حذف اسپانسر انجام نشد."
        )

        return True

    # --------------------------------------------------------
    # Stats
    # --------------------------------------------------------

    if text == "/stats":

        send_stats(chat_id)

        return True

    # --------------------------------------------------------
    # Episodes
    # --------------------------------------------------------

    if text == "/episodes":

        send_long(
            chat_id,
            list_episodes_text()
        )

        return True

    # --------------------------------------------------------
    # Sync
    # --------------------------------------------------------

    if text == "/sync":

        result = sync_cache(
            force=True
        )

        send_message(
            chat_id,
            "✅ سینک شد."
            if result
            else "❌ سینک دیتابیس خطا داشت."
        )

        return True

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    if text == "/status":

        with PENDING_LOCK:
            pending_count = len(PENDING)

        send_message(
            chat_id,
            "⚡ وضعیت ربات\n\n"
            f"Cache: {len(EPISODES)} قسمت\n"
            f"Pending: {pending_count}\n"
            f"Broadcast: {'فعال' if chat_id in BROADCAST_MODE else 'خاموش'}\n"
            f"Sync: هر {CACHE_SYNC_SECONDS} ثانیه"
        )

        return True

    # --------------------------------------------------------
    # Delete all
    # --------------------------------------------------------

    if text == "/delete_all":

        result = delete_all_episodes()

        clear_pending_all()

        send_message(
            chat_id,
            "✅ همه اطلاعات قسمت‌ها پاک شد."
            if result is not None
            else "❌ حذف انجام نشد.\n"
                 "فایل‌های اصلی تلگرام حذف نمی‌شوند."
        )

        return True

    # --------------------------------------------------------
    # Delete preview
    # --------------------------------------------------------

    if text.startswith(
        "/delete_preview"
    ):

        raw = text[
            len("/delete_preview"):
        ].strip()

        parts = [
            x.strip()
            for x in raw.split("|", 1)
        ]

        if (
            len(parts) != 2
            or not parts[1].isdigit()
        ):

            send_message(
                chat_id,
                "فرمت درست:\n\n"
                "/delete_preview اسم سریال | شماره قسمت"
            )

            return True

        key = preview_key(
            parts[0],
            int(parts[1])
        )

        result = delete_episode(
            key
        )

        send_message(
            chat_id,
            "✅ پیش‌نمایش حذف شد."
            if result is not None
            else "❌ حذف پیش‌نمایش انجام نشد."
        )

        return True

    # --------------------------------------------------------
    # Delete episode
    # --------------------------------------------------------

    if text.startswith(
        "/delete_episode"
    ):

        parts = text.split(
            maxsplit=1
        )

        if len(parts) != 2:

            send_message(
                chat_id,
                "فرمت درست:\n\n"
                "/delete_episode EPISODE_KEY"
            )

            return True

        result = delete_episode(
            parts[1].strip()
        )

        send_message(
            chat_id,
            "✅ قسمت حذف شد."
            if result is not None
            else "❌ حذف انجام نشد."
        )

        return True

    # --------------------------------------------------------
    # Broadcast
    # --------------------------------------------------------

    if text == "/broadcast":

        broadcast_start(
            chat_id
        )

        return True

    # --------------------------------------------------------
    # Cancel broadcast
    # --------------------------------------------------------

    if text == "/cancel_broadcast":

        BROADCAST_CANCEL.set()

        BROADCAST_MODE.discard(
            chat_id
        )

        send_message(
            chat_id,
            "🛑 پیام همگانی لغو شد."
        )

        return True

    return False


# ============================================================
# ADMIN CALLBACKS
# ============================================================

def admin_callback(callback):

    data = callback.get(
        "data",
        ""
    )

    message = callback.get(
        "message"
    ) or {}

    chat_id = (
        message.get("chat") or {}
    ).get("id")

    user_id = (
        callback.get("from") or {}
    ).get("id")

    callback_id = callback.get(
        "id"
    )

    if user_id != ADMIN_ID:
        return False

    answer_callback(
        callback_id
    )

    # --------------------------------------------------------
    # Episodes
    # --------------------------------------------------------

    if data == "adm_episodes":

        send_message(
            chat_id,
            "🎬 مدیریت قسمت‌ها\n\n"
            "برای آپلود فایل، ویدیو، صوت یا عکس رو "
            "با کپشن استاندارد برای ربات بفرست.\n\n"
            "حذف قسمت:\n"
            "/delete_episode EPISODE_KEY\n\n"
            "حذف همه:\n"
            "/delete_all"
        )

        return True

    # --------------------------------------------------------
    # Sponsors
    # --------------------------------------------------------

    if data == "adm_sponsors":

        send_message(
            chat_id,
            "📢 مدیریت اسپانسرها\n\n"
            "/sponsors\n\n"
            "/add_sponsor @channel | نام کانال | https://t.me/channel\n\n"
            "/remove_sponsor ID"
        )

        return True

    # --------------------------------------------------------
    # Broadcast
    # --------------------------------------------------------

    if data == "adm_broadcast":

        broadcast_start(
            chat_id
        )

        return True

    # --------------------------------------------------------
    # Stats
    # --------------------------------------------------------

    if data == "adm_stats":

        send_stats(
            chat_id
        )

        return True

    # --------------------------------------------------------
    # List
    # --------------------------------------------------------

    if data == "adm_list":

        send_long(
            chat_id,
            list_episodes_text()
        )

        return True

    # --------------------------------------------------------
    # Delete
    # --------------------------------------------------------

    if data == "adm_delete":

        send_message(
            chat_id,
            "🗑 برای حذف قسمت:\n\n"
            "/delete_episode EPISODE_KEY"
        )

        return True

    # --------------------------------------------------------
    # Delete all
    # --------------------------------------------------------

    if data == "adm_delete_all":

        result = delete_all_episodes()

        clear_pending_all()

        send_message(
            chat_id,
            "✅ همه قسمت‌ها حذف شدند."
            if result is not None
            else "❌ حذف همه قسمت‌ها انجام نشد."
        )

        return True

    # --------------------------------------------------------
    # Sync
    # --------------------------------------------------------

    if data == "adm_sync":

        result = sync_cache(
            force=True
        )

        send_message(
            chat_id,
            "✅ سینک شد."
            if result
            else "❌ سینک خطا داشت."
        )

        return True

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    if data == "adm_status":

        admin_command(
            chat_id,
            ADMIN_ID,
            "/status"
        )

        return True

    return False


# ============================================================
# CHANNEL POSTS
# ============================================================

def save_channel_post(message):

    try:

        message_id = message.get(
            "message_id"
        )

        if not message_id:
            return

        supabase \
            .table("channel_posts") \
            .upsert(
                {
                    "message_id":
                        int(message_id)
                },
                on_conflict="message_id"
            ) \
            .execute()

    except Exception as e:

        print(
            "[DB] channel post:",
            e
        )


# ============================================================
# WEB
# ============================================================

@app.route("/")
def home():

    return "Bot is running."


@app.route("/health")
def health():

    return jsonify(
        {
            "ok": True,
            "episodes": len(EPISODES)
        }
    )


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    update = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:

        # ----------------------------------------------------
        # Channel post
        # ----------------------------------------------------

        if update.get(
            "channel_post"
        ):

            channel_post = (
                update.get(
                    "channel_post"
                )
                or {}
            )

            chat = (
                channel_post.get(
                    "chat"
                )
                or {}
            )

            username = (
                chat.get(
                    "username",
                    ""
                )
                .lower()
            )

            if username == CHANNEL_ID.lstrip(
                "@"
            ).lower():

                POOL.submit(
                    save_channel_post,
                    channel_post
                )

            return jsonify(
                {"ok": True}
            )

        # ----------------------------------------------------
        # Callback query
        # ----------------------------------------------------

        callback = update.get(
            "callback_query"
        )

        if callback:

            data = callback.get(
                "data",
                ""
            )

            user_id = (
                callback.get("from")
                or {}
            ).get("id")

            message = (
                callback.get(
                    "message"
                )
                or {}
            )

            chat_id = (
                message.get(
                    "chat"
                )
                or {}
            ).get("id")

            message_id = message.get(
                "message_id"
            )

            # Admin callbacks
            if (
                user_id == ADMIN_ID
                and data.startswith("adm_")
            ):

                admin_callback(
                    callback
                )

                return jsonify(
                    {"ok": True}
                )

            # ------------------------------------------------
            # Membership check
            # ------------------------------------------------

            if data == "check_join":

                answer_callback(
                    callback.get("id"),
                    "در حال بررسی عضویت..."
                )

                pending = get_pending(
                    user_id
                )

                if not pending:

                    send_message(
                        chat_id,
                        "❌ لینک قسمت پیدا نشد."
                    )

                    return jsonify(
                        {"ok": True}
                    )

                missing = missing_memberships(
                    user_id
                )

                if missing:

                    send_message(
                        chat_id,
                        "هنوز عضویت کامل نشده 👇",
                        join_keyboard(
                            missing
                        )
                    )

                    return jsonify(
                        {"ok": True}
                    )

                if message_id:

                    delete_message(
                        chat_id,
                        message_id
                    )

                show_reaction_gate(
                    chat_id,
                    user_id
                )

                return jsonify(
                    {"ok": True}
                )

            # ------------------------------------------------
            # Reaction done
            # ------------------------------------------------

            if data == "reaction_done":

                answer_callback(
                    callback.get("id"),
                    "در حال آماده‌سازی فایل..."
                )

                pending = get_pending(
                    user_id
                )

                if not pending:

                    send_message(
                        chat_id,
                        "❌ لینک قسمت پیدا نشد."
                    )

                    return jsonify(
                        {"ok": True}
                    )

                missing = missing_memberships(
                    user_id
                )

                if missing:

                    send_message(
                        chat_id,
                        "هنوز عضویت کامل نشده 👇",
                        join_keyboard(
                            missing
                        )
                    )

                    return jsonify(
                        {"ok": True}
                    )

                if message_id:

                    delete_message(
                        chat_id,
                        message_id
                    )

                deliver(
                    user_id,
                    chat_id
                )

                return jsonify(
                    {"ok": True}
                )

            return jsonify(
                {"ok": True}
            )

        # ----------------------------------------------------
        # Normal message
        # ----------------------------------------------------

        message = update.get(
            "message"
        )

        if message:

            user = (
                message.get("from")
                or {}
            )

            user_id = user.get(
                "id"
            )

            chat_id = (
                message.get(
                    "chat"
                )
                or {}
            ).get("id")

            text = (
                message.get(
                    "text"
                )
                or ""
            )

            # Register user in background
            if user_id:

                POOL.submit(
                    register_user,
                    user
                )

            # ------------------------------------------------
            # START
            # ------------------------------------------------

            if (
                user_id
                and text.startswith(
                    "/start"
                )
            ):

                parts = text.split(
                    maxsplit=1
                )

                token = (
                    parts[1].strip()
                    if len(parts) > 1
                    else ""
                )

                POOL.submit(
                    stat,
                    user_id,
                    "start",
                    None,
                    None,
                    0
                )

                # Admin start
                if not token:

                    if user_id == ADMIN_ID:

                        admin_panel(
                            chat_id
                        )

                    else:

                        send_message(
                            chat_id,
                            "سلام 👋\n"
                            "لینک قسمت موردنظرت رو باز کن تا فایل برات ارسال بشه."
                        )

                    return jsonify(
                        {"ok": True}
                    )

                resolved = resolve_episode_token(
                    token
                )

                if not resolved:

                    send_message(
                        chat_id,
                        "❌ این قسمت پیدا نشد یا لینک قدیمیه."
                    )

                    return jsonify(
                        {"ok": True}
                    )

                if isinstance(
                    resolved,
                    tuple
                ):

                    key, indexes = resolved

                else:

                    key = resolved
                    indexes = None

                set_pending(
                    user_id,
                    key,
                    indexes
                )

                missing = missing_memberships(
                    user_id
                )

                if missing:

                    send_message(
                        chat_id,
                        "برای دریافت فایل، اول عضو کانال‌های زیر شو 👇",
                        join_keyboard(
                            missing
                        )
                    )

                else:

                    show_reaction_gate(
                        chat_id,
                        user_id
                    )

                return jsonify(
                    {"ok": True}
                )

            # ------------------------------------------------
            # Admin
            # ------------------------------------------------

            if user_id == ADMIN_ID:

                # Broadcast mode
                if (
                    chat_id in BROADCAST_MODE
                    and text
                    != "/cancel_broadcast"
                ):

                    POOL.submit(
                        broadcast_message,
                        chat_id,
                        message
                    )

                    return jsonify(
                        {"ok": True}
                    )

                # File upload
                if (
                    message.get("video")
                    or message.get("document")
                    or message.get("audio")
                    or message.get("photo")
                ):

                    admin_file(
                        message
                    )

                    return jsonify(
                        {"ok": True}
                    )

                # Commands
                if text:

                    if admin_command(
                        chat_id,
                        user_id,
                        text
                    ):

                        return jsonify(
                            {"ok": True}
                        )

        return jsonify(
            {"ok": True}
        )

    except Exception as e:

        print(
            "[WEBHOOK ERROR]",
            repr(e)
        )

        return jsonify(
            {"ok": True}
        )


# ============================================================
# STARTUP
# ============================================================

def setup():

    global BOT_USERNAME

    me = get_me()

    BOT_USERNAME = (
        me.get("result") or {}
    ).get(
        "username",
        ""
    )

    print(
        "[BOT] username:",
        BOT_USERNAME
    )

    sync_cache(
        force=True
    )

    base_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        "https://telegram-aeries-bot.onrender.com"
    ).rstrip("/")

    webhook_url = (
        base_url
        + "/webhook"
    )

    response = tg(
        "setWebhook",
        {
            "url": webhook_url,
            "allowed_updates": [
                "message",
                "callback_query",
                "channel_post"
            ],
            "drop_pending_updates": False
        }
    )

    print(
        "[WEBHOOK]",
        response
    )


# ============================================================
# RUN
# ============================================================

setup()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "10000"
            )
        )
    )
