import os
import sys
import asyncio
import html
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional

from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import aiosqlite
import swisseph as swe
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
from google import genai
from google.genai import types as genai_types


# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "8080"))
DB_PATH = os.getenv("DB_PATH", "aura_astro.db")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if not BOT_TOKEN or not GEMINI_KEY:
    print("[CRITICAL] TELEGRAM_BOT_TOKEN and GEMINI_API_KEY are required.")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

geolocator = Nominatim(
    user_agent="aura_astro_engine_prod_v2",
    timeout=7,
)
tf = TimezoneFinder()
ai_client = genai.Client(api_key=GEMINI_KEY)

UTC = timezone.utc


# ============================================================
# PLANS
# ============================================================
PRICING_PLANS = {
    "plan_1m": {
        "title": "🌟 1 Month Access",
        "description": "30 days of daily transits, personal chart guidance and AI chart questions.",
        "stars": 500,
        "days": 30,
    },
    "plan_6m": {
        "title": "⚡ 6 Months Access",
        "description": "180 days of personal transit forecasts and AI chart guidance.",
        "stars": 2000,
        "days": 180,
    },
    "plan_1y": {
        "title": "👑 1 Year Access",
        "description": "365 days of full personal astrology guidance and AI chart questions.",
        "stars": 3500,
        "days": 365,
    },
}


def pricing_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ 1 Month — 500 Stars", callback_data="buy_plan_1m")],
            [InlineKeyboardButton(text="⚡ 6 Months — 2000 Stars", callback_data="buy_plan_6m")],
            [InlineKeyboardButton(text="👑 1 Year — 3500 Stars", callback_data="buy_plan_1y")],
            [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="menu")],
        ]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌌 My Chart", callback_data="chart"),
                InlineKeyboardButton(text="🔮 Today's Forecast", callback_data="forecast"),
            ],
            [
                InlineKeyboardButton(text="🌙 Tomorrow", callback_data="tomorrow"),
                InlineKeyboardButton(text="💬 Ask My Chart", callback_data="ask_chart"),
            ],
            [
                InlineKeyboardButton(text="❤️ Relationships", callback_data="topic_relationships"),
                InlineKeyboardButton(text="💼 Career", callback_data="topic_career"),
            ],
            [
                InlineKeyboardButton(text="💰 Money", callback_data="topic_money"),
                InlineKeyboardButton(text="⭐ Subscription", callback_data="subscription"),
            ],
        ]
    )


def back_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="menu")]
        ]
    )


# ============================================================
# DATABASE
# ============================================================
async def db_columns(db, table: str) -> set:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                birth_date TEXT NOT NULL,
                birth_time TEXT NOT NULL,
                city TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                timezone TEXT NOT NULL,
                trial_until TEXT NOT NULL,
                premium_until TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )

        # Safe migration from original schema
        columns = await db_columns(db, "users")
        migrations = {
            "trial_used": "INTEGER DEFAULT 1",
            "last_forecast_date": "TEXT",
        }
        for column, definition in migrations.items():
            if column not in columns:
                await db.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                forecast_date TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                UNIQUE(user_id, forecast_date)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_name TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                telegram_payment_charge_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                plan_key TEXT NOT NULL,
                stars INTEGER NOT NULL,
                days INTEGER NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_usage (
                user_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL,
                requests INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, usage_date)
            )
            """
        )

        await db.execute("UPDATE users SET trial_used = 1 WHERE trial_used IS NULL")
        await db.commit()


async def log_event(
    user_id: Optional[int],
    event_name: str,
    metadata: str = "",
):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO events(user_id, event_name, metadata, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    event_name,
                    metadata[:1000],
                    datetime.now(UTC).isoformat(),
                ),
            )
            await db.commit()
    except Exception as e:
        print(f"[Analytics] {e}")


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def save_or_update_user(
    user_id: int,
    name: str,
    b_date: str,
    b_time: str,
    city: str,
    lat: float,
    lon: float,
    tz_str: str,
):
    now_utc = datetime.now(UTC)
    existing = await get_user(user_id)

    async with aiosqlite.connect(DB_PATH) as db:
        if existing:
            await db.execute(
                """
                UPDATE users
                SET name = ?, birth_date = ?, birth_time = ?, city = ?,
                    lat = ?, lon = ?, timezone = ?, is_active = 1
                WHERE user_id = ?
                """,
                (
                    name,
                    b_date,
                    b_time,
                    city,
                    lat,
                    lon,
                    tz_str,
                    user_id,
                ),
            )
        else:
            trial_end = (now_utc + timedelta(days=7)).isoformat()
            await db.execute(
                """
                INSERT INTO users (
                    user_id, name, birth_date, birth_time, city,
                    lat, lon, timezone, trial_until, premium_until,
                    is_active, created_at, trial_used
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, 1)
                """,
                (
                    user_id,
                    name,
                    b_date,
                    b_time,
                    city,
                    lat,
                    lon,
                    tz_str,
                    trial_end,
                    now_utc.isoformat(),
                ),
            )
        await db.commit()

    await log_event(
        user_id,
        "registration_complete",
        f"city={city[:150]}",
    )


async def add_premium_days(user_id: int, days: int):
    now_utc = datetime.now(UTC)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT premium_until FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        current_until = row["premium_until"] if row else None

        if current_until:
            try:
                current_dt = datetime.fromisoformat(current_until)
                if current_dt.tzinfo is None:
                    current_dt = current_dt.replace(tzinfo=UTC)
                base_dt = max(now_utc, current_dt)
            except ValueError:
                base_dt = now_utc
        else:
            base_dt = now_utc

        new_until = (base_dt + timedelta(days=days)).isoformat()

        await db.execute(
            """
            UPDATE users
            SET premium_until = ?, is_active = 1
            WHERE user_id = ?
            """,
            (new_until, user_id),
        )
        await db.commit()


async def get_user_access(user_id: int) -> Dict[str, Any]:
    user = await get_user(user_id)
    now_utc = datetime.now(UTC)

    if not user:
        return {
            "has_access": False,
            "status": "not_found",
            "days_left": 0,
        }

    premium_until = user.get("premium_until")
    if premium_until:
        try:
            prem_dt = datetime.fromisoformat(premium_until)
            if prem_dt.tzinfo is None:
                prem_dt = prem_dt.replace(tzinfo=UTC)
            if prem_dt > now_utc:
                return {
                    "has_access": True,
                    "status": "premium",
                    "days_left": max(1, (prem_dt - now_utc).days + 1),
                }
        except ValueError:
            pass

    trial_until = user.get("trial_until")
    if trial_until:
        try:
            trial_dt = datetime.fromisoformat(trial_until)
            if trial_dt.tzinfo is None:
                trial_dt = trial_dt.replace(tzinfo=UTC)
            if trial_dt > now_utc:
                return {
                    "has_access": True,
                    "status": "trial",
                    "days_left": max(1, (trial_dt - now_utc).days + 1),
                }
        except ValueError:
            pass

    return {
        "has_access": False,
        "status": "expired",
        "days_left": 0,
    }


async def get_active_users() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE is_active = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def deactivate_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def was_forecast_sent(user_id: int, forecast_date: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT 1 FROM daily_deliveries
            WHERE user_id = ? AND forecast_date = ?
            """,
            (user_id, forecast_date),
        ) as cursor:
            return await cursor.fetchone() is not None


async def mark_forecast_sent(user_id: int, forecast_date: str):
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO daily_deliveries
            (user_id, forecast_date, sent_at)
            VALUES (?, ?, ?)
            """,
            (user_id, forecast_date, now),
        )
        await db.execute(
            """
            UPDATE users SET last_forecast_date = ?
            WHERE user_id = ?
            """,
            (forecast_date, user_id),
        )
        await db.commit()


async def payment_already_processed(charge_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM payments WHERE telegram_payment_charge_id = ?",
            (charge_id,),
        ) as cursor:
            return await cursor.fetchone() is not None


async def record_payment(
    charge_id: str,
    user_id: int,
    plan_key: str,
    stars: int,
    days: int,
    payload: str,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO payments
            (telegram_payment_charge_id, user_id, plan_key, stars, days, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                charge_id,
                user_id,
                plan_key,
                stars,
                days,
                payload,
                datetime.now(UTC).isoformat(),
            ),
        )
        await db.commit()


async def ai_request_allowed(user_id: int, limit: int = 10) -> bool:
    today = datetime.now(UTC).date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT requests FROM ai_usage
            WHERE user_id = ? AND usage_date = ?
            """,
            (user_id, today),
        ) as cursor:
            row = await cursor.fetchone()

        current = int(row["requests"]) if row else 0
        if current >= limit:
            return False

        if row:
            await db.execute(
                """
                UPDATE ai_usage SET requests = requests + 1
                WHERE user_id = ? AND usage_date = ?
                """,
                (user_id, today),
            )
        else:
            await db.execute(
                """
                INSERT INTO ai_usage(user_id, usage_date, requests)
                VALUES (?, ?, 1)
                """,
                (user_id, today),
            )
        await db.commit()
        return True


# ============================================================
# ASTRO ENGINE
# ============================================================
ZODIAC = [
    "Aries ♈",
    "Taurus ♉",
    "Gemini ♊",
    "Cancer ♋",
    "Leo ♌",
    "Virgo ♍",
    "Libra ♎",
    "Scorpio ♏",
    "Sagittarius ♐",
    "Capricorn ♑",
    "Aquarius ♒",
    "Pisces ♓",
]

TRACKED_PLANETS = {
    "Sun": swe.SUN,
    "Moon": swe.MOON,
    "Mercury": swe.MERCURY,
    "Venus": swe.VENUS,
    "Mars": swe.MARS,
    "Jupiter": swe.JUPITER,
    "Saturn": swe.SATURN,
    "Uranus": swe.URANUS,
    "Neptune": swe.NEPTUNE,
    "Pluto": swe.PLUTO,
}

ASPECTS = {
    0: ("Conjunction", 2.0),
    60: ("Sextile", 1.5),
    90: ("Square", 2.0),
    120: ("Trine", 2.0),
    180: ("Opposition", 2.0),
}


def normalize_deg(deg: float) -> float:
    return deg % 360.0


def angular_distance(a: float, b: float) -> float:
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


def deg_to_sign(deg: float) -> str:
    deg = normalize_deg(deg)
    sign_index = int(deg // 30)
    degree_in_sign = int(deg % 30)
    return f"{ZODIAC[sign_index]} {degree_in_sign}°"


def get_julian_day(dt_utc: datetime) -> float:
    dt_utc = dt_utc.astimezone(UTC)
    return swe.julday(
        dt_utc.year,
        dt_utc.month,
        dt_utc.day,
        dt_utc.hour
        + dt_utc.minute / 60.0
        + dt_utc.second / 3600.0,
    )


def planet_positions(dt_utc: datetime) -> Dict[str, float]:
    jd = get_julian_day(dt_utc)
    result = {}
    for name, planet_id in TRACKED_PLANETS.items():
        result[name] = normalize_deg(swe.calc_ut(jd, planet_id)[0][0])
    return result


def get_natal_blueprint(
    birth_utc: datetime,
    lat: float,
    lon: float,
) -> Dict[str, Any]:
    jd = get_julian_day(birth_utc)
    positions = planet_positions(birth_utc)

    try:
        _, ascmc = swe.houses(jd, lat, lon, b"P")
        asc = normalize_deg(ascmc[0])
        mc = normalize_deg(ascmc[1])
    except Exception:
        asc = 0.0
        mc = 0.0

    return {
        "Sun": deg_to_sign(positions["Sun"]),
        "Moon": deg_to_sign(positions["Moon"]),
        "Ascendant": deg_to_sign(asc),
        "MC": deg_to_sign(mc),
        "Mercury": deg_to_sign(positions["Mercury"]),
        "Venus": deg_to_sign(positions["Venus"]),
        "Mars": deg_to_sign(positions["Mars"]),
        "Jupiter": deg_to_sign(positions["Jupiter"]),
        "Saturn": deg_to_sign(positions["Saturn"]),
        "Uranus": deg_to_sign(positions["Uranus"]),
        "Neptune": deg_to_sign(positions["Neptune"]),
        "Pluto": deg_to_sign(positions["Pluto"]),
        "_positions": positions,
        "_asc_deg": asc,
        "_mc_deg": mc,
    }


def find_transits(
    birth_utc: datetime,
    target_utc: datetime,
    max_results: int = 7,
) -> List[Dict[str, Any]]:
    natal = planet_positions(birth_utc)
    transit = planet_positions(target_utc)
    previous = planet_positions(target_utc - timedelta(days=1))

    found = []

    for t_name, t_deg in transit.items():
        for n_name, n_deg in natal.items():
            if t_name == n_name and t_name in {"Sun", "Moon"}:
                continue

            diff = angular_distance(t_deg, n_deg)
            for angle, (aspect_name, max_orb) in ASPECTS.items():
                orb = abs(diff - angle)
                if orb > max_orb:
                    continue

                prev_diff = angular_distance(previous[t_name], n_deg)
                prev_orb = abs(prev_diff - angle)
                motion = "applying" if orb < prev_orb else "separating"

                planet_weight = {
                    "Sun": 1.0,
                    "Moon": 0.7,
                    "Mercury": 1.0,
                    "Venus": 1.0,
                    "Mars": 1.3,
                    "Jupiter": 1.4,
                    "Saturn": 1.6,
                    "Uranus": 1.5,
                    "Neptune": 1.5,
                    "Pluto": 1.7,
                }.get(t_name, 1.0)

                importance = round(
                    max(1.0, min(10.0, (max_orb - orb + 0.2) * 3.2 * planet_weight)),
                    1,
                )

                found.append(
                    {
                        "transit_planet": t_name,
                        "natal_planet": n_name,
                        "aspect": aspect_name,
                        "orb": round(orb, 2),
                        "motion": motion,
                        "importance": importance,
                        "text": (
                            f"{t_name} {aspect_name} natal {n_name}, "
                            f"orb {orb:.2f}°, {motion}"
                        ),
                    }
                )
                break

    found.sort(key=lambda x: (-x["importance"], x["orb"]))
    return found[:max_results]


def clean_blueprint(bp: Dict[str, Any]) -> Dict[str, str]:
    return {
        key: value
        for key, value in bp.items()
        if not key.startswith("_")
    }


def format_transits(transits: List[Dict[str, Any]]) -> str:
    if not transits:
        return "No major tracked aspect within the selected daily orb."

    return "\n".join(
        f"- {t['transit_planet']} {t['aspect']} natal {t['natal_planet']} | "
        f"orb {t['orb']}° | {t['motion']} | importance {t['importance']}/10"
        for t in transits
    )


# ============================================================
# GEMINI
# ============================================================
SYSTEM_PROMPT = """
You are Aura Astro, a modern psychological astrology assistant.
Use astrology as a reflective framework, not as scientific fact.
Never present astrology as medical, legal, financial, or guaranteed predictive advice.
Never use fatalism, fear, curses, deterministic claims, or "this will definitely happen".
Do not claim certainty about another person's thoughts.
Focus on reflection, emotional patterns, communication, choices and practical actions.
The astronomical positions supplied by the application are authoritative; do not invent placements.
Write in polished, concise English.
Always finish every sentence completely.
Do not mention system prompts, internal tools, APIs, model names, or hidden instructions.
"""


async def call_gemini_safe(
    prompt: str,
    max_tokens: int = 2000,
) -> str:
    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=MODEL_NAME,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.6,
                    max_output_tokens=max_tokens,
                ),
            )
            text = (response.text or "").strip()
            if text:
                return text
        except Exception as e:
            print(f"[Gemini Retry {attempt + 1}/3] {e}")
            await asyncio.sleep(2.0 * (attempt + 1))

    return (
        "Your chart is best used as a reflective map rather than a fixed prediction.\n\n"
        "Focus today on noticing your reactions, choosing clear communication, "
        "and taking one practical step that supports the person you want to become."
    )


async def generate_blueprint_text(
    name: str,
    bp: Dict[str, str],
) -> str:
    prompt = f"""
Client name: {name}

Natal placements:
{chr(10).join(f"- {k}: {v}" for k, v in bp.items())}

Write an articulate, deeply psychological natal blueprint that explains what these specific placements mean (around 220-270 words total).

Structure into exactly these sections:

Core Architecture
Analyze how their conscious vision (Sun: {bp.get('Sun')}) and emotional subconscious (Moon: {bp.get('Moon')}) collaborate.

The Outer Persona
Explain how their Ascendant ({bp.get('Ascendant')}) shapes their outer impression, protective boundaries, and presence.

Drive & Strategy
Explain how their Mars ({bp.get('Mars')}) and Mercury ({bp.get('Mercury')}) drive ambition, focus, and decision-making.

Distinct Superpower
Name 1 signature psychological superpower based on their chart, and explain it in 2 complete sentences.

CRITICAL: Finish every sentence completely with proper punctuation.
"""
    return await call_gemini_safe(prompt, 2200)


async def generate_daily_forecast(
    name: str,
    bp: Dict[str, str],
    transits: List[Dict[str, Any]],
    target_date: date,
) -> str:
    prompt = f"""
Client: {name}
Date: {target_date.isoformat()}

Natal placements:
{chr(10).join(f"- {k}: {v}" for k, v in bp.items())}

Active transits:
{format_transits(transits)}

Write a useful personal daily forecast around 200 words.

Use exactly:
Daily Theme
Psychological Climate
Relationships
Work & Focus
Tactical Directives
Reflective Inquiry

Tactical Directives must contain exactly 2 bullet points.
CRITICAL: Finish every sentence completely.
"""
    return await call_gemini_safe(prompt, 2000)


async def generate_topic_answer(
    user: Dict[str, Any],
    topic: str,
    bp: Dict[str, str],
    transits: List[Dict[str, Any]],
) -> str:
    prompt = f"""
Client: {user['name']}
Requested topic: {topic}

Natal placements:
{chr(10).join(f"- {k}: {v}" for k, v in bp.items())}

Current transits:
{format_transits(transits)}

Give a focused psychological astrology reading about {topic}, around 200 words.
Explain the relevant patterns and give 3 practical suggestions.
Finish all sentences completely.
"""
    return await call_gemini_safe(prompt, 2000)


async def generate_chart_answer(
    user: Dict[str, Any],
    bp: Dict[str, str],
    transits: List[Dict[str, Any]],
    question: str,
) -> str:
    prompt = f"""
Client: {user['name']}
Question: {question}

Natal placements:
{chr(10).join(f"- {k}: {v}" for k, v in bp.items())}

Current transits:
{format_transits(transits)}

Answer the client's question using their supplied chart as a reflective framework.
Around 200 words.
Start with a direct answer, then explain the relevant chart factors, then give 2 practical actions.
Finish all sentences completely.
"""
    return await call_gemini_safe(prompt, 2000)


# ============================================================
# HELPERS
# ============================================================
def parse_user_birth_datetime(user: Dict[str, Any]) -> datetime:
    tz = ZoneInfo(user["timezone"])
    local_dt = datetime.fromisoformat(
        f"{user['birth_date']}T{user['birth_time']}"
    ).replace(tzinfo=tz)
    return local_dt.astimezone(UTC)


async def get_chart_for_user(user: Dict[str, Any]) -> Dict[str, Any]:
    birth_utc = parse_user_birth_datetime(user)
    return get_natal_blueprint(
        birth_utc,
        float(user["lat"]),
        float(user["lon"]),
    )


async def target_noon_utc(user: Dict[str, Any], target_date: date) -> datetime:
    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        12,
        0,
        tzinfo=UTC,
    )


def access_text(access: Dict[str, Any]) -> str:
    if not access["has_access"]:
        return "Expired"
    status = "Trial" if access["status"] == "trial" else "Premium"
    return f"{status} · {access['days_left']} day(s) left"


async def send_long_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
):
    if len(text) <= 3900:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )
        return

    chunks = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 > 3800:
            if current:
                chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()

    if current:
        chunks.append(current)

    for index, chunk in enumerate(chunks):
        await bot.send_message(
            chat_id=chat_id,
            text=chunk,
            reply_markup=reply_markup if index == len(chunks) - 1 else None,
        )


async def ensure_access(message: types.Message) -> Optional[Dict[str, Any]]:
    access = await get_user_access(message.from_user.id)
    if not access["has_access"]:
        await message.answer(
            "🔒 Your free access has ended.\n\n"
            "Choose a plan to continue receiving your personal forecasts and using Ask My Chart.",
            reply_markup=pricing_keyboard(),
        )
        await log_event(message.from_user.id, "paywall_shown")
        return None
    return access


# ============================================================
# REGISTRATION
# ============================================================
class Registration(StatesGroup):
    name = State()
    birth_date = State()
    birth_time = State()
    birth_city = State()


class AskChartState(StatesGroup):
    question = State()


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = await get_user(message.from_user.id)

    if user:
        access = await get_user_access(message.from_user.id)
        await message.answer(
            f"✨ Welcome back, {html.escape(user['name'])}.\n\n"
            f"Status: {access_text(access)}\n\n"
            "Choose what you want to explore.",
            reply_markup=main_menu_keyboard(),
        )
        await log_event(message.from_user.id, "start_returning")
        return

    await message.answer(
        "✨ Welcome to Aura Astro.\n\n"
        "Your personal astrology companion combines Swiss Ephemeris calculations "
        "with psychological AI guidance.\n\n"
        "Let's build your personal chart.\n\n"
        "What is your preferred name?"
    )
    await state.set_state(Registration.name)
    await log_event(message.from_user.id, "start_new")


@dp.message(Registration.name)
async def process_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()[:80]
    if len(name) < 1:
        await message.answer("Please enter a name or nickname.")
        return

    await state.update_data(name=name)
    await message.answer(
        "What is your date of birth?\n"
        "Use YYYY-MM-DD, for example 1994-08-23."
    )
    await state.set_state(Registration.birth_date)


@dp.message(Registration.birth_date)
async def process_date(message: types.Message, state: FSMContext):
    value = (message.text or "").strip()
    try:
        parsed = date.fromisoformat(value)
        if parsed > date.today() or parsed.year < 1900:
            raise ValueError
    except ValueError:
        await message.answer(
            "⚠️ Invalid date.\nPlease use YYYY-MM-DD, for example 1995-11-04."
        )
        return

    await state.update_data(birth_date=value)
    await message.answer(
        "What time were you born?\n"
        "Use 24-hour HH:MM, for example 14:30.\n"
        "If you don't know, use 12:00."
    )
    await state.set_state(Registration.birth_time)


@dp.message(Registration.birth_time)
async def process_time(message: types.Message, state: FSMContext):
    value = (message.text or "").strip()
    try:
        time.fromisoformat(value)
        if len(value) == 5:
            time.fromisoformat(value + ":00")
    except ValueError:
        await message.answer(
            "⚠️ Invalid time.\nPlease use HH:MM, for example 08:45."
        )
        return

    if len(value) == 5:
        value = value + ":00"

    await state.update_data(birth_time=value)
    await message.answer(
        "Where were you born?\n"
        "Enter city and country, for example London, UK or Chicago, USA."
    )
    await state.set_state(Registration.birth_city)


@dp.message(Registration.birth_city)
async def process_city(message: types.Message, state: FSMContext):
    city_query = (message.text or "").strip()[:150]
    status_msg = await message.answer("🔭 Calculating your chart coordinates...")

    try:
        location = await asyncio.to_thread(
            geolocator.geocode,
            city_query,
            language="en",
        )
    except Exception as e:
        print(f"[Geocoder] {e}")
        location = None

    if not location:
        await status_msg.delete()
        await message.answer(
            "⚠️ Location not found.\n"
            "Please try again as City, Country."
        )
        return

    lat, lon = float(location.latitude), float(location.longitude)
    tz_str = tf.timezone_at(lng=lon, lat=lat) or "UTC"

    data = await state.get_data()

    try:
        b_local = datetime.fromisoformat(
            f"{data['birth_date']}T{data['birth_time']}"
        ).replace(tzinfo=ZoneInfo(tz_str))
        b_utc = b_local.astimezone(UTC)
    except Exception:
        await status_msg.delete()
        await message.answer(
            "⚠️ I couldn't resolve the historical time zone for that location. "
            "Please try another nearby city."
        )
        return

    try:
        bp_full = get_natal_blueprint(b_utc, lat, lon)
        bp = clean_blueprint(bp_full)
        analysis = await generate_blueprint_text(data["name"], bp)
    except Exception as e:
        print(f"[Chart] {e}")
        await status_msg.delete()
        await message.answer(
            "⚠️ Something went wrong while calculating the chart. Please try again."
        )
        return

    await save_or_update_user(
        message.from_user.id,
        data["name"],
        data["birth_date"],
        data["birth_time"],
        location.address or city_query,
        lat,
        lon,
        tz_str,
    )

    await status_msg.delete()

    card = (
        f"🌌 NATAL BLUEPRINT — {data['name'].upper()}\n\n"
        f"☀️ Sun: {bp['Sun']}\n"
        f"🌙 Moon: {bp['Moon']}\n"
        f"🌅 Ascendant: {bp['Ascendant']}\n"
        f"☿ Mercury: {bp['Mercury']}\n"
        f"♀ Venus: {bp['Venus']}\n"
        f"♂ Mars: {bp['Mars']}\n"
        f"♃ Jupiter: {bp['Jupiter']}\n"
        f"♄ Saturn: {bp['Saturn']}\n\n"
        f"{analysis}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎁 7-Day Full Access Activated.\n"
        "Your daily forecast will arrive at 20:00 local time."
    )

    await send_long_message(
        message.chat.id,
        card,
        main_menu_keyboard(),
    )
    await state.clear()


# ============================================================
# MENU CALLBACKS
# ============================================================
@dp.callback_query(F.data == "menu")
async def cb_menu(callback: types.CallbackQuery):
    await callback.answer()
    user = await get_user(callback.from_user.id)

    if not user:
        await callback.message.answer("Please use /start to create your chart.")
        return

    access = await get_user_access(callback.from_user.id)
    await callback.message.edit_text(
        f"✨ Aura Astro\n\n"
        f"Status: {access_text(access)}\n\n"
        "What would you like to explore?",
        reply_markup=main_menu_keyboard(),
    )


@dp.callback_query(F.data == "chart")
async def cb_chart(callback: types.CallbackQuery):
    await callback.answer("Building your chart…")
    user = await get_user(callback.from_user.id)

    if not user:
        await callback.message.answer("Please use /start first.")
        return

    bp = clean_blueprint(await get_chart_for_user(user))

    text = (
        f"🌌 YOUR NATAL CHART\n\n"
        f"☀️ Sun: {bp['Sun']}\n"
        f"🌙 Moon: {bp['Moon']}\n"
        f"🌅 Ascendant: {bp['Ascendant']}\n"
        f"☿ Mercury: {bp['Mercury']}\n"
        f"♀ Venus: {bp['Venus']}\n"
        f"♂ Mars: {bp['Mars']}\n"
        f"♃ Jupiter: {bp['Jupiter']}\n"
        f"♄ Saturn: {bp['Saturn']}\n"
        f"♅ Uranus: {bp['Uranus']}\n"
        f"♆ Neptune: {bp['Neptune']}\n"
        f"♇ Pluto: {bp['Pluto']}\n"
        f"📍 Birth place: {user['city']}\n"
        f"🕰 Time zone: {user['timezone']}"
    )
    await send_long_message(
        callback.message.chat.id,
        text,
        back_menu_keyboard(),
    )
    await log_event(callback.from_user.id, "chart_view")


async def build_forecast_for_user(
    user: Dict[str, Any],
    target_date: date,
) -> str:
    bp = clean_blueprint(await get_chart_for_user(user))
    birth_utc = parse_user_birth_datetime(user)
    target_utc = await target_noon_utc(user, target_date)
    transits = find_transits(birth_utc, target_utc)

    return await generate_daily_forecast(
        user["name"],
        bp,
        transits,
        target_date,
    )


@dp.callback_query(F.data.in_({"forecast", "tomorrow"}))
async def cb_forecast(callback: types.CallbackQuery):
    await callback.answer("Reading your chart…")
    user = await get_user(callback.from_user.id)

    if not user:
        await callback.message.answer("Please use /start first.")
        return

    access = await get_user_access(callback.from_user.id)
    if not access["has_access"]:
        await callback.message.answer(
            "🔒 Your access has ended.",
            reply_markup=pricing_keyboard(),
        )
        return

    local_today = datetime.now(UTC).astimezone(
        ZoneInfo(user["timezone"])
    ).date()

    target_date = (
        local_today
        if callback.data == "forecast"
        else local_today + timedelta(days=1)
    )

    forecast = await build_forecast_for_user(user, target_date)

    header = (
        f"🔮 YOUR FORECAST — {target_date.strftime('%B %d')}\n\n"
        if callback.data == "forecast"
        else f"🌙 TOMORROW — {target_date.strftime('%B %d')}\n\n"
    )

    await send_long_message(
        callback.message.chat.id,
        header + forecast,
        back_menu_keyboard(),
    )

    await log_event(
        callback.from_user.id,
        "forecast_view",
        target_date.isoformat(),
    )


@dp.callback_query(F.data == "subscription")
async def cb_subscription(callback: types.CallbackQuery):
    await callback.answer()
    access = await get_user_access(callback.from_user.id)

    text = (
        "⭐ AURA ASTRO MEMBERSHIP\n\n"
        f"Current status: {access_text(access)}\n\n"
        "Membership includes:\n"
        "• Daily personal forecasts.\n"
        "• Current transit analysis.\n"
        "• Ask My Chart AI guidance.\n"
        "• Relationship, career and money themes.\n\n"
        "Choose your plan:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=pricing_keyboard(),
    )
    await log_event(callback.from_user.id, "subscribe_clicked")


@dp.callback_query(F.data == "ask_chart")
async def cb_ask_chart(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    user = await get_user(callback.from_user.id)

    if not user:
        await callback.message.answer("Please use /start first.")
        return

    access = await get_user_access(callback.from_user.id)
    if not access["has_access"]:
        await callback.message.answer(
            "🔒 Ask My Chart is available with active access.",
            reply_markup=pricing_keyboard(),
        )
        return

    await state.set_state(AskChartState.question)
    await callback.message.answer(
        "💬 ASK MY CHART\n\n"
        "Ask one question about yourself, relationships, work, motivation "
        "or a current situation.\n\n"
        "Example:\n"
        "“Why do I keep overthinking important conversations?”"
    )


@dp.callback_query(F.data.startswith("topic_"))
async def cb_topic(callback: types.CallbackQuery):
    await callback.answer("Analyzing…")
    user = await get_user(callback.from_user.id)

    if not user:
        await callback.message.answer("Please use /start first.")
        return

    access = await get_user_access(callback.from_user.id)
    if not access["has_access"]:
        await callback.message.answer(
            "🔒 This feature requires active access.",
            reply_markup=pricing_keyboard(),
        )
        return

    topic_map = {
        "topic_relationships": "relationships",
        "topic_career": "career",
        "topic_money": "money and personal financial mindset",
    }
    topic = topic_map.get(callback.data, "personal development")

    if not await ai_request_allowed(callback.from_user.id):
        await callback.message.answer(
            "You've reached today's AI guidance limit. "
            "Come back tomorrow for more questions.",
            reply_markup=back_menu_keyboard(),
        )
        return

    bp = clean_blueprint(await get_chart_for_user(user))
    birth_utc = parse_user_birth_datetime(user)
    target_utc = datetime.now(UTC)
    transits = find_transits(birth_utc, target_utc)

    answer = await generate_topic_answer(
        user,
        topic,
        bp,
        transits,
    )

    await send_long_message(
        callback.message.chat.id,
        f"✨ {topic.upper()}\n\n{answer}",
        back_menu_keyboard(),
    )

    await log_event(
        callback.from_user.id,
        "topic_analysis",
        topic,
    )


# ============================================================
# ASK MY CHART
# ============================================================
@dp.message(AskChartState.question)
async def process_chart_question(
    message: types.Message,
    state: FSMContext,
):
    question = (message.text or "").strip()

    if not question:
        await message.answer("Please type a question.")
        return

    if len(question) > 700:
        await message.answer(
            "Please keep the question under 700 characters so I can give you a focused answer."
        )
        return

    user = await get_user(message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Please use /start first.")
        return

    access = await get_user_access(message.from_user.id)
    if not access["has_access"]:
        await state.clear()
        await message.answer(
            "🔒 Your access has ended.",
            reply_markup=pricing_keyboard(),
        )
        return

    if not await ai_request_allowed(message.from_user.id):
        await state.clear()
        await message.answer(
            "You've reached today's AI guidance limit. "
            "Come back tomorrow for more questions.",
            reply_markup=main_menu_keyboard(),
        )
        return

    status = await message.answer("🧠 Looking at your chart and current transits…")

    try:
        bp = clean_blueprint(await get_chart_for_user(user))
        birth_utc = parse_user_birth_datetime(user)
        transits = find_transits(birth_utc, datetime.now(UTC))

        answer = await generate_chart_answer(
            user,
            bp,
            transits,
            question,
        )

        await status.delete()
        await send_long_message(
            message.chat.id,
            f"💬 {question}\n\n{answer}",
            main_menu_keyboard(),
        )
        await log_event(
            message.from_user.id,
            "ask_chart",
            question[:500],
        )
    except Exception as e:
        print(f"[AskChart] {e}")
        await status.edit_text(
            "I couldn't complete that reading right now. Please try again."
        )
    finally:
        await state.clear()


# ============================================================
# COMMANDS
# ============================================================
@dp.message(Command("menu"))
async def cmd_menu(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Use /start to create your chart.")
        return

    access = await get_user_access(message.from_user.id)
    await message.answer(
        f"✨ Aura Astro\n\nStatus: {access_text(access)}",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message):
    access = await get_user_access(message.from_user.id)
    await message.answer(
        "⭐ AURA ASTRO MEMBERSHIP\n\n"
        f"Current status: {access_text(access)}\n\n"
        "Choose your access plan:",
        reply_markup=pricing_keyboard(),
    )
    await log_event(message.from_user.id, "subscribe_command")


# ============================================================
# PAYMENTS — TELEGRAM STARS
# ============================================================
@dp.callback_query(F.data.startswith("buy_plan_"))
async def process_buy_plan(callback: types.CallbackQuery):
    await callback.answer()
    plan_key = callback.data.replace("buy_", "")
    plan = PRICING_PLANS.get(plan_key)

    if not plan:
        await callback.message.answer("Invalid plan.")
        return

    prices = [
        LabeledPrice(
            label=plan["title"],
            amount=plan["stars"],
        )
    ]

    payload = f"{plan_key}:{callback.from_user.id}"

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=plan["title"],
        description=plan["description"],
        payload=payload,
        currency="XTR",
        prices=prices,
        provider_token="",
    )

    await log_event(
        callback.from_user.id,
        "payment_started",
        plan_key,
    )


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(
        pre_checkout.id,
        ok=True,
    )


@dp.message(F.successful_payment)
async def process_payment_success(message: types.Message):
    payment = message.successful_payment
    payload = payment.invoice_payload
    charge_id = payment.telegram_payment_charge_id

    if await payment_already_processed(charge_id):
        await message.answer(
            "✅ This payment has already been applied to your account.",
            reply_markup=main_menu_keyboard(),
        )
        return

    plan_key = payload.split(":")[0]
    plan = PRICING_PLANS.get(plan_key)

    if not plan:
        await message.answer(
            "Payment received, but the plan could not be identified. "
            "Please contact support."
        )
        return

    await record_payment(
        charge_id=charge_id,
        user_id=message.from_user.id,
        plan_key=plan_key,
        stars=plan["stars"],
        days=plan["days"],
        payload=payload,
    )

    await add_premium_days(
        message.from_user.id,
        plan["days"],
    )

    await log_event(
        message.from_user.id,
        "payment_success",
        plan_key,
    )

    await message.answer(
        "🎉 PAYMENT SUCCESSFUL\n\n"
        f"Activated: {plan['title']}\n"
        f"Added: {plan['days']} days of Premium access.\n\n"
        "Your daily forecasts will continue arriving at 20:00 local time.",
        reply_markup=main_menu_keyboard(),
    )


# ============================================================
# DAILY SCHEDULER
# ============================================================
async def send_daily_cycle():
    users = await get_active_users()
    now_utc = datetime.now(UTC)

    for user in users:
        try:
            tz = ZoneInfo(user["timezone"])
            local_now = now_utc.astimezone(tz)

            if local_now.hour != 20:
                continue

            forecast_date = (local_now.date() + timedelta(days=1))
            forecast_key = forecast_date.isoformat()

            if await was_forecast_sent(user["user_id"], forecast_key):
                continue

            access = await get_user_access(user["user_id"])

            if not access["has_access"]:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=(
                        f"🔒 Your free access has ended, {user['name']}.\n\n"
                        "Continue with Premium to receive daily personal forecasts "
                        "and use Ask My Chart."
                    ),
                    reply_markup=pricing_keyboard(),
                )
                await log_event(
                    user["user_id"],
                    "paywall_daily",
                )
                await asyncio.sleep(0.2)
                continue

            birth_utc = parse_user_birth_datetime(user)
            target_utc = await target_noon_utc(user, forecast_date)
            transits = find_transits(
                birth_utc,
                target_utc,
                max_results=7,
            )

            bp = clean_blueprint(await get_chart_for_user(user))
            reading = await generate_daily_forecast(
                user["name"],
                bp,
                transits,
                forecast_date,
            )

            status = "Trial" if access["status"] == "trial" else "Premium"

            msg = (
                f"✨ TOMORROW'S DAILY ALIGNMENT\n"
                f"{forecast_date.strftime('%B %d, %Y')}\n\n"
                f"{reading}\n\n"
                f"⏳ {status}: {access['days_left']} "
                f"{'day' if access['days_left'] == 1 else 'days'} remaining."
            )

            await send_long_message(
                user["user_id"],
                msg,
                main_menu_keyboard(),
            )

            await mark_forecast_sent(
                user["user_id"],
                forecast_key,
            )

            await log_event(
                user["user_id"],
                "forecast_sent",
                forecast_key,
            )

            await asyncio.sleep(1.2)

        except TelegramForbiddenError:
            await deactivate_user(user["user_id"])
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            print(
                f"[Scheduler Error] user={user.get('user_id')}: {e}"
            )
            await asyncio.sleep(0.5)


# ============================================================
# HEALTH SERVER
# ============================================================
async def health_check(request):
    return web.Response(
        text="Aura Astro Engine v2.2 Online",
        status=200,
    )


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )
    await site.start()

    print(
        f"🌍 Health-check server running on port {PORT}"
    )


# ============================================================
# MAIN
# ============================================================
async def main():
    await init_db()
    await start_web_server()

    scheduler = AsyncIOScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
        },
    )

    scheduler.add_job(
        send_daily_cycle,
        "cron",
        minute=0,
        id="daily_forecast_cycle",
        replace_existing=True,
    )

    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)

    print("🚀 Aura Astro v2.2 is operational.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot safely shutdown.")
