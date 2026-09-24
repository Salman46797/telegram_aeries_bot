import os
import re
import random
import string
import threading
import time
import requests

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request


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


EPISODES_CACHE = None
SPONSORS_CACHE = None

CACHE_LOCK = threading.Lock()


# =========================
# TELEGRAM
# =========================

def telegram(method, data=None):

    started = time.time()

    try:

        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=data or {},
            timeout=20
        )

        result = r.json()

        elapsed = time.time() - started

        # فقط برای بررسی سرعت
        if method in [
            "getChatMember",
            "sendMessage",
            "sendVideo",
            "sendDocument"
        ]:

            print(
                f"[TELEGRAM] {method}: "
                f"{elapsed:.3f}s"
            )

        return result

    except Exception as e:

        print(
            f"Telegram error ({method}):",
            e
        )

        return {
            "ok": False
        }


def send_message(
    chat_id,
    text,
    keyboard=None
):

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


# =========================
# SUPABASE
# =========================

def db_get(
    table,
    params=None
):

    started = time.time()

    try:

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params=params or {},
            timeout=20
        )

        elapsed = time.time() - started

        print(
            f"[SUPABASE GET] {table}: "
            f"{elapsed:.3f}s"
        )

        if r.status_code != 200:

            print(
                "DB GET:",
                r.status_code,
                r.text
            )

            return []

        return r.json()

    except Exception as e:

        print(
            "DB GET ERROR:",
            e
        )

        return []


def db_insert(
    table,
    data
):

    started = time.time()

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

        elapsed = time.time() - started

        print(
            f"[SUPABASE INSERT] {table}: "
            f"{elapsed:.3f}s"
        )

        if r.status_code in [200, 201]:

            return r.json()

        print(
            "DB INSERT:",
            r.status_code,
            r.text
        )

        return None

    except Exception as e:

        print(
            "DB INSERT ERROR:",
            e
        )

        return None


def db_patch(
    table,
    params,
    data
):

    started = time.time()

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

        elapsed = time.time() - started

        print(
            f"[SUPABASE PATCH] {table}: "
            f"{elapsed:.3f}s"
        )

        if r.status_code not in [200, 204]:

            print(
                "DB PATCH:",
                r.status_code,
                r.text
            )

        return r.status_code in [
            200,
            204
        ]

    except Exception as e:

        print(
            "DB PATCH ERROR:",
            e
        )

        return False


def db_delete(
    table,
    params
):

    started = time.time()

    try:

        r = requests.delete(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS,
            params=params,
            timeout=20
        )

        elapsed = time.time() - started

        print(
            f"[SUPABASE DELETE] {table}: "
            f"{elapsed:.3f}s"
        )

        return r.status_code in [
            200,
            204
        ]

    except Exception as e:

        print(
            "DB DELETE ERROR:",
            e
        )

        return False


# =========================
# USER STATISTICS
# =========================

def track_user(
    user_id
):

    if not user_id:
        return

    if user_id == ADMIN_ID:
        return

    started = time.time()

    try:

        now = datetime.now(
            timezone.utc
        ).isoformat()

        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={
                **HEADERS,
                "Prefer":
                    "resolution=merge-duplicates,"
                    "return=minimal"
            },
            json={
                "user_id": user_id,
                "last_seen": now
            },
            timeout=20
        )

        elapsed = time.time() - started

        print(
            f"[TRACK_USER] "
            f"{elapsed:.3f}s"
        )

        if r.status_code not in [
            200,
            201,
            204
        ]:

            print(
                "TRACK USER:",
                r.status_code,
                r.text
            )

    except Exception as e:

        print(
            "TRACK USER ERROR:",
            e
        )


def db_count(
    table,
    params=None
):

    try:

        headers = {
            **HEADERS,
            "Prefer": "count=exact",
            "Range": "0-0"
        }

        final_params = {
            **(params or {}),
            "select": "user_id"
        }

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers,
            params=final_params,
            timeout=20
        )

        if r.status_code not in [
            200,
            206
        ]:

            print(
                "DB COUNT:",
                r.status_code,
                r.text
            )

            return 0

        content_range = r.headers.get(
            "Content-Range",
            ""
        )

        if "/" in content_range:

            total = content_range.split(
                "/"
            )[-1]

            if total != "*":

                return int(total)

        return len(
            r.json()
        )

    except Exception as e:

        print(
            "DB COUNT ERROR:",
            e
        )

        return 0


def get_stats():

    now = datetime.now(
        timezone.utc
    )

    today_start = now.replace(
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

    total_users = db_count(
        "users"
    )

    today_users = db_count(
        "users",
        {
            "last_seen":
                f"gte.{today_start.isoformat()}"
        }
    )

    monthly_users = db_count(
        "users",
        {
            "last_seen":
                f"gte.{month_start.isoformat()}"
        }
    )

    new_month_users = db_count(
        "users",
        {
            "first_seen":
                f"gte.{month_start.isoformat()}"
        }
    )

    return (
        total_users,
        today_users,
        monthly_users,
        new_month_users
    )


# =========================
# EPISODES CACHE
# =========================

def refresh_episodes_cache():

    global EPISODES_CACHE

    started = time.time()

    rows = db_get(
        "episodes",
        {
            "select": "*",
            "order": "id.asc"
        }
    )

    with CACHE_LOCK:
        EPISODES_CACHE = rows

    print(
        f"[CACHE] refresh episodes: "
        f"{time.time() - started:.3f}s"
    )

    return rows


def get_episodes():

    global EPISODES_CACHE

    with CACHE_LOCK:

        if EPISODES_CACHE is not None:

            return EPISODES_CACHE

    return refresh_episodes_cache()


# =========================
# SPONSORS CACHE
# =========================

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


# =========================
# CODES
# =========================

def random_code(
    length=6
):

    chars = (
        string.ascii_letters +
        string.digits
    )

    return "".join(
        random.choice(chars)
        for _ in range(length)
    )


def all_used_codes():

    codes = set()

    for episode in get_episodes():

        if episode.get(
            "start_code"
        ):

            codes.add(
                episode["start_code"]
            )

        for item in (
            episode.get("files")
            or []
        ):

            if isinstance(
                item,
                dict
            ):

                if item.get(
                    "type_code"
                ):

                    codes.add(
                        item["type_code"]
                    )

    return codes


def unique_code():

    used = all_used_codes()

    while True:

        code = random_code()

        if code not in used:

            return code


# =========================
# EPISODE FUNCTIONS
# =========================

def get_episode_by_key(
    key
):

    for episode in get_episodes():

        if episode.get(
            "episode_key"
        ) == key:

            return episode

    return None


def get_episode_by_type_code(
    code
):

    for episode in get_episodes():

        if episode.get(
            "start_code"
        ) == code:

            return episode, None

        for item in (
            episode.get("files")
            or []
        ):

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


def ensure_type_codes(
    episode
):

    files = (
        episode.get("files")
        or []
    )

    if not files:

        return episode

    groups = {}

    for item in files:

        if not isinstance(
            item,
            dict
        ):

            continue

        file_type = item.get(
            "file_type",
            "نامشخص"
        )

        if file_type not in groups:

            groups[file_type] = []

        groups[file_type].append(
            item
        )

    used = all_used_codes()

    if episode.get(
        "start_code"
    ) in used:

        used.discard(
            episode.get(
                "start_code"
            )
        )

    changed = False

    for file_type, items in groups.items():

        existing_code = None

        for item in items:

            old_code = item.get(
                "type_code"
            )

            if old_code:

                existing_code = old_code

                break

        if not existing_code:

            while True:

                existing_code = random_code()

                if existing_code not in used:

                    break

            used.add(
                existing_code
            )

        for item in items:

            if item.get(
                "type_code"
            ) != existing_code:

                item["type_code"] = (
                    existing_code
                )

                changed = True

    if changed:

        db_patch(
            "episodes",
            {
                "id":
                    f"eq.{episode['id']}"
            },
            {
                "files": files
            }
        )

        episode["files"] = files

    return episode


def get_type_links(
    episode
):

    episode = ensure_type_codes(
        episode
    )

    links = {}

    for item in (
        episode.get("files")
        or []
    ):

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

        if code and file_type not in links:

            links[file_type] = code

    return links


# =========================
# PENDING
# =========================

def set_pending(
    user_id,
    episode_key,
    file_type
):

    started = time.time()

    value = (
        f"{episode_key}|||"
        f"{file_type}"
    )

    old = db_get(
        "pending",
        {
            "user_id":
                f"eq.{user_id}",
            "limit": "1"
        }
    )

    if old:

        db_patch(
            "pending",
            {
                "user_id":
                    f"eq.{user_id}"
            },
            {
                "episode_key":
                    value
            }
        )

    else:

        db_insert(
            "pending",
            {
                "user_id":
                    user_id,
                "episode_key":
                    value
            }
        )

    print(
        f"[PENDING] total: "
        f"{time.time() - started:.3f}s"
    )


def get_pending(
    user_id
):

    rows = db_get(
        "pending",
        {
            "user_id":
                f"eq.{user_id}",
            "limit": "1"
        }
    )

    return (
        rows[0]
        if rows
        else None
    )


def delete_pending(
    user_id
):

    db_delete(
        "pending",
        {
            "user_id":
                f"eq.{user_id}"
        }
    )


# =========================
# SPONSORS
# =========================

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
            "id":
                f"eq.{sponsor_id}"
        }
    )

    refresh_sponsors_cache()

    return result


# =========================
# MEMBERSHIP
# =========================

def check_channel(
    channel,
    user_id
):

    started = time.time()

    result = telegram(
        "getChatMember",
        {
            "chat_id": channel,
            "user_id": user_id
        }
    )

    elapsed = time.time() - started

    print(
        f"[MEMBERSHIP] "
        f"{channel}: "
        f"{elapsed:.3f}s"
    )

    if not result.get(
        "ok"
    ):

        return False

    status = result[
        "result"
    ].get(
        "status"
    )

    return status in [
        "member",
        "administrator",
        "creator"
    ]


def all_channels_joined(
    user_id
):

    started = time.time()

    channels = [
        CHANNEL_ID
    ]

    for sponsor in get_sponsors():

        channels.append(
            sponsor["chat_id"]
        )

    if not channels:

        return True

    with ThreadPoolExecutor(
        max_workers=min(
            10,
            len(channels)
        )
    ) as executor:

        results = list(
            executor.map(
                lambda ch:
                    check_channel(
                        ch,
                        user_id
                    ),
                channels
            )
        )

    elapsed = time.time() - started

    print(
        f"[MEMBERSHIP TOTAL] "
        f"{elapsed:.3f}s"
    )

    return all(
        results
    )


def show_join_message(
    chat_id
):

    buttons = []

    buttons.append(
        [
            {
                "text":
                    "📢 کانال اصلی",
                "url":
                    CHANNEL_URL
            }
        ]
    )

    for sponsor in get_sponsors():

        buttons.append(
            [
                {
                    "text":
                        sponsor["title"],
                    "url":
                        sponsor["url"]
                }
            ]
        )

    buttons.append(
        [
            {
                "text":
                    "عضو شدم ✅",
                "callback_data":
                    "check_join"
            }
        ]
    )

    return send_message(
        chat_id,

        "📣برای استفاده از ربات و دریافت فایل :\n\n"
        "1️⃣ابتدا عضو کانال های زیر بشید\n"
        "2️⃣سپس رو دکمه عضو شدم کلیک کنید",

        {
            "inline_keyboard":
                buttons
        }
    )


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
                        "text":
                            "📢 رفتن به کانال",
                        "url":
                            CHANNEL_URL
                    }
                ],

                [
                    {
                        "text":
                            "انجام دادم ✅",
                        "callback_data":
                            "done_posts"
                    }
                ]

            ]
        }
    )


# =========================
# FILE SENDING
# =========================

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

    telegram_type = item.get(
        "type",
        "video"
    )

    caption = item.get(
        "caption",
        ""
    )

    if telegram_type == "document":

        result = telegram(
            "sendDocument",
            {
                "chat_id":
                    chat_id,
                "document":
                    file_id,
                "caption":
                    caption
            }
        )

    else:

        result = telegram(
            "sendVideo",
            {
                "chat_id":
                    chat_id,
                "video":
                    file_id,
                "caption":
                    caption,
                "supports_streaming":
                    True
            }
        )

    if result.get(
        "ok"
    ):

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

    selected = []

    for item in (
        episode.get("files")
        or []
    ):

        if not isinstance(
            item,
            dict
        ):

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


# =========================
# SEND LINKS
# =========================

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


# =========================
# SAVE EPISODE
# =========================

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
        "file_id":
            file_id,
        "type":
            telegram_type,
        "file_type":
            file_type,
        "caption":
            caption
    }

    if episode:

        files = (
            episode.get("files")
            or []
        )

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
                "id":
                    f"eq.{episode['id']}"
            },
            {
                "files":
                    episode["files"]
            }
        )

        refresh_episodes_cache()

        return episode

    start_code = unique_code()

    new_file[
        "type_code"
    ] = unique_code()

    data = {
        "episode_key":
            key,
        "series_name":
            series,
        "episode_number":
            episode_number,
        "files":
            [new_file],
        "start_code":
            start_code
    }

    result = db_insert(
        "episodes",
        data
    )

    if result:

        refresh_episodes_cache()

        return result[0]

    return None


# =========================
# DELETE EPISODES
# =========================

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
            "episode_key":
                f"eq.{key}"
        }
    )

    refresh_episodes_cache()

    return result


def delete_all():

    result = db_delete(
        "episodes",
        {
            "id":
                "gt.0"
        }
    )

    refresh_episodes_cache()

    return result


# =========================
# CAPTION PARSER
# =========================

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

    if "زبان اصلی" in caption:

        file_type = "زبان اصلی"

    elif "زیرنویس فوری" in caption:

        file_type = "زیرنویس فوری"

    elif "زیرنویس مووی باز" in caption:

        file_type = "زیرنویس مووی باز"

    else:

        file_type = "نامشخص"

    return (
        series,
        episode_number,
        file_type
    )


# =========================
# MESSAGE HANDLER
# =========================

def handle_message(
    message
):

    if not message:

        return

    total_started = time.time()

    chat_id = message.get(
        "chat",
        {}
    ).get("id")

    user_id = message.get(
        "from",
        {}
    ).get("id")

    text = message.get(
        "text",
        ""
    )

    # =====================
    # USER TRACKING
    # =====================

    t = time.time()

    track_user(
        user_id
    )

    print(
        f"[START FLOW] "
        f"track_user: "
        f"{time.time() - t:.3f}s"
    )

    # =====================
    # START
    # =====================

    if text.startswith(
        "/start"
    ):

        start_time = time.time()

        parts = text.split(
            maxsplit=1
        )

        if len(parts) < 2:

            send_message(
                chat_id,
                "سلام 👋"
            )

            print(
                f"[START FLOW] "
                f"TOTAL: "
                f"{time.time() - total_started:.3f}s"
            )

            return

        code = parts[1].strip()

        # -----------------
        # EPISODE
        # -----------------

        t = time.time()

        episode, file_type = (
            get_episode_by_type_code(
                code
            )
        )

        print(
            f"[START FLOW] "
            f"get_episode: "
            f"{time.time() - t:.3f}s"
        )

        if not episode:

            send_message(
                chat_id,
                "❌ لینک فایل معتبر نیست یا قسمت پیدا نشد."
            )

            print(
                f"[START FLOW] "
                f"TOTAL: "
                f"{time.time() - total_started:.3f}s"
            )

            return

        # -----------------
        # TYPE CODES
        # -----------------

        t = time.time()

        episode = ensure_type_codes(
            episode
        )

        print(
            f"[START FLOW] "
            f"ensure_codes: "
            f"{time.time() - t:.3f}s"
        )

        if not file_type:

            file_type = "همه"

        # -----------------
        # PENDING
        # -----------------

        t = time.time()

        set_pending(
            user_id,
            episode["episode_key"],
            file_type
        )

        print(
            f"[START FLOW] "
            f"set_pending: "
            f"{time.time() - t:.3f}s"
        )

        # -----------------
        # MEMBERSHIP
        # -----------------

        t = time.time()

        joined = all_channels_joined(
            user_id
        )

        print(
            f"[START FLOW] "
            f"membership: "
            f"{time.time() - t:.3f}s"
        )

        # -----------------
        # SHOW MESSAGE
        # -----------------

        t = time.time()

        if joined:

            show_posts_message(
                chat_id
            )

        else:

            show_join_message(
                chat_id
            )

        print(
            f"[START FLOW] "
            f"send_message: "
            f"{time.time() - t:.3f}s"
        )

        print(
            f"[START FLOW] "
            f"TOTAL: "
            f"{time.time() - total_started:.3f}s"
        )

        return

    # =====================
    # ADMIN COMMANDS
    # =====================

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

        if text == "/stats":

            (
                total_users,
                today_users,
                monthly_users,
                new_month_users
            ) = get_stats()

            now = datetime.now(
                timezone.utc
            )

            month_name = (
                f"{now.year}/"
                f"{now.month:02d}"
            )

            text_stats = (

                "📊 آمار ربات\n\n"

                f"👥 کل کاربران: "
                f"{total_users}\n\n"

                f"☀️ کاربران فعال امروز: "
                f"{today_users}\n\n"

                f"📅 کاربران فعال این ماه "
                f"({month_name}): "
                f"{monthly_users}\n\n"

                f"🆕 کاربران جدید این ماه: "
                f"{new_month_users}"
            )

            send_message(
                chat_id,
                text_stats
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

            out = (
                "📢 اسپانسرها:\n\n"
            )

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

            except:

                send_message(
                    chat_id,
                    "❌ ID اشتباه است."
                )

                return

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

            if add_sponsor(
                parts[0],
                parts[1],
                parts[2]
            ):

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

    # =====================
    # ADMIN FILE UPLOAD
    # =====================

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

            (
                series,
                episode_number,
                file_type
            ) = parsed

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


# =========================
# CALLBACK HANDLER
# =========================

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

    # ثبت فعالیت کاربر
    t = time.time()

    track_user(
        user_id
    )

    print(
        f"[CALLBACK] "
        f"track_user: "
        f"{time.time() - t:.3f}s"
    )

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

    # =====================
    # CHECK JOIN
    # =====================

    if data == "check_join":

        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id":
                    callback["id"],

                "text":
                    "در حال بررسی عضویت..."
            }
        )

        t = time.time()

        joined = all_channels_joined(
            user_id
        )

        print(
            f"[CALLBACK] "
            f"membership: "
            f"{time.time() - t:.3f}s"
        )

        if not joined:

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback["id"],

                    "text":
                        "❌ هنوز عضویت کامل نیست.",

                    "show_alert":
                        True
                }
            )

            return

        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id":
                    callback["id"],

                "text":
                    "✅ عضویت تأیید شد."
            }
        )

        delete_message(
            chat_id,
            message_id
        )

        show_posts_message(
            chat_id
        )

        return

    # =====================
    # DONE POSTS
    # =====================

    if data == "done_posts":

        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id":
                    callback["id"],

                "text":
                    "در حال آماده‌سازی فایل..."
            }
        )

        total_started = time.time()

        t = time.time()

        pending = get_pending(
            user_id
        )

        print(
            f"[CALLBACK] "
            f"get_pending: "
            f"{time.time() - t:.3f}s"
        )

        if not pending:

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback["id"],

                    "text":
                        "❌ درخواست فعال پیدا نشد.",

                    "show_alert":
                        True
                }
            )

            return

        t = time.time()

        joined = all_channels_joined(
            user_id
        )

        print(
            f"[CALLBACK] "
            f"membership: "
            f"{time.time() - t:.3f}s"
        )

        if not joined:

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback["id"],

                    "text":
                        "❌ عضویت کانال‌ها کامل نیست.",

                    "show_alert":
                        True
                }
            )

            return

        stored = pending.get(
            "episode_key",
            ""
        )

        if "|||" in stored:

            (
                episode_key,
                file_type
            ) = stored.split(
                "|||",
                1
            )

        else:

            episode_key = stored
            file_type = "همه"

        t = time.time()

        episode = get_episode_by_key(
            episode_key
        )

        print(
            f"[CALLBACK] "
            f"get_episode: "
            f"{time.time() - t:.3f}s"
        )

        if not episode:

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback["id"],

                    "text":
                        "❌ قسمت پیدا نشد.",

                    "show_alert":
                        True
                }
            )

            return

        delete_message(
            chat_id,
            message_id
        )

        delete_pending(
            user_id
        )

        if file_type == "همه":

            types = []

            for item in (
                episode.get("files")
                or []
            ):

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

                    types.append(
                        ft
                    )

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

        print(
            f"[CALLBACK] "
            f"DONE TOTAL: "
            f"{time.time() - total_started:.3f}s"
        )

        return


# =========================
# FLASK
# =========================

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


# =========================
# WEBHOOK SETUP
# =========================

def setup_webhook():

    result = telegram(
        "setWebhook",
        {
            "url":
                WEBHOOK_URL,

            "allowed_updates": [
                "message",
                "channel_post",
                "callback_query"
            ],

            "max_connections":
                40
        }
    )

    print(
        "WEBHOOK:",
        result
    )


# =========================
# START
# =========================

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
