import os
import sys
import asyncio
import html
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional

from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
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

import asyncpg
import swisseph as swe
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
from google import genai
from google.genai import types as genai_types


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_KEY")
PORT = int(os.getenv("PORT", "8080"))
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    print("[CRITICAL] TELEGRAM_BOT_TOKEN is required.", flush=True)
    sys.exit(1)

if not GEMINI_KEY:
    print("[CRITICAL] GEMINI_API_KEY is required.", flush=True)
    sys.exit(1)

if not DATABASE_URL:
    print("[CRITICAL] DATABASE_URL is required for Neon PostgreSQL.", flush=True)
    sys.exit(1)


# ============================================================
# GEMINI
# ============================================================

# Можно изменить через переменную окружения GEMINI_MODEL.
# Основная модель:
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

# Резервная модель.
MODELS_CHAIN = [
    DEFAULT_GEMINI_MODEL,
    "gemini-3.1-pro-preview",
]


# ============================================================
# GLOBAL OBJECTS
# ============================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

geolocator = Nominatim(
    user_agent="aura_astro_engine_prod_v3",
    timeout=7,
)

tf = TimezoneFinder()

ai_client = genai.Client(api_key=GEMINI_KEY)

UTC = timezone.utc

db_pool: Optional[asyncpg.Pool] = None


# ============================================================
# LOCALIZATION
# ============================================================

TEXTS = {
    "ru": {
        "welcome_back": (
            "✨ С возвращением, {name}!\n\n"
            "Статус: {status}\n\n"
            "Что хотите изучить?"
        ),

        "start_intro": (
            "✨ Добро пожаловать в Aura Astro.\n\n"
            "Ваш персональный астрологический проводник объединяет "
            "точные расчёты Swiss Ephemeris и глубокую психологию.\n\n"
            "Давайте построим вашу натальную карту.\n\n"
            "Как к вам обращаться?"
        ),

        "ask_birth_date": (
            "Укажите вашу дату рождения в формате "
            "`ГГГГ-ММ-ДД`.\n\n"
            "Например: `1994-08-23`"
        ),

        "invalid_date": (
            "⚠️ Неверный формат даты.\n\n"
            "Используйте `ГГГГ-ММ-ДД`.\n"
            "Например: `1995-11-04`."
        ),

        "ask_birth_time": (
            "В какое время вы родились?\n\n"
            "Используйте 24-часовой формат `ЧЧ:ММ`.\n"
            "Например: `14:30`.\n\n"
            "Если точное время неизвестно, напишите `12:00`."
        ),

        "invalid_time": (
            "⚠️ Неверный формат времени.\n\n"
            "Введите время в формате `ЧЧ:ММ`.\n"
            "Например: `08:45`."
        ),

        "ask_city": (
            "Где вы родились?\n\n"
            "Укажите город и страну.\n"
            "Например: `Москва, Россия` или `Минск, Беларусь`."
        ),

        "calc_coords": "🔭 Рассчитываю координаты и натальную карту...",

        "city_not_found": (
            "⚠️ Город не найден.\n\n"
            "Попробуйте написать точнее: `Город, Страна`."
        ),

        "tz_error": (
            "⚠️ Не удалось определить исторический часовой пояс.\n\n"
            "Попробуйте указать ближайший крупный город."
        ),

        "calc_error": (
            "⚠️ Произошла ошибка при расчёте карты.\n\n"
            "Пожалуйста, попробуйте ещё раз."
        ),

        "trial_activated": (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🎁 Полный доступ активирован на 7 дней.\n\n"
            "Ежедневный прогноз будет приходить "
            "в 20:00 по местному времени."
        ),

        "btn_chart": "🌌 Моя карта",
        "btn_forecast": "🔮 Прогноз на сегодня",
        "btn_tomorrow": "🌙 Завтра",
        "btn_ask": "💬 Спросить карту",
        "btn_rel": "❤️ Отношения",
        "btn_career": "💼 Карьера",
        "btn_money": "💰 Финансы",
        "btn_sub": "⭐ Подписка",
        "btn_menu": "⬅️ Главное меню",

        "status_trial": "Пробный период",
        "status_prem": "Премиум",
        "status_exp": "Истёк",

        "days_left": "осталось {days} дн.",

        "limit_reached": (
            "Вы исчерпали лимит запросов к карте на сегодня.\n\n"
            "Возвращайтесь завтра."
        ),

        "paywall_msg": (
            "🔒 Бесплатный доступ завершён.\n\n"
            "Оформите подписку, чтобы продолжить получать "
            "прогнозы и задавать вопросы натальной карте."
        ),

        "ask_prompt": (
            "💬 СПРОСИТЬ КАРТУ\n\n"
            "Задайте один вопрос о себе, работе, отношениях "
            "или важном выборе.\n\n"
            "Например:\n"
            "«В чём корень моих сомнений при смене работы?»"
        ),

        "analyzing": (
            "🧠 Анализирую натальную карту и положение планет..."
        ),

        "subscription_title": "⭐ AURA ASTRO — ПОДПИСКА",

        "subscription_choose": "Выберите план доступа:",

        "plan_1m_btn": "⭐ 1 месяц — 199 Stars",
        "plan_3m_btn": "✨ 3 месяца — 450 Stars",
        "plan_6m_btn": "⚡ 6 месяцев — 800 Stars",
        "plan_1y_btn": "👑 1 год — 1200 Stars",

        "payment_success": "🎉 Доступ активирован на {days} дней!",
        "payment_already_processed": "✅ Этот платёж уже был обработан.",

        "chart_title": "🌌 ВАША НАТАЛЬНАЯ КАРТА",
        "chart_title_short": "🌌 НАТАЛЬНАЯ КАРТА",

        "sun": "Солнце",
        "moon": "Луна",
        "ascendant": "Асцендент",
        "mc": "MC",
        "mercury": "Меркурий",
        "venus": "Венера",
        "mars": "Марс",
        "jupiter": "Юпитер",
        "saturn": "Сатурн",
        "uranus": "Уран",
        "neptune": "Нептун",
        "pluto": "Плутон",

        "meaning_sun": "личность, воля и жизненная энергия",
        "meaning_moon": "эмоции, внутренние потребности и чувство безопасности",
        "meaning_ascendant": "внешний образ, первое впечатление и способ проявляться",
        "meaning_mc": "карьера, статус и направление профессиональной реализации",
        "meaning_mercury": "мышление, речь и способ обрабатывать информацию",
        "meaning_venus": "любовь, симпатии, ценности и личный вкус",
        "meaning_mars": "действие, энергия, напор и способ добиваться своего",
        "meaning_jupiter": "рост, убеждения, возможности и расширение горизонтов",
        "meaning_saturn": "дисциплина, границы, ответственность и зрелость",
        "meaning_uranus": "свобода, перемены и стремление к независимости",
        "meaning_neptune": "интуиция, идеалы, воображение и чувствительность",
        "meaning_pluto": "глубокие изменения, сила и внутренние трансформации",

        "location": "Место рождения",

        "tomorrow_title": "✨ ПРОГНОЗ НА ЗАВТРА",
        "forecast_title": "🔮 ПРОГНОЗ",

        "trial": "Пробный период",
        "premium": "Премиум",
        "expired": "Доступ завершён",
    },

    "en": {
        "welcome_back": (
            "✨ Welcome back, {name}!\n\n"
            "Status: {status}\n\n"
            "What would you like to explore?"
        ),

        "start_intro": (
            "✨ Welcome to Aura Astro.\n\n"
            "Your personal astrology companion combines "
            "Swiss Ephemeris calculations with psychological AI guidance.\n\n"
            "Let's build your personal chart.\n\n"
            "What is your preferred name?"
        ),

        "ask_birth_date": (
            "What is your date of birth?\n\n"
            "Use `YYYY-MM-DD`.\n"
            "Example: `1994-08-23`"
        ),

        "invalid_date": (
            "⚠️ Invalid date.\n\n"
            "Please use `YYYY-MM-DD`.\n"
            "Example: `1995-11-04`."
        ),

        "ask_birth_time": (
            "What time were you born?\n\n"
            "Use 24-hour format `HH:MM`.\n"
            "Example: `14:30`.\n\n"
            "If unknown, type `12:00`."
        ),

        "invalid_time": (
            "⚠️ Invalid time.\n\n"
            "Please use `HH:MM`.\n"
            "Example: `08:45`."
        ),

        "ask_city": (
            "Where were you born?\n\n"
            "Enter city and country.\n"
            "Example: `London, UK` or `Chicago, USA`."
        ),

        "calc_coords": "🔭 Calculating coordinates and natal chart...",

        "city_not_found": (
            "⚠️ Location not found.\n\n"
            "Try again as `City, Country`."
        ),

        "tz_error": (
            "⚠️ Couldn't resolve the historical timezone.\n\n"
            "Try a nearby larger city."
        ),

        "calc_error": (
            "⚠️ Something went wrong calculating the chart.\n\n"
            "Please try again."
        ),

        "trial_activated": (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🎁 7-Day Full Access Activated.\n\n"
            "Your daily forecast will arrive at 20:00 local time."
        ),

        "btn_chart": "🌌 My Chart",
        "btn_forecast": "🔮 Today's Forecast",
        "btn_tomorrow": "🌙 Tomorrow",
        "btn_ask": "💬 Ask My Chart",
        "btn_rel": "❤️ Relationships",
        "btn_career": "💼 Career",
        "btn_money": "💰 Money",
        "btn_sub": "⭐ Subscription",
        "btn_menu": "⬅️ Main Menu",

        "status_trial": "Trial",
        "status_prem": "Premium",
        "status_exp": "Expired",

        "days_left": "{days} day(s) left",

        "limit_reached": (
            "You've reached today's AI guidance limit.\n\n"
            "Come back tomorrow."
        ),

        "paywall_msg": (
            "🔒 Your free access has ended.\n\n"
            "Choose a subscription to continue receiving "
            "forecasts and asking your chart."
        ),

        "ask_prompt": (
            "💬 ASK MY CHART\n\n"
            "Ask one question about yourself, relationships, "
            "work, or an important decision.\n\n"
            "Example:\n"
            "\"Why do I keep overthinking conversations?\""
        ),

        "analyzing": (
            "🧠 Looking at your chart and current planetary transits..."
        ),

        "subscription_title": "⭐ AURA ASTRO — MEMBERSHIP",

        "subscription_choose": "Choose your access plan:",

        "plan_1m_btn": "⭐ 1 Month — 199 Stars",
        "plan_3m_btn": "✨ 3 Months — 450 Stars",
        "plan_6m_btn": "⚡ 6 Months — 800 Stars",
        "plan_1y_btn": "👑 1 Year — 1200 Stars",

        "payment_success": "🎉 Access activated for {days} days!",
        "payment_already_processed": "✅ This payment has already been processed.",

        "chart_title": "🌌 YOUR NATAL CHART",
        "chart_title_short": "🌌 NATAL CHART",

        "sun": "Sun",
        "moon": "Moon",
        "ascendant": "Ascendant",
        "mc": "MC",
        "mercury": "Mercury",
        "venus": "Venus",
        "mars": "Mars",
        "jupiter": "Jupiter",
        "saturn": "Saturn",
        "uranus": "Uranus",
        "neptune": "Neptune",
        "pluto": "Pluto",

        "meaning_sun": "personality, will and life energy",
        "meaning_moon": "emotions, inner needs and sense of security",
        "meaning_ascendant": "outer image, first impression and way of expressing yourself",
        "meaning_mc": "career, status and professional direction",
        "meaning_mercury": "thinking, communication and information processing",
        "meaning_venus": "love, attraction, values and personal taste",
        "meaning_mars": "action, energy, drive and determination",
        "meaning_jupiter": "growth, beliefs, opportunities and expansion",
        "meaning_saturn": "discipline, boundaries, responsibility and maturity",
        "meaning_uranus": "freedom, change and independence",
        "meaning_neptune": "intuition, ideals, imagination and sensitivity",
        "meaning_pluto": "deep change, power and inner transformation",

        "location": "Birth place",

        "tomorrow_title": "✨ TOMORROW'S ALIGNMENT",
        "forecast_title": "🔮 FORECAST",

        "trial": "Trial",
        "premium": "Premium",
        "expired": "Access expired",
    },
}


def normalize_lang(lang: Optional[str]) -> str:
    return "ru" if (lang or "").lower().startswith("ru") else "en"


def t(key: str, lang: str = "en", **kwargs) -> str:
    lang = normalize_lang(lang)
    text = TEXTS.get(lang, TEXTS["en"]).get(
        key,
        TEXTS["en"].get(key, "")
    )

    if kwargs:
        return text.format(**kwargs)

    return text


# ============================================================
# ASTRO LABELS / DESCRIPTIONS
# ============================================================

PLACEMENT_KEYS = [
    "Sun",
    "Moon",
    "Ascendant",
    "MC",
    "Mercury",
    "Venus",
    "Mars",
    "Jupiter",
    "Saturn",
    "Uranus",
    "Neptune",
    "Pluto",
]


PLACEMENT_ICONS = {
    "Sun": "☀️",
    "Moon": "🌙",
    "Ascendant": "🌅",
    "MC": "🎯",
    "Mercury": "☿",
    "Venus": "♀",
    "Mars": "♂",
    "Jupiter": "♃",
    "Saturn": "♄",
    "Uranus": "♅",
    "Neptune": "♆",
    "Pluto": "♇",
}


def format_placement_line(key: str, value: str, lang: str) -> str:
    icon = PLACEMENT_ICONS.get(key, "•")
    label = t(key.lower(), lang)
    meaning = t(f"meaning_{key.lower()}", lang)

    return f"{icon} {label}: {value}\n   ↳ {meaning}"


def build_chart_card(
    bp: Dict[str, str],
    name: str,
    lang: str,
    include_all: bool = True,
) -> str:

    title = t("chart_title", lang)

    keys = PLACEMENT_KEYS if include_all else [
        "Sun",
        "Moon",
        "Ascendant",
        "Mercury",
        "Venus",
        "Mars",
        "Jupiter",
        "Saturn",
    ]

    lines = [
        f"{title} — {html.escape(name).upper()}",
        "",
    ]

    for key in keys:
        if key in bp:
            lines.append(
                format_placement_line(
                    key,
                    bp[key],
                    lang,
                )
            )
            lines.append("")

    return "\n".join(lines).rstrip()


# ============================================================
# KEYBOARDS
# ============================================================

PRICING_PLANS = {
    "plan_1m": {
        "title": "🌟 1 Month Access",
        "description": "30 days of forecasts & Ask My Chart.",
        "stars": 199,
        "days": 30,
    },

    "plan_3m": {
        "title": "✨ 3 Months Access",
        "description": "90 days of complete astro guidance.",
        "stars": 450,
        "days": 90,
    },

    "plan_6m": {
        "title": "⚡ 6 Months Access",
        "description": "180 days of forecasts & transit tracking.",
        "stars": 800,
        "days": 180,
    },

    "plan_1y": {
        "title": "👑 1 Year Access",
        "description": "365 days of complete astrology coaching.",
        "stars": 1200,
        "days": 365,
    },
}


def pricing_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("plan_1m_btn", lang),
                    callback_data="buy_plan_1m",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("plan_3m_btn", lang),
                    callback_data="buy_plan_3m",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("plan_6m_btn", lang),
                    callback_data="buy_plan_6m",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("plan_1y_btn", lang),
                    callback_data="buy_plan_1y",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("btn_menu", lang),
                    callback_data="menu",
                )
            ],
        ]
    )


def main_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("btn_chart", lang),
                    callback_data="chart",
                ),
                InlineKeyboardButton(
                    text=t("btn_forecast", lang),
                    callback_data="forecast",
                ),
            ],

            [
                InlineKeyboardButton(
                    text=t("btn_tomorrow", lang),
                    callback_data="tomorrow",
                ),
                InlineKeyboardButton(
                    text=t("btn_ask", lang),
                    callback_data="ask_chart",
                ),
            ],

            [
                InlineKeyboardButton(
                    text=t("btn_rel", lang),
                    callback_data="topic_relationships",
                ),
                InlineKeyboardButton(
                    text=t("btn_career", lang),
                    callback_data="topic_career",
                ),
            ],

            [
                InlineKeyboardButton(
                    text=t("btn_money", lang),
                    callback_data="topic_money",
                ),
                InlineKeyboardButton(
                    text=t("btn_sub", lang),
                    callback_data="subscription",
                ),
            ],
        ]
    )


def back_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("btn_menu", lang),
                    callback_data="menu",
                )
            ]
        ]
    )


# ============================================================
# DATABASE
# ============================================================

async def init_db():
    global db_pool

    clean_url = DATABASE_URL

    if clean_url.startswith("postgres://"):
        clean_url = clean_url.replace(
            "postgres://",
            "postgresql://",
            1,
        )

    db_pool = await asyncpg.create_pool(
        clean_url,
        min_size=1,
        max_size=5,
    )

    async with db_pool.acquire() as conn:

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                name TEXT NOT NULL,
                birth_date TEXT NOT NULL,
                birth_time TEXT NOT NULL,
                city TEXT NOT NULL,
                lat DOUBLE PRECISION NOT NULL,
                lon DOUBLE PRECISION NOT NULL,
                timezone TEXT NOT NULL,
                trial_until TEXT NOT NULL,
                premium_until TEXT,
                is_active INT DEFAULT 1,
                created_at TEXT NOT NULL,
                trial_used INT DEFAULT 1,
                last_forecast_date TEXT,
                language_code TEXT DEFAULT 'en'
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_deliveries (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                forecast_date TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                UNIQUE(user_id, forecast_date)
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                telegram_payment_charge_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                plan_key TEXT NOT NULL,
                stars INT NOT NULL,
                days INT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_usage (
                user_id BIGINT NOT NULL,
                usage_date TEXT NOT NULL,
                requests INT NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, usage_date)
            );
        """)


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1",
            user_id,
        )

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
    lang_code: str,
):
    now_utc = datetime.now(UTC)

    existing = await get_user(user_id)

    lang = normalize_lang(lang_code)

    async with db_pool.acquire() as conn:

        if existing:

            await conn.execute("""
                UPDATE users
                SET
                    name = $1,
                    birth_date = $2,
                    birth_time = $3,
                    city = $4,
                    lat = $5,
                    lon = $6,
                    timezone = $7,
                    language_code = $8,
                    is_active = 1
                WHERE user_id = $9
            """,
                name,
                b_date,
                b_time,
                city,
                lat,
                lon,
                tz_str,
                lang,
                user_id,
            )

        else:

            trial_end = (
                now_utc + timedelta(days=7)
            ).isoformat()

            await conn.execute("""
                INSERT INTO users (
                    user_id,
                    name,
                    birth_date,
                    birth_time,
                    city,
                    lat,
                    lon,
                    timezone,
                    trial_until,
                    premium_until,
                    is_active,
                    created_at,
                    trial_used,
                    language_code
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9,
                    NULL,
                    1,
                    $10,
                    1,
                    $11
                )
            """,
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
                lang,
            )


async def add_premium_days(
    user_id: int,
    days: int,
):
    now_utc = datetime.now(UTC)

    async with db_pool.acquire() as conn:

        current_until = await conn.fetchval(
            """
            SELECT premium_until
            FROM users
            WHERE user_id = $1
            """,
            user_id,
        )

        if current_until:

            try:
                current_dt = datetime.fromisoformat(
                    current_until
                )

                if current_dt.tzinfo is None:
                    current_dt = current_dt.replace(
                        tzinfo=UTC
                    )

                base_dt = max(
                    now_utc,
                    current_dt,
                )

            except ValueError:
                base_dt = now_utc

        else:
            base_dt = now_utc

        new_until = (
            base_dt + timedelta(days=days)
        ).isoformat()

        await conn.execute(
            """
            UPDATE users
            SET premium_until = $1,
                is_active = 1
            WHERE user_id = $2
            """,
            new_until,
            user_id,
        )


async def record_payment(
    charge_id: str,
    user_id: int,
    plan_key: str,
    stars: int,
    days: int,
    payload: str,
) -> bool:

    async with db_pool.acquire() as conn:

        result = await conn.fetchval(
            """
            INSERT INTO payments (
                telegram_payment_charge_id,
                user_id,
                plan_key,
                stars,
                days,
                payload,
                created_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7
            )
            ON CONFLICT (
                telegram_payment_charge_id
            )
            DO NOTHING
            RETURNING telegram_payment_charge_id
            """,
            charge_id,
            user_id,
            plan_key,
            stars,
            days,
            payload,
            datetime.now(UTC).isoformat(),
        )

        return result is not None


async def get_user_access(
    user_id: int,
) -> Dict[str, Any]:

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

            prem_dt = datetime.fromisoformat(
                premium_until
            )

            if prem_dt.tzinfo is None:
                prem_dt = prem_dt.replace(
                    tzinfo=UTC
                )

            if prem_dt > now_utc:

                return {
                    "has_access": True,
                    "status": "premium",
                    "days_left": max(
                        1,
                        (prem_dt - now_utc).days + 1,
                    ),
                }

        except ValueError:
            pass

    trial_until = user.get("trial_until")

    if trial_until:

        try:

            trial_dt = datetime.fromisoformat(
                trial_until
            )

            if trial_dt.tzinfo is None:
                trial_dt = trial_dt.replace(
                    tzinfo=UTC
                )

            if trial_dt > now_utc:

                return {
                    "has_access": True,
                    "status": "trial",
                    "days_left": max(
                        1,
                        (trial_dt - now_utc).days + 1,
                    ),
                }

        except ValueError:
            pass

    return {
        "has_access": False,
        "status": "expired",
        "days_left": 0,
    }


async def get_active_users() -> List[Dict[str, Any]]:
    async with db_pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT *
            FROM users
            WHERE is_active = 1
            """
        )

        return [
            dict(row)
            for row in rows
        ]


async def deactivate_user(user_id: int):

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE users
            SET is_active = 0
            WHERE user_id = $1
            """,
            user_id,
        )


async def mark_forecast_sent(
    user_id: int,
    forecast_date: str,
):

    now = datetime.now(UTC).isoformat()

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO daily_deliveries (
                user_id,
                forecast_date,
                sent_at
            )
            VALUES ($1, $2, $3)
            ON CONFLICT (
                user_id,
                forecast_date
            )
            DO NOTHING
            """,
            user_id,
            forecast_date,
            now,
        )

        await conn.execute(
            """
            UPDATE users
            SET last_forecast_date = $1
            WHERE user_id = $2
            """,
            forecast_date,
            user_id,
        )


async def was_forecast_sent(
    user_id: int,
    forecast_date: str,
) -> bool:

    async with db_pool.acquire() as conn:

        val = await conn.fetchval(
            """
            SELECT 1
            FROM daily_deliveries
            WHERE user_id = $1
            AND forecast_date = $2
            """,
            user_id,
            forecast_date,
        )

        return val is not None


async def ai_request_allowed(
    user_id: int,
    limit: int = 15,
) -> bool:

    user = await get_user(user_id)

    if not user:
        return False

    try:
        tz = ZoneInfo(
            user.get("timezone", "UTC")
        )
    except Exception:
        tz = UTC

    local_today = (
        datetime.now(UTC)
        .astimezone(tz)
        .date()
        .isoformat()
    )

    async with db_pool.acquire() as conn:

        current = await conn.fetchval(
            """
            SELECT requests
            FROM ai_usage
            WHERE user_id = $1
            AND usage_date = $2
            """,
            user_id,
            local_today,
        )

        current = current or 0

        if current >= limit:
            return False

        await conn.execute(
            """
            INSERT INTO ai_usage (
                user_id,
                usage_date,
                requests
            )
            VALUES ($1, $2, 1)

            ON CONFLICT (
                user_id,
                usage_date
            )

            DO UPDATE SET
                requests =
                    ai_usage.requests + 1
            """,
            user_id,
            local_today,
        )

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


def angular_distance(
    a: float,
    b: float,
) -> float:

    diff = abs(a - b) % 360.0

    return min(
        diff,
        360.0 - diff,
    )


def deg_to_sign(deg: float) -> str:

    deg = normalize_deg(deg)

    sign_index = int(
        deg // 30
    )

    sign_degree = deg % 30

    whole_degree = int(
        sign_degree
    )

    minutes = int(
        round(
            (sign_degree - whole_degree) * 60
        )
    )

    if minutes >= 60:
        whole_degree += 1
        minutes = 0

    if whole_degree >= 30:
        whole_degree = 0
        sign_index = (
            sign_index + 1
        ) % 12

    return (
        f"{ZODIAC[sign_index]} "
        f"{whole_degree}°{minutes:02d}'"
    )


def get_julian_day(
    dt_utc: datetime,
) -> float:

    dt_utc = dt_utc.astimezone(UTC)

    hour = (
        dt_utc.hour
        + dt_utc.minute / 60.0
        + dt_utc.second / 3600.0
    )

    return swe.julday(
        dt_utc.year,
        dt_utc.month,
        dt_utc.day,
        hour,
    )


def planet_positions(
    dt_utc: datetime,
) -> Dict[str, float]:

    jd = get_julian_day(dt_utc)

    result = {}

    for name, planet_id in TRACKED_PLANETS.items():

        calc = swe.calc_ut(
            jd,
            planet_id,
        )

        result[name] = normalize_deg(
            calc[0][0]
        )

    return result


def get_natal_blueprint(
    birth_utc: datetime,
    lat: float,
    lon: float,
) -> Dict[str, Any]:

    jd = get_julian_day(
        birth_utc
    )

    pos = planet_positions(
        birth_utc
    )

    try:

        _, ascmc = swe.houses(
            jd,
            lat,
            lon,
            b"P",
        )

        asc = normalize_deg(
            ascmc[0]
        )

        mc = normalize_deg(
            ascmc[1]
        )

    except Exception as e:

        print(
            f"[Houses Error] {e}",
            flush=True,
        )

        asc = 0.0
        mc = 0.0

    return {
        "Sun": deg_to_sign(pos["Sun"]),
        "Moon": deg_to_sign(pos["Moon"]),
        "Ascendant": deg_to_sign(asc),
        "MC": deg_to_sign(mc),

        "Mercury": deg_to_sign(
            pos["Mercury"]
        ),

        "Venus": deg_to_sign(
            pos["Venus"]
        ),

        "Mars": deg_to_sign(
            pos["Mars"]
        ),

        "Jupiter": deg_to_sign(
            pos["Jupiter"]
        ),

        "Saturn": deg_to_sign(
            pos["Saturn"]
        ),

        "Uranus": deg_to_sign(
            pos["Uranus"]
        ),

        "Neptune": deg_to_sign(
            pos["Neptune"]
        ),

        "Pluto": deg_to_sign(
            pos["Pluto"]
        ),
    }


def find_transits(
    birth_utc: datetime,
    target_utc: datetime,
    max_results: int = 7,
) -> List[Dict[str, Any]]:

    natal = planet_positions(
        birth_utc
    )

    transit = planet_positions(
        target_utc
    )

    previous = planet_positions(
        target_utc - timedelta(days=1)
    )

    found = []

    weights = {
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
    }

    for t_name, t_deg in transit.items():

        for n_name, n_deg in natal.items():

            if (
                t_name == n_name
                and t_name in {
                    "Sun",
                    "Moon",
                }
            ):
                continue

            diff = angular_distance(
                t_deg,
                n_deg,
            )

            for angle, (
                aspect_name,
                max_orb,
            ) in ASPECTS.items():

                orb = abs(
                    diff - angle
                )

                if orb <= max_orb:

                    previous_diff = (
                        angular_distance(
                            previous[t_name],
                            n_deg,
                        )
                    )

                    previous_orb = abs(
                        previous_diff - angle
                    )

                    motion = (
                        "applying"
                        if orb < previous_orb
                        else "separating"
                    )

                    importance = round(
                        max(
                            1.0,
                            min(
                                10.0,
                                (
                                    max_orb
                                    - orb
                                    + 0.2
                                )
                                * 3.2
                                * weights.get(
                                    t_name,
                                    1.0,
                                ),
                            ),
                        ),
                        1,
                    )

                    found.append({
                        "transit_planet": t_name,
                        "natal_planet": n_name,
                        "aspect": aspect_name,
                        "orb": round(
                            orb,
                            2,
                        ),
                        "motion": motion,
                        "importance": importance,
                    })

                    break

    found.sort(
        key=lambda x: (
            -x["importance"],
            x["orb"],
        )
    )

    return found[:max_results]


def format_transits(
    transits: List[Dict[str, Any]],
) -> str:

    if not transits:

        return (
            "No major tight aspects detected."
        )

    return "\n".join(
        (
            f"- "
            f"{item['transit_planet']} "
            f"{item['aspect']} "
            f"natal "
            f"{item['natal_planet']} "
            f"(orb "
            f"{item['orb']}°, "
            f"{item['motion']})"
        )
        for item in transits
    )


# ============================================================
# GEMINI ENGINE
# ============================================================

SYSTEM_PROMPT = """
You are Aura Astro, an elite psychological astrologer,
self-awareness mentor, and reflective guide.

Your task is to interpret astrology as a psychological
reflection tool, not as deterministic fortune-telling.

CORE RULES:

1. Never use fear-based predictions.
2. Never claim that something is guaranteed to happen.
3. Never say that a person is doomed, cursed, or destined.
4. Never make medical, legal, or financial guarantees.
5. Do not use generic horoscope clichés.
6. Connect astrology with real psychological patterns,
   behavior, communication, motivation, boundaries,
   attention, and decision-making.
7. Give practical recommendations.
8. Be specific to the supplied chart.
9. Never invent planetary positions.
10. Use only the planetary positions and transits supplied
    in the prompt.
11. If birth time is uncertain, avoid overconfidence about
    Ascendant and MC.
12. Every sentence must be complete and properly punctuated.
13. Never leave unfinished sentences.
14. Never switch language inside the answer.
15. Do not mention that you are an AI unless directly asked.
16. Do not repeat the entire chart unnecessarily.
"""


async def call_gemini_safe(
    prompt: str,
    lang: str = "en",
    max_tokens: int = 2000,
) -> str:

    lang = normalize_lang(lang)

    lang_name = (
        "Russian"
        if lang == "ru"
        else "English"
    )

    lang_rule = f"""

CRITICAL LANGUAGE RULE:
Respond entirely and naturally in {lang_name}.
Do not switch languages.
"""

    full_prompt = (
        prompt
        + lang_rule
    )

    for model_candidate in MODELS_CHAIN:

        try:

            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=model_candidate,
                contents=full_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=max_tokens,
                ),
            )

            text = (
                response.text or ""
            ).strip()

            if text:
                return text

        except Exception as e:

            print(
                "[Gemini Failover] "
                f"model={model_candidate} "
                f"error={e}",
                flush=True,
            )

            continue

    if lang == "ru":

        return (
            "Ваша натальная карта — это "
            "инструмент для самонаблюдения, "
            "а не фиксированный сценарий будущего.\n\n"
            "Сегодня полезно обратить внимание "
            "на свои реакции, качество общения "
            "и один конкретный шаг к важной цели."
        )

    return (
        "Your natal chart is best used as a "
        "tool for self-reflection rather than "
        "a fixed script for the future.\n\n"
        "Today, notice your reactions, "
        "communication patterns, and one "
        "concrete step toward an important goal."
    )


# ============================================================
# AI — NATAL CHART
# ============================================================

async def generate_blueprint_text(
    name: str,
    bp: Dict[str, str],
    lang: str,
) -> str:

    chart_data = "\n".join(
        f"- {key}: {value}"
        for key, value in bp.items()
    )

    prompt = f"""
Client name:
{name}

Natal placements:
{chart_data}

Create a deep psychological interpretation
of this person's natal chart.

Length:
approximately 300 words.

Structure exactly into 4 sections.

1. ARCHITECTURE OF PERSONALITY
Analyze the relationship between Sun and Moon.
Explain conscious identity versus emotional needs.

2. OUTER EXPRESSION
Analyze the Ascendant.
Explain first impressions, social behavior,
and how the person naturally enters situations.

3. ACTION AND THINKING
Analyze Mars and Mercury.
Explain decision-making, communication,
assertiveness, motivation, and conflict style.

4. UNIQUE SUPERPOWER
Identify ONE distinctive psychological strength
that emerges from the chart.
Explain why it is useful and how the person
can consciously develop it.

Important:
Do not simply describe zodiac signs.
Connect placements together.
Do not make deterministic predictions.
Make the reading feel personal and specific.
Complete every sentence.
"""

    return await call_gemini_safe(
        prompt,
        lang,
        2400,
    )


# ============================================================
# AI — DAILY FORECAST
# ============================================================

async def generate_daily_forecast(
    name: str,
    bp: Dict[str, str],
    transits: List[Dict[str, Any]],
    target_date: date,
    lang: str,
) -> str:

    chart_data = "\n".join(
        f"- {key}: {value}"
        for key, value in bp.items()
    )

    prompt = f"""
Client:
{name}

Date:
{target_date.isoformat()}

Natal chart:
{chart_data}

Important current transits:
{format_transits(transits)}

Create a personalized daily psychological
astrology forecast.

Length:
approximately 220 words.

Structure exactly:

DAILY THEME
One clear meaningful title.

PSYCHOLOGICAL CLIMATE
Two complete sentences describing the
likely psychological atmosphere and
attention patterns.

RELATIONSHIPS
One or two sentences about communication,
boundaries, emotional reactions, or intimacy.

TACTICAL DIRECTIVES
Exactly two bullet points.
Each must be practical and actionable today.

REFLECTIVE INQUIRY
One thoughtful question for self-reflection.

Do not predict specific events.
Do not use fear.
Do not say "something unexpected will happen".
Focus on choices, patterns, attention,
communication, and behavior.

Complete every sentence.
"""

    return await call_gemini_safe(
        prompt,
        lang,
        2200,
    )


# ============================================================
# AI — ASK MY CHART
# ============================================================

async def generate_chart_answer(
    user: Dict[str, Any],
    bp: Dict[str, str],
    transits: List[Dict[str, Any]],
    question: str,
) -> str:

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
    )

    chart_data = "\n".join(
        f"- {key}: {value}"
        for key, value in bp.items()
    )

    prompt = f"""
Client:
{user['name']}

Question:
{question}

Natal chart:
{chart_data}

Current transits:
{format_transits(transits)}

Answer the client's question as a
psychological astrology mentor.

Length:
approximately 220 words.

Requirements:

1. Directly address the actual question.
2. Connect the answer to one or two
   relevant natal placements.
3. If transits are relevant, explain
   how they may symbolically highlight
   the psychological theme.
4. Give exactly two practical action steps.
5. Do not make deterministic predictions.
6. Do not scare the client.
7. Do not simply repeat their question.
8. Do not invent chart information.
9. Complete every sentence.
"""

    return await call_gemini_safe(
        prompt,
        lang,
        2200,
    )


# ============================================================
# UTILITIES
# ============================================================

def parse_birth_dt(
    user: Dict[str, Any],
) -> datetime:

    tz = ZoneInfo(
        user["timezone"]
    )

    local_dt = datetime.fromisoformat(
        f"{user['birth_date']}T"
        f"{user['birth_time']}"
    )

    local_dt = local_dt.replace(
        tzinfo=tz
    )

    return local_dt.astimezone(
        UTC
    )


def get_status_str(
    access: Dict[str, Any],
    lang: str,
) -> str:

    if not access["has_access"]:
        return t(
            "status_exp",
            lang,
        )

    if access["status"] == "premium":

        status = t(
            "status_prem",
            lang,
        )

    else:

        status = t(
            "status_trial",
            lang,
        )

    return (
        f"{status} · "
        f"{t('days_left', lang, days=access['days_left'])}"
    )


async def send_long_message(
    chat_id: int,
    text: str,
    reply_markup=None,
):

    if len(text) <= 3900:

        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )

        return

    paragraphs = text.split(
        "\n\n"
    )

    parts = []

    current = ""

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if not paragraph:
            continue

        candidate = (
            f"{current}\n\n{paragraph}"
            if current
            else paragraph
        )

        if len(candidate) <= 3800:

            current = candidate

        else:

            if current:
                parts.append(
                    current
                )

            # Если отдельный абзац слишком длинный.
            if len(paragraph) > 3800:

                for i in range(
                    0,
                    len(paragraph),
                    3700,
                ):
                    parts.append(
                        paragraph[
                            i:i + 3700
                        ]
                    )

                current = ""

            else:

                current = paragraph

    if current:
        parts.append(current)

    for index, part in enumerate(parts):

        markup = (
            reply_markup
            if index == len(parts) - 1
            else None
        )

        await bot.send_message(
            chat_id=chat_id,
            text=part,
            reply_markup=markup,
        )


async def safe_delete_message(
    message: Optional[types.Message],
):

    if not message:
        return

    try:
        await message.delete()
    except Exception:
        pass


# ============================================================
# FSM
# ============================================================

class Registration(StatesGroup):

    name = State()
    birth_date = State()
    birth_time = State()
    birth_city = State()


class AskChartState(StatesGroup):

    question = State()


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def cmd_start(
    message: types.Message,
    state: FSMContext,
):

    await state.clear()

    lang = normalize_lang(
        message.from_user.language_code
    )

    user = await get_user(
        message.from_user.id
    )

    if user:

        user_lang = normalize_lang(
            user.get(
                "language_code",
                lang,
            )
        )

        access = await get_user_access(
            message.from_user.id
        )

        msg = t(
            "welcome_back",
            user_lang,
            name=html.escape(
                user["name"]
            ),
            status=get_status_str(
                access,
                user_lang,
            ),
        )

        await message.answer(
            msg,
            reply_markup=main_menu_keyboard(
                user_lang
            ),
        )

        return

    await state.update_data(
        lang_code=lang
    )

    await message.answer(
        t(
            "start_intro",
            lang,
        )
    )

    await state.set_state(
        Registration.name
    )


# ============================================================
# REGISTRATION — NAME
# ============================================================

@dp.message(Registration.name)
async def process_name(
    message: types.Message,
    state: FSMContext,
):

    data = await state.get_data()

    lang = normalize_lang(
        data.get(
            "lang_code",
            "en",
        )
    )

    name = (
        message.text or ""
    ).strip()

    name = name[:60]

    if not name:
        return

    await state.update_data(
        name=name
    )

    await message.answer(
        t(
            "ask_birth_date",
            lang,
        ),
        parse_mode="Markdown",
    )

    await state.set_state(
        Registration.birth_date
    )


# ============================================================
# REGISTRATION — DATE
# ============================================================

@dp.message(Registration.birth_date)
async def process_date(
    message: types.Message,
    state: FSMContext,
):

    data = await state.get_data()

    lang = normalize_lang(
        data.get(
            "lang_code",
            "en",
        )
    )

    value = (
        message.text or ""
    ).strip()

    try:

        birth_date = date.fromisoformat(
            value
        )

        today = date.today()

        if birth_date > today:
            raise ValueError

        if birth_date.year < 1900:
            raise ValueError

    except ValueError:

        await message.answer(
            t(
                "invalid_date",
                lang,
            ),
            parse_mode="Markdown",
        )

        return

    await state.update_data(
        birth_date=value
    )

    await message.answer(
        t(
            "ask_birth_time",
            lang,
        ),
        parse_mode="Markdown",
    )

    await state.set_state(
        Registration.birth_time
    )


# ============================================================
# REGISTRATION — TIME
# ============================================================

@dp.message(Registration.birth_time)
async def process_time(
    message: types.Message,
    state: FSMContext,
):

    data = await state.get_data()

    lang = normalize_lang(
        data.get(
            "lang_code",
            "en",
        )
    )

    value = (
        message.text or ""
    ).strip()

    try:

        parsed_time = time.fromisoformat(
            value
        )

        # Разрешаем только HH:MM или HH:MM:SS.
        if len(value) == 5:
            normalized_time = (
                value + ":00"
            )

        elif len(value) == 8:
            normalized_time = value

        else:
            raise ValueError

        # Проверяем, что секунды тоже корректны.
        parsed_time = time.fromisoformat(
            normalized_time
        )

        _ = parsed_time

    except ValueError:

        await message.answer(
            t(
                "invalid_time",
                lang,
            ),
            parse_mode="Markdown",
        )

        return

    await state.update_data(
        birth_time=normalized_time
    )

    await message.answer(
        t(
            "ask_city",
            lang,
        ),
        parse_mode="Markdown",
    )

    await state.set_state(
        Registration.birth_city
    )


# ============================================================
# REGISTRATION — CITY
# ============================================================

@dp.message(Registration.birth_city)
async def process_city(
    message: types.Message,
    state: FSMContext,
):

    data = await state.get_data()

    lang = normalize_lang(
        data.get(
            "lang_code",
            "en",
        )
    )

    city_query = (
        message.text or ""
    ).strip()[:150]

    wait_message = await message.answer(
        t(
            "calc_coords",
            lang,
        )
    )

    try:

        location = await asyncio.to_thread(
            geolocator.geocode,
            city_query,
            language="en",
        )

    except Exception as e:

        print(
            f"[Geocoding Error] {e}",
            flush=True,
        )

        location = None

    if not location:

        await safe_delete_message(
            wait_message
        )

        await message.answer(
            t(
                "city_not_found",
                lang,
            )
        )

        return

    lat = float(
        location.latitude
    )

    lon = float(
        location.longitude
    )

    tz_str = (
        tf.timezone_at(
            lng=lon,
            lat=lat,
        )
        or "UTC"
    )

    try:

        birth_local = datetime.fromisoformat(
            f"{data['birth_date']}T"
            f"{data['birth_time']}"
        )

        birth_local = birth_local.replace(
            tzinfo=ZoneInfo(
                tz_str
            )
        )

        birth_utc = (
            birth_local.astimezone(
                UTC
            )
        )

    except Exception as e:

        print(
            f"[Timezone Error] {e}",
            flush=True,
        )

        await safe_delete_message(
            wait_message
        )

        await message.answer(
            t(
                "tz_error",
                lang,
            )
        )

        return

    try:

        bp = get_natal_blueprint(
            birth_utc,
            lat,
            lon,
        )

        analysis = await generate_blueprint_text(
            data["name"],
            bp,
            lang,
        )

    except Exception as e:

        print(
            f"[Blueprint Error] {e}",
            flush=True,
        )

        await safe_delete_message(
            wait_message
        )

        await message.answer(
            t(
                "calc_error",
                lang,
            )
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
        lang,
    )

    await safe_delete_message(
        wait_message
    )

    card = build_chart_card(
        bp,
        data["name"],
        lang,
        include_all=False,
    )

    full_text = (
        card
        + "\n\n"
        + "━━━━━━━━━━━━━━━━━━━━"
        + "\n\n"
        + analysis
        + "\n\n"
        + t(
            "trial_activated",
            lang,
        )
    )

    await send_long_message(
        message.chat.id,
        full_text,
        main_menu_keyboard(
            lang
        ),
    )

    await state.clear()


# ============================================================
# MAIN MENU
# ============================================================

@dp.callback_query(
    F.data == "menu"
)
async def cb_menu(
    cb: types.CallbackQuery,
):

    await cb.answer()

    user = await get_user(
        cb.from_user.id
    )

    if not user:

        await cb.message.answer(
            "Please /start first."
        )

        return

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
    )

    access = await get_user_access(
        cb.from_user.id
    )

    await cb.message.edit_text(
        t(
            "welcome_back",
            lang,
            name=html.escape(
                user["name"]
            ),
            status=get_status_str(
                access,
                lang,
            ),
        ),
        reply_markup=main_menu_keyboard(
            lang
        ),
    )


# ============================================================
# NATAL CHART
# ============================================================

@dp.callback_query(
    F.data == "chart"
)
async def cb_chart(
    cb: types.CallbackQuery,
):

    await cb.answer()

    user = await get_user(
        cb.from_user.id
    )

    if not user:
        return

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
    )

    status_message = await cb.message.answer(
        t(
            "analyzing",
            lang,
        )
    )

    try:

        birth_utc = parse_birth_dt(
            user
        )

        bp = get_natal_blueprint(
            birth_utc,
            float(user["lat"]),
            float(user["lon"]),
        )

        analysis = await generate_blueprint_text(
            user["name"],
            bp,
            lang,
        )

    except Exception as e:

        print(
            f"[Chart Error] {e}",
            flush=True,
        )

        await safe_delete_message(
            status_message
        )

        await cb.message.answer(
            t(
                "calc_error",
                lang,
            )
        )

        return

    await safe_delete_message(
        status_message
    )

    card = build_chart_card(
        bp,
        user["name"],
        lang,
        include_all=True,
    )

    full_text = (
        card
        + "\n\n"
        + f"📍 {t('location', lang)}: "
        + html.escape(
            user["city"]
        )
        + "\n"
        + "━━━━━━━━━━━━━━━━━━━━"
        + "\n\n"
        + analysis
    )

    await send_long_message(
        cb.message.chat.id,
        full_text,
        back_menu_keyboard(
            lang
        ),
    )


# ============================================================
# FORECAST
# ============================================================

@dp.callback_query(
    F.data.in_({
        "forecast",
        "tomorrow",
    })
)
async def cb_forecast(
    cb: types.CallbackQuery,
):

    await cb.answer()

    user = await get_user(
        cb.from_user.id
    )

    if not user:
        return

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
    )

    access = await get_user_access(
        cb.from_user.id
    )

    if not access["has_access"]:

        await cb.message.answer(
            t(
                "paywall_msg",
                lang,
            ),
            reply_markup=pricing_keyboard(
                lang
            ),
        )

        return

    try:

        user_tz = ZoneInfo(
            user["timezone"]
        )

    except Exception:

        user_tz = UTC

    local_date = (
        datetime.now(UTC)
        .astimezone(user_tz)
        .date()
    )

    if cb.data == "forecast":

        target_date = local_date

    else:

        target_date = (
            local_date
            + timedelta(days=1)
        )

    status_message = await cb.message.answer(
        t(
            "analyzing",
            lang,
        )
    )

    try:

        birth_utc = parse_birth_dt(
            user
        )

        target_utc = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            12,
            0,
            tzinfo=UTC,
        )

        bp = get_natal_blueprint(
            birth_utc,
            float(user["lat"]),
            float(user["lon"]),
        )

        transits = find_transits(
            birth_utc,
            target_utc,
        )

        reading = await generate_daily_forecast(
            user["name"],
            bp,
            transits,
            target_date,
            lang,
        )

    except Exception as e:

        print(
            f"[Forecast Error] {e}",
            flush=True,
        )

        await safe_delete_message(
            status_message
        )

        await cb.message.answer(
            t(
                "calc_error",
                lang,
            )
        )

        return

    await safe_delete_message(
        status_message
    )

    title = (
        t(
            "forecast_title",
            lang,
        )
        if cb.data == "forecast"
        else t(
            "tomorrow_title",
            lang,
        )
    )

    header = (
        f"{title}\n"
        f"📅 {target_date.strftime('%d.%m.%Y')}\n\n"
    )

    await send_long_message(
        cb.message.chat.id,
        header + reading,
        back_menu_keyboard(
            lang
        ),
    )


# ============================================================
# ASK MY CHART
# ============================================================

@dp.callback_query(
    F.data == "ask_chart"
)
async def cb_ask(
    cb: types.CallbackQuery,
    state: FSMContext,
):

    await cb.answer()

    user = await get_user(
        cb.from_user.id
    )

    if not user:
        return

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
    )

    access = await get_user_access(
        cb.from_user.id
    )

    if not access["has_access"]:

        await cb.message.answer(
            t(
                "paywall_msg",
                lang,
            ),
            reply_markup=pricing_keyboard(
                lang
            ),
        )

        return

    await state.set_state(
        AskChartState.question
    )

    await cb.message.answer(
        t(
            "ask_prompt",
            lang,
        )
    )


@dp.message(
    AskChartState.question
)
async def process_question(
    message: types.Message,
    state: FSMContext,
):

    question = (
        message.text or ""
    ).strip()

    user = await get_user(
        message.from_user.id
    )

    if not user or not question:

        await state.clear()

        return

    question = question[:1500]

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
    )

    access = await get_user_access(
        message.from_user.id
    )

    if not access["has_access"]:

        await state.clear()

        await message.answer(
            t(
                "paywall_msg",
                lang,
            ),
            reply_markup=pricing_keyboard(
                lang
            ),
        )

        return

    if not await ai_request_allowed(
        message.from_user.id
    ):

        await state.clear()

        await message.answer(
            t(
                "limit_reached",
                lang,
            ),
            reply_markup=main_menu_keyboard(
                lang
            ),
        )

        return

    status_message = await message.answer(
        t(
            "analyzing",
            lang,
        )
    )

    try:

        birth_utc = parse_birth_dt(
            user
        )

        bp = get_natal_blueprint(
            birth_utc,
            float(user["lat"]),
            float(user["lon"]),
        )

        transits = find_transits(
            birth_utc,
            datetime.now(UTC),
        )

        answer = await generate_chart_answer(
            user,
            bp,
            transits,
            question,
        )

    except Exception as e:

        print(
            f"[Ask Chart Error] {e}",
            flush=True,
        )

        await safe_delete_message(
            status_message
        )

        await message.answer(
            t(
                "calc_error",
                lang,
            ),
            reply_markup=main_menu_keyboard(
                lang
            ),
        )

        await state.clear()

        return

    await safe_delete_message(
        status_message
    )

    full_text = (
        f"💬 {html.escape(question)}\n\n"
        f"{answer}"
    )

    await send_long_message(
        message.chat.id,
        full_text,
        main_menu_keyboard(
            lang
        ),
    )

    await state.clear()


# ============================================================
# TOPICS
# ============================================================

@dp.callback_query(
    F.data.in_({
        "topic_relationships",
        "topic_career",
        "topic_money",
    })
)
async def cb_topics(
    cb: types.CallbackQuery,
):

    await cb.answer()

    user = await get_user(
        cb.from_user.id
    )

    if not user:
        return

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
    )

    access = await get_user_access(
        cb.from_user.id
    )

    if not access["has_access"]:

        await cb.message.answer(
            t(
                "paywall_msg",
                lang,
            ),
            reply_markup=pricing_keyboard(
                lang
            ),
        )

        return

    topic_data = {

        "topic_relationships": {
            "title_ru": "❤️ ОТНОШЕНИЯ И БЛИЗОСТЬ",
            "title_en": "❤️ RELATIONSHIPS & INTIMACY",
            "focus": (
                "Venus, Moon, Mars, "
                "emotional intimacy, attraction, "
                "attachment patterns, communication, "
                "boundaries and conflict resolution."
            ),
        },

        "topic_career": {
            "title_ru": "💼 КАРЬЕРА И ПРИЗВАНИЕ",
            "title_en": "💼 CAREER & VOCATION",
            "focus": (
                "MC, Saturn, Mars, Sun, "
                "professional ambition, leadership, "
                "discipline, motivation, "
                "work identity and strategic career choices."
            ),
        },

        "topic_money": {
            "title_ru": "💰 ФИНАНСЫ И РЕСУРСЫ",
            "title_en": "💰 MONEY & RESOURCES",
            "focus": (
                "Jupiter, Venus, Saturn, "
                "resource management, spending habits, "
                "risk perception, self-worth, "
                "financial discipline and long-term strategy."
            ),
        },
    }

    info = topic_data[
        cb.data
    ]

    status_message = await cb.message.answer(
        t(
            "analyzing",
            lang,
        )
    )

    try:

        birth_utc = parse_birth_dt(
            user
        )

        bp = get_natal_blueprint(
            birth_utc,
            float(user["lat"]),
            float(user["lon"]),
        )

        transits = find_transits(
            birth_utc,
            datetime.now(UTC),
        )

        chart_data = "\n".join(
            f"- {key}: {value}"
            for key, value in bp.items()
        )

        prompt = f"""
Client:
{user['name']}

Analysis domain:
{info['focus']}

Natal chart:
{chart_data}

Current transits:
{format_transits(transits)}

Create a deep psychological and strategic
astrology reading focused strictly on the
selected domain.

Length:
approximately 250 words.

Structure exactly:

1. CORE PATTERN
Explain the person's natural psychological
pattern in this area.

2. CURRENT MOMENTUM
Explain which current transits symbolically
highlight the theme.

3. TACTICAL RECOMMENDATIONS
Give exactly two practical recommendations.

Do not make deterministic predictions.
Do not invent houses that were not provided.
Do not use clichés.
Do not give guarantees.
Complete every sentence.
"""

        reading = await call_gemini_safe(
            prompt,
            lang,
            2200,
        )

    except Exception as e:

        print(
            f"[Topic Error] {e}",
            flush=True,
        )

        await safe_delete_message(
            status_message
        )

        await cb.message.answer(
            t(
                "calc_error",
                lang,
            )
        )

        return

    await safe_delete_message(
        status_message
    )

    title = (
        info["title_ru"]
        if lang == "ru"
        else info["title_en"]
    )

    full_text = (
        f"✨ {title}\n\n"
        f"{reading}"
    )

    await send_long_message(
        cb.message.chat.id,
        full_text,
        back_menu_keyboard(
            lang
        ),
    )


# ============================================================
# SUBSCRIPTION
# ============================================================

@dp.callback_query(
    F.data == "subscription"
)
async def cb_sub(
    cb: types.CallbackQuery,
):

    await cb.answer()

    user = await get_user(
        cb.from_user.id
    )

    if not user:

        return

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
    )

    access = await get_user_access(
        cb.from_user.id
    )

    txt = (
        f"{t('subscription_title', lang)}\n\n"
        f"{t('status_prem', lang) if access['status'] == 'premium' else t('status_trial', lang) if access['status'] == 'trial' else t('status_exp', lang)}"
        f"\n"
        f"{t('days_left', lang, days=access['days_left']) if access['has_access'] else ''}"
        f"\n\n"
        f"{t('subscription_choose', lang)}"
    )

    await cb.message.edit_text(
        txt,
        reply_markup=pricing_keyboard(
            lang
        ),
    )


# ============================================================
# PAYMENTS — TELEGRAM STARS
# ============================================================

@dp.callback_query(
    F.data.startswith("buy_plan_")
)
async def process_buy(
    cb: types.CallbackQuery,
):

    await cb.answer()

    plan_key = cb.data.replace(
        "buy_",
        "",
        1,
    )

    plan = PRICING_PLANS.get(
        plan_key
    )

    if not plan:
        return

    payload = (
        f"{plan_key}:"
        f"{cb.from_user.id}"
    )

    prices = [
        LabeledPrice(
            label=plan["title"],
            amount=plan["stars"],
        )
    ]

    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=plan["title"],
        description=plan["description"],
        payload=payload,
        currency="XTR",
        prices=prices,
        provider_token="",
    )


@dp.pre_checkout_query()
async def process_pre_checkout(
    query: PreCheckoutQuery,
):

    try:

        await bot.answer_pre_checkout_query(
            query.id,
            ok=True,
        )

    except Exception as e:

        print(
            f"[PreCheckout Error] {e}",
            flush=True,
        )


@dp.message(
    F.successful_payment
)
async def process_success(
    message: types.Message,
):

    payment = (
        message.successful_payment
    )

    if not payment:
        return

    payload = (
        payment.invoice_payload
        or ""
    )

    plan_key = payload.split(
        ":",
        1,
    )[0]

    plan = PRICING_PLANS.get(
        plan_key
    )

    if not plan:

        print(
            f"[Payment Error] "
            f"Unknown plan: {plan_key}",
            flush=True,
        )

        return

    charge_id = (
        payment.telegram_payment_charge_id
    )

    inserted = await record_payment(
        charge_id=charge_id,
        user_id=message.from_user.id,
        plan_key=plan_key,
        stars=plan["stars"],
        days=plan["days"],
        payload=payload,
    )

    user = await get_user(
        message.from_user.id
    )

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
        if user
        else "en"
    )

    # Защита от повторного Telegram update.
    if not inserted:

        await message.answer(
            t(
                "payment_already_processed",
                lang,
            ),
            reply_markup=main_menu_keyboard(
                lang
            ),
        )

        return

    await add_premium_days(
        message.from_user.id,
        plan["days"],
    )

    await message.answer(
        t(
            "payment_success",
            lang,
            days=plan["days"],
        ),
        reply_markup=main_menu_keyboard(
            lang
        ),
    )


# ============================================================
# DAILY FORECAST
# ============================================================

async def send_daily_cycle():

    users = await get_active_users()

    now_utc = datetime.now(UTC)

    for user in users:

        user_id = user["user_id"]

        try:

            try:
                tz = ZoneInfo(
                    user["timezone"]
                )
            except Exception:
                tz = UTC

            local_now = (
                now_utc.astimezone(tz)
            )

            # Отправляем в 20:00.
            if local_now.hour != 20:
                continue

            forecast_date = (
                local_now.date()
                + timedelta(days=1)
            )

            forecast_key = (
                forecast_date.isoformat()
            )

            if await was_forecast_sent(
                user_id,
                forecast_key,
            ):
                continue

            lang = normalize_lang(
                user.get(
                    "language_code",
                    "en",
                )
            )

            access = await get_user_access(
                user_id
            )

            if not access["has_access"]:

                # Чтобы не спамить paywall
                # каждый час при проблемах с cron.
                await bot.send_message(
                    user_id,
                    t(
                        "paywall_msg",
                        lang,
                    ),
                    reply_markup=pricing_keyboard(
                        lang
                    ),
                )

                # Считаем этот день обработанным.
                await mark_forecast_sent(
                    user_id,
                    forecast_key,
                )

                await asyncio.sleep(
                    0.3
                )

                continue

            birth_utc = parse_birth_dt(
                user
            )

            target_utc = datetime(
                forecast_date.year,
                forecast_date.month,
                forecast_date.day,
                12,
                0,
                tzinfo=UTC,
            )

            bp = get_natal_blueprint(
                birth_utc,
                float(user["lat"]),
                float(user["lon"]),
            )

            transits = find_transits(
                birth_utc,
                target_utc,
            )

            reading = await generate_daily_forecast(
                user["name"],
                bp,
                transits,
                forecast_date,
                lang,
            )

            title = t(
                "tomorrow_title",
                lang,
            )

            message_text = (
                f"{title}\n"
                f"📅 {forecast_date.strftime('%d.%m.%Y')}\n\n"
                f"{reading}\n\n"
                f"⏳ "
                f"{get_status_str(access, lang)}"
            )

            await send_long_message(
                user_id,
                message_text,
                main_menu_keyboard(
                    lang
                ),
            )

            await mark_forecast_sent(
                user_id,
                forecast_key,
            )

            await asyncio.sleep(
                1.0
            )

        except TelegramForbiddenError:

            print(
                f"[Scheduler] "
                f"User {user_id} blocked bot.",
                flush=True,
            )

            await deactivate_user(
                user_id
            )

        except TelegramRetryAfter as e:

            print(
                f"[Scheduler] "
                f"Rate limit: "
                f"{e.retry_after}s",
                flush=True,
            )

            await asyncio.sleep(
                e.retry_after
            )

        except Exception as e:

            print(
                f"[Scheduler User "
                f"{user_id}] {e}",
                flush=True,
            )


# ============================================================
# WEB SERVER
# ============================================================

async def health_check(
    request,
):

    return web.Response(
        text="Aura Astro v3.0 Online",
        status=200,
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health_check,
    )

    app.router.add_get(
        "/health",
        health_check,
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()


# ============================================================
# MAIN
# ============================================================

async def main():

    await init_db()

    await start_web_server()

    scheduler = AsyncIOScheduler(
        timezone="UTC"
    )

    scheduler.add_job(
        send_daily_cycle,
        "cron",
        minute=0,
        id="daily_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    print(
        "🚀 Aura Astro v3.0 is running.",
        flush=True,
    )

    print(
        f"🤖 Gemini model: "
        f"{DEFAULT_GEMINI_MODEL}",
        flush=True,
    )

    await dp.start_polling(
        bot
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except (
        KeyboardInterrupt,
        SystemExit,
    ):

        print(
            "Bot safely shutdown.",
            flush=True,
        )
