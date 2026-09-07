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
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODEL_NAME = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.0-flash")

if not BOT_TOKEN or not GEMINI_KEY:
    print("[CRITICAL] TELEGRAM_BOT_TOKEN and GEMINI_API_KEY are required.", flush=True)
    sys.exit(1)

if not DATABASE_URL:
    print("[CRITICAL] DATABASE_URL is required for Neon PostgreSQL.", flush=True)
    sys.exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
geolocator = Nominatim(user_agent="aura_astro_engine_prod_v2", timeout=7)
tf = TimezoneFinder()
ai_client = genai.Client(api_key=GEMINI_KEY)
UTC = timezone.utc
db_pool: Optional[asyncpg.Pool] = None

# Кэш доступных на аккаунте моделей
AVAILABLE_MODELS: List[str] = []

def init_available_models():
    global AVAILABLE_MODELS
    try:
        models = ai_client.models.list()
        found = []
        for m in models:
            m_name = getattr(m, "name", "")
            if "generateContent" in getattr(m, "supported_generation_methods", []) or "flash" in m_name:
                cleaned = m_name.replace("models/", "")
                found.append(cleaned)
        if found:
            AVAILABLE_MODELS = found
            print(f"[Gemini Info] Detected available models: {AVAILABLE_MODELS}", flush=True)
    except Exception as e:
        print(f"[Gemini Info] ListModels check skipped: {e}", flush=True)

# ============================================================
# LOCALIZATION (I18N)
# ============================================================
TEXTS = {
    "ru": {
        "welcome_back": "✨ С возвращением, {name}!\n\nСтатус: {status}\n\nЧто хотите изучить?",
        "start_intro": (
            "✨ Добро пожаловать в Aura Astro.\n\n"
            "Ваш персональный астрологический проводник объединяет точные расчеты "
            "Swiss Ephemeris и глубокую психологию.\n\n"
            "Давайте построим вашу натальную карту.\n\n"
            "Как к вам обращаться (ваше имя)?"
        ),
        "ask_birth_date": "Укажите вашу **дату рождения** в формате `ГГГГ-ММ-ДД` (например, `1994-08-23`):",
        "invalid_date": "⚠️ Неверный формат даты. Пожалуйста, используйте `ГГГГ-ММ-ДД` (например, `1995-11-04`):",
        "ask_birth_time": (
            "В какое **время** вы родились?\n"
            "Используйте 24-часовой формат `ЧЧ:ММ` (например, `14:30`).\n"
            "_Если точное время неизвестно, напишите `12:00`._"
        ),
        "invalid_time": "⚠️ Неверный формат времени. Введите `ЧЧ:ММ` (например, `08:45` или `12:00`):",
        "ask_city": "Где вы родились? Укажите **город и страну** (например, `Москва, Россия` или `Минск, Беларусь`):",
        "calc_coords": "🔭 Считаем координаты и карту...",
        "city_not_found": "⚠️ Город не найден. Попробуйте ввести точнее: Город, Страна.",
        "tz_error": "⚠️ Не удалось определить часовой пояс. Попробуйте указать ближайший крупный город.",
        "calc_error": "⚠️ Ошибка при расчете карты. Пожалуйста, попробуйте снова.",
        "trial_activated": (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🎁 Активирован полный доступ на 7 дней.\n"
            "Ваш ежедневный прогноз будет приходить в 20:00 по местному времени."
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
        "limit_reached": "Вы исчерпали лимит запросов к карте на сегодня. Приходите завтра!",
        "paywall_msg": "🔒 Бесплатный доступ завершен.\n\nОформите подписку, чтобы продолжить получать прогнозы и задавать вопросы карте.",
        "ask_prompt": "💬 СПРОСИТЬ КАРТУ\n\nЗадайте один вопрос о себе, работе, отношениях или выборе:\n\n_Например: В чем корень моих сомнений при смене работы?_",
        "analyzing": "🧠 Анализирую карту и транзиты планет...",
        "plan_1m_btn": "⭐ 1 месяц — 199 Stars",
        "plan_3m_btn": "✨ 3 месяца — 450 Stars",
        "plan_6m_btn": "⚡ 6 месяцев — 800 Stars",
        "plan_1y_btn": "👑 1 год — 1200 Stars",
    },
    "en": {
        "welcome_back": "✨ Welcome back, {name}!\n\nStatus: {status}\n\nWhat would you like to explore?",
        "start_intro": (
            "✨ Welcome to Aura Astro.\n\n"
            "Your personal astrology companion combines Swiss Ephemeris calculations "
            "with psychological AI guidance.\n\n"
            "Let's build your personal chart.\n\n"
            "What is your preferred name?"
        ),
        "ask_birth_date": "What is your **date of birth**? Format: `YYYY-MM-DD` (e.g. `1994-08-23`):",
        "invalid_date": "⚠️ Invalid date. Please use `YYYY-MM-DD` (e.g. `1995-11-04`):",
        "ask_birth_time": (
            "What **time** were you born?\n"
            "Use 24-hour format `HH:MM` (e.g. `14:30`).\n"
            "_If unknown, simply type `12:00`._"
        ),
        "invalid_time": "⚠️ Invalid time. Please use `HH:MM` (e.g. `08:45` or `12:00`):",
        "ask_city": "Where were you born? Enter **city and country** (e.g. `London, UK` or `Chicago, USA`):",
        "calc_coords": "🔭 Calculating your chart coordinates...",
        "city_not_found": "⚠️ Location not found. Please try again as City, Country.",
        "tz_error": "⚠️ Couldn't resolve historical timezone. Try a nearby larger city.",
        "calc_error": "⚠️ Something went wrong calculating the chart. Please try again.",
        "trial_activated": (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🎁 7-Day Full Access Activated.\n"
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
        "limit_reached": "You've reached today's AI guidance limit. Come back tomorrow!",
        "paywall_msg": "🔒 Your free access has ended.\n\nChoose a plan to continue receiving forecasts and asking your chart.",
        "ask_prompt": "💬 ASK MY CHART\n\nAsk one question about yourself, relationships, work, or decisions:\n\n_Example: Why do I keep overthinking conversations?_",
        "analyzing": "🧠 Looking at your chart and current transits...",
        "plan_1m_btn": "⭐ 1 Month — 199 Stars",
        "plan_3m_btn": "✨ 3 Months — 450 Stars",
        "plan_6m_btn": "⚡ 6 Months — 800 Stars",
        "plan_1y_btn": "👑 1 Year — 1200 Stars",
    }
}

def t(key: str, lang: str = "en", **kwargs) -> str:
    lang = "ru" if lang.startswith("ru") else "en"
    text = TEXTS.get(lang, TEXTS["en"]).get(key, TEXTS["en"].get(key, ""))
    return text.format(**kwargs) if kwargs else text

# ============================================================
# PLANS & KEYBOARDS
# ============================================================
PRICING_PLANS = {
    "plan_1m": {"title": "🌟 1 Month Access", "description": "30 days of forecasts & Ask My Chart.", "stars": 199, "days": 30},
    "plan_3m": {"title": "✨ 3 Months Access", "description": "90 days of complete astro guidance.", "stars": 450, "days": 90},
    "plan_6m": {"title": "⚡ 6 Months Access", "description": "180 days of forecasts & transit tracking.", "stars": 800, "days": 180},
    "plan_1y": {"title": "👑 1 Year Access", "description": "365 days of complete astrology coaching.", "stars": 1200, "days": 365},
}

def pricing_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("plan_1m_btn", lang), callback_data="buy_plan_1m")],
        [InlineKeyboardButton(text=t("plan_3m_btn", lang), callback_data="buy_plan_3m")],
        [InlineKeyboardButton(text=t("plan_6m_btn", lang), callback_data="buy_plan_6m")],
        [InlineKeyboardButton(text=t("plan_1y_btn", lang), callback_data="buy_plan_1y")],
        [InlineKeyboardButton(text=t("btn_menu", lang), callback_data="menu")],
    ])

def main_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_chart", lang), callback_data="chart"), InlineKeyboardButton(text=t("btn_forecast", lang), callback_data="forecast")],
        [InlineKeyboardButton(text=t("btn_tomorrow", lang), callback_data="tomorrow"), InlineKeyboardButton(text=t("btn_ask", lang), callback_data="ask_chart")],
        [InlineKeyboardButton(text=t("btn_rel", lang), callback_data="topic_relationships"), InlineKeyboardButton(text=t("btn_career", lang), callback_data="topic_career")],
        [InlineKeyboardButton(text=t("btn_money", lang), callback_data="topic_money"), InlineKeyboardButton(text=t("btn_sub", lang), callback_data="subscription")],
    ])

def back_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_menu", lang), callback_data="menu")]
    ])

# ============================================================
# DATABASE (Neon PostgreSQL via asyncpg)
# ============================================================
async def init_db():
    global db_pool
    clean_url = DATABASE_URL
    if clean_url.startswith("postgres://"):
        clean_url = clean_url.replace("postgres://", "postgresql://", 1)

    db_pool = await asyncpg.create_pool(clean_url, min_size=1, max_size=5)

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
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        return dict(row) if row else None

async def save_or_update_user(user_id: int, name: str, b_date: str, b_time: str, city: str, lat: float, lon: float, tz_str: str, lang_code: str):
    now_utc = datetime.now(UTC)
    existing = await get_user(user_id)
    lang = "ru" if (lang_code or "").startswith("ru") else "en"

    async with db_pool.acquire() as conn:
        if existing:
            await conn.execute("""
                UPDATE users
                SET name = $1, birth_date = $2, birth_time = $3, city = $4,
                    lat = $5, lon = $6, timezone = $7, language_code = $8, is_active = 1
                WHERE user_id = $9
            """, name, b_date, b_time, city, lat, lon, tz_str, lang, user_id)
        else:
            trial_end = (now_utc + timedelta(days=7)).isoformat()
            await conn.execute("""
                INSERT INTO users (
                    user_id, name, birth_date, birth_time, city,
                    lat, lon, timezone, trial_until, premium_until,
                    is_active, created_at, trial_used, language_code
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NULL, 1, $10, 1, $11)
            """, user_id, name, b_date, b_time, city, lat, lon, tz_str, trial_end, now_utc.isoformat(), lang)

async def add_premium_days(user_id: int, days: int):
    now_utc = datetime.now(UTC)
    async with db_pool.acquire() as conn:
        current_until = await conn.fetchval("SELECT premium_until FROM users WHERE user_id = $1", user_id)
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
        await conn.execute("UPDATE users SET premium_until = $1, is_active = 1 WHERE user_id = $2", new_until, user_id)

async def get_user_access(user_id: int) -> Dict[str, Any]:
    user = await get_user(user_id)
    now_utc = datetime.now(UTC)
    if not user:
        return {"has_access": False, "status": "not_found", "days_left": 0}

    premium_until = user.get("premium_until")
    if premium_until:
        try:
            prem_dt = datetime.fromisoformat(premium_until)
            if prem_dt.tzinfo is None:
                prem_dt = prem_dt.replace(tzinfo=UTC)
            if prem_dt > now_utc:
                return {"has_access": True, "status": "premium", "days_left": max(1, (prem_dt - now_utc).days + 1)}
        except ValueError:
            pass

    trial_until = user.get("trial_until")
    if trial_until:
        try:
            trial_dt = datetime.fromisoformat(trial_until)
            if trial_dt.tzinfo is None:
                trial_dt = trial_dt.replace(tzinfo=UTC)
            if trial_dt > now_utc:
                return {"has_access": True, "status": "trial", "days_left": max(1, (trial_dt - now_utc).days + 1)}
        except ValueError:
            pass

    return {"has_access": False, "status": "expired", "days_left": 0}

async def get_active_users() -> List[Dict[str, Any]]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users WHERE is_active = 1")
        return [dict(row) for row in rows]

async def deactivate_user(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_active = 0 WHERE user_id = $1", user_id)

async def mark_forecast_sent(user_id: int, forecast_date: str):
    now = datetime.now(UTC).isoformat()
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO daily_deliveries (user_id, forecast_date, sent_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, forecast_date) DO NOTHING
        """, user_id, forecast_date, now)
        await conn.execute("UPDATE users SET last_forecast_date = $1 WHERE user_id = $2", forecast_date, user_id)

async def was_forecast_sent(user_id: int, forecast_date: str) -> bool:
    async with db_pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT 1 FROM daily_deliveries WHERE user_id = $1 AND forecast_date = $2",
            user_id, forecast_date
        )
        return val is not None

async def ai_request_allowed(user_id: int, limit: int = 15) -> bool:
    today = datetime.now(UTC).date().isoformat()
    async with db_pool.acquire() as conn:
        current = await conn.fetchval(
            "SELECT requests FROM ai_usage WHERE user_id = $1 AND usage_date = $2",
            user_id, today
        )
        current = current or 0
        if current >= limit:
            return False

        await conn.execute("""
            INSERT INTO ai_usage (user_id, usage_date, requests)
            VALUES ($1, $2, 1)
            ON CONFLICT (user_id, usage_date)
            DO UPDATE SET requests = ai_usage.requests + 1
        """, user_id, today)
        return True

# ============================================================
# ASTRO ENGINE
# ============================================================
ZODIAC = ["Aries ♈", "Taurus ♉", "Gemini ♊", "Cancer ♋", "Leo ♌", "Virgo ♍", "Libra ♎", "Scorpio ♏", "Sagittarius ♐", "Capricorn ♑", "Aquarius ♒", "Pisces ♓"]
TRACKED_PLANETS = {"Sun": swe.SUN, "Moon": swe.MOON, "Mercury": swe.MERCURY, "Venus": swe.VENUS, "Mars": swe.MARS, "Jupiter": swe.JUPITER, "Saturn": swe.SATURN, "Uranus": swe.URANUS, "Neptune": swe.NEPTUNE, "Pluto": swe.PLUTO}
ASPECTS = {0: ("Conjunction", 2.0), 60: ("Sextile", 1.5), 90: ("Square", 2.0), 120: ("Trine", 2.0), 180: ("Opposition", 2.0)}

def normalize_deg(deg: float) -> float:
    return deg % 360.0

def angular_distance(a: float, b: float) -> float:
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)

def deg_to_sign(deg: float) -> str:
    deg = normalize_deg(deg)
    return f"{ZODIAC[int(deg // 30)]} {int(deg % 30)}°"

def get_julian_day(dt_utc: datetime) -> float:
    dt_utc = dt_utc.astimezone(UTC)
    return swe.julday(dt_utc.year, dt_utc.month, dt_utc.day, dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0)

def planet_positions(dt_utc: datetime) -> Dict[str, float]:
    jd = get_julian_day(dt_utc)
    return {name: normalize_deg(swe.calc_ut(jd, pid)[0][0]) for name, pid in TRACKED_PLANETS.items()}

def get_natal_blueprint(birth_utc: datetime, lat: float, lon: float) -> Dict[str, Any]:
    jd = get_julian_day(birth_utc)
    pos = planet_positions(birth_utc)
    try:
        _, ascmc = swe.houses(jd, lat, lon, b"P")
        asc, mc = normalize_deg(ascmc[0]), normalize_deg(ascmc[1])
    except Exception:
        asc, mc = 0.0, 0.0

    return {
        "Sun": deg_to_sign(pos["Sun"]), "Moon": deg_to_sign(pos["Moon"]), "Ascendant": deg_to_sign(asc), "MC": deg_to_sign(mc),
        "Mercury": deg_to_sign(pos["Mercury"]), "Venus": deg_to_sign(pos["Venus"]), "Mars": deg_to_sign(pos["Mars"]),
        "Jupiter": deg_to_sign(pos["Jupiter"]), "Saturn": deg_to_sign(pos["Saturn"]), "Uranus": deg_to_sign(pos["Uranus"]),
        "Neptune": deg_to_sign(pos["Neptune"]), "Pluto": deg_to_sign(pos["Pluto"]),
    }

def find_transits(birth_utc: datetime, target_utc: datetime, max_results: int = 7) -> List[Dict[str, Any]]:
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
                if orb <= max_orb:
                    prev_orb = abs(angular_distance(previous[t_name], n_deg) - angle)
                    motion = "applying" if orb < prev_orb else "separating"
                    p_weight = {"Sun": 1.0, "Moon": 0.7, "Mars": 1.3, "Jupiter": 1.4, "Saturn": 1.6, "Uranus": 1.5, "Neptune": 1.5, "Pluto": 1.7}.get(t_name, 1.0)
                    importance = round(max(1.0, min(10.0, (max_orb - orb + 0.2) * 3.2 * p_weight)), 1)
                    found.append({"transit_planet": t_name, "natal_planet": n_name, "aspect": aspect_name, "orb": round(orb, 2), "motion": motion, "importance": importance})
                    break

    found.sort(key=lambda x: (-x["importance"], x["orb"]))
    return found[:max_results]

def format_transits(transits: List[Dict[str, Any]]) -> str:
    if not transits:
        return "Harmonious planetary flow; no harsh aspect within tight orb."
    return "\n".join(f"- {t['transit_planet']} {t['aspect']} natal {t['natal_planet']} (orb {t['orb']}°, {t['motion']})" for t in transits)

# ============================================================
# GEMINI ENGINE WITH FAILOVER
# ============================================================
SYSTEM_PROMPT = """
You are Aura Astro, an elite psychological astrologer and mindfulness mentor.
Approach astrology through depth psychology, cognitive reflections, and practical self-awareness.
Never use fortune-telling, fear, deterministic claims, or cliché predictions.
Always finish every single sentence you begin. Use proper punctuation.
"""

async def call_gemini_safe(prompt: str, lang: str = "en", max_tokens: int = 2000) -> str:
    lang_name = "Russian" if lang.startswith("ru") else "English"
    lang_rule = f"\nCRITICAL: Respond natively and entirely in {lang_name}. Never switch languages."
    full_prompt = prompt + lang_rule

    default_chain = [MODEL_NAME, FALLBACK_MODEL_NAME, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash"]
    models_chain = AVAILABLE_MODELS if AVAILABLE_MODELS else default_chain

    # Убираем дубликаты с сохранением порядка
    seen = set()
    final_chain = [m for m in models_chain if not (m in seen or seen.add(m))]

    for model_candidate in final_chain:
        for attempt in range(2):
            try:
                response = await asyncio.to_thread(
                    ai_client.models.generate_content,
                    model=model_candidate,
                    contents=full_prompt,
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
                print(f"[Gemini Error model={model_candidate} attempt={attempt+1}] {e}", flush=True)
                await asyncio.sleep(0.5)

    return (
        "Ваша натальная карта — это ориентир для размышлений и осознанного выбора.\n\n"
        "Обратите внимание на свои реакции сегодня, сохраняйте ясность в общении и сделайте один практический шаг к своей цели."
        if lang.startswith("ru") else
        "Your chart is best used as a reflective map rather than a fixed prediction.\n\n"
        "Focus today on noticing your reactions, choosing clear communication, and taking one practical step forward."
    )

async def generate_blueprint_text(name: str, bp: Dict[str, str], lang: str) -> str:
    prompt = f"""
Client name: {name}
Placements:
{chr(10).join(f"- {k}: {v}" for k, v in bp.items())}

Write a comprehensive, inspiring psychological interpretation of this chart (around 250 words).
Structure strictly into 4 sections:
1. Core Architecture (Sun & Moon synergy, inner motives)
2. Outer Persona (Ascendant impact on image and first impressions)
3. Drive & Strategy (Mars & Mercury: action, intellect, willpower)
4. Distinct Superpower (Name 1 unique psychological superpower and explain it in 2 complete sentences)

Finish every sentence with a period.
"""
    return await call_gemini_safe(prompt, lang, 2200)

async def generate_daily_forecast(name: str, bp: Dict[str, str], transits: List[Dict[str, Any]], target_date: date, lang: str) -> str:
    prompt = f"""
Client: {name}
Date: {target_date.isoformat()}
Chart: {chr(10).join(f"- {k}: {v}" for k, v in bp.items())}
Transits:
{format_transits(transits)}

Write a personal daily forecast (around 200 words):
- Daily Theme: 1 clear title
- Psychological Climate: 2 sentences on mood and cognitive clarity
- Relationships: 1-2 sentences on interpersonal dynamics
- Tactical Directives: exactly 2 actionable bullet points
- Reflective Inquiry: 1 mindful question to contemplate today

Complete all sentences.
"""
    return await call_gemini_safe(prompt, lang, 2000)

async def generate_chart_answer(user: Dict[str, Any], bp: Dict[str, str], transits: List[Dict[str, Any]], question: str) -> str:
    lang = user.get("language_code", "en")
    prompt = f"""
Client: {user['name']}
Question: {question}
Natal Chart: {chr(10).join(f"- {k}: {v}" for k, v in bp.items())}
Transits: {format_transits(transits)}

Answer the client's question thoughtfully using their chart as a reflective mirror (around 200 words).
Give direct insight, connect with 1-2 astrological factors, and offer 2 practical action steps.
"""
    return await call_gemini_safe(prompt, lang, 2000)

# ============================================================
# UTILITIES
# ============================================================
def parse_birth_dt(user: Dict[str, Any]) -> datetime:
    tz = ZoneInfo(user["timezone"])
    return datetime.fromisoformat(f"{user['birth_date']}T{user['birth_time']}").replace(tzinfo=tz).astimezone(UTC)

def get_status_str(access: Dict[str, Any], lang: str) -> str:
    if not access["has_access"]:
        return t("status_exp", lang)
    st = t("status_prem", lang) if access["status"] == "premium" else t("status_trial", lang)
    return f"{st} · {t('days_left', lang, days=access['days_left'])}"

async def send_long_message(chat_id: int, text: str, reply_markup=None):
    if len(text) <= 3900:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        return
    parts = text.split("\n\n")
    cur = ""
    for p in parts:
        if len(cur) + len(p) + 2 > 3800:
            await bot.send_message(chat_id=chat_id, text=cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}".strip()
    if cur:
        await bot.send_message(chat_id=chat_id, text=cur, reply_markup=reply_markup)

# ============================================================
# FSM & HANDLERS
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
    lang = message.from_user.language_code or "en"
    user = await get_user(message.from_user.id)

    if user:
        u_lang = user.get("language_code", lang)
        access = await get_user_access(message.from_user.id)
        msg = t("welcome_back", u_lang, name=html.escape(user["name"]), status=get_status_str(access, u_lang))
        await message.answer(msg, reply_markup=main_menu_keyboard(u_lang))
        return

    await state.update_data(lang_code=lang)
    await message.answer(t("start_intro", lang))
    await state.set_state(Registration.name)

@dp.message(Registration.name)
async def process_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang_code", "en")
    name = (message.text or "").strip()[:60]
    if not name:
        return
    await state.update_data(name=name)
    await message.answer(t("ask_birth_date", lang), parse_mode="Markdown")
    await state.set_state(Registration.birth_date)

@dp.message(Registration.birth_date)
async def process_date(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang_code", "en")
    val = (message.text or "").strip()
    try:
        p = date.fromisoformat(val)
        if p > date.today() or p.year < 1900:
            raise ValueError
    except ValueError:
        await message.answer(t("invalid_date", lang), parse_mode="Markdown")
        return
    await state.update_data(birth_date=val)
    await message.answer(t("ask_birth_time", lang), parse_mode="Markdown")
    await state.set_state(Registration.birth_time)

@dp.message(Registration.birth_time)
async def process_time(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang_code", "en")
    val = (message.text or "").strip()
    try:
        time.fromisoformat(val)
        if len(val) == 5:
            val += ":00"
    except ValueError:
        await message.answer(t("invalid_time", lang), parse_mode="Markdown")
        return
    await state.update_data(birth_time=val)
    await message.answer(t("ask_city", lang), parse_mode="Markdown")
    await state.set_state(Registration.birth_city)

@dp.message(Registration.birth_city)
async def process_city(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang_code", "en")
    city_q = (message.text or "").strip()[:150]
    wait_msg = await message.answer(t("calc_coords", lang))

    try:
        loc = await asyncio.to_thread(geolocator.geocode, city_q, language="en")
    except Exception:
        loc = None

    if not loc:
        await wait_msg.delete()
        await message.answer(t("city_not_found", lang))
        return

    lat, lon = float(loc.latitude), float(loc.longitude)
    tz_str = tf.timezone_at(lng=lon, lat=lat) or "UTC"

    try:
        b_local = datetime.fromisoformat(f"{data['birth_date']}T{data['birth_time']}").replace(tzinfo=ZoneInfo(tz_str))
        b_utc = b_local.astimezone(UTC)
    except Exception:
        await wait_msg.delete()
        await message.answer(t("tz_error", lang))
        return

    try:
        bp = get_natal_blueprint(b_utc, lat, lon)
        analysis = await generate_blueprint_text(data["name"], bp, lang)
    except Exception as e:
        print(f"[Blueprint Error] {e}", flush=True)
        await wait_msg.delete()
        await message.answer(t("calc_error", lang))
        return

    await save_or_update_user(message.from_user.id, data["name"], data["birth_date"], data["birth_time"], loc.address or city_q, lat, lon, tz_str, lang)
    await wait_msg.delete()

    title = "🌌 НАТАЛЬНАЯ КАРТА" if lang.startswith("ru") else "🌌 NATAL BLUEPRINT"
    card = (
        f"{title} — {data['name'].upper()}\n\n"
        f"☀️ Sun: {bp['Sun']}\n"
        f"🌙 Moon: {bp['Moon']}\n"
        f"🌅 Ascendant: {bp['Ascendant']}\n"
        f"☿ Mercury: {bp['Mercury']}\n"
        f"♀ Venus: {bp['Venus']}\n"
        f"♂ Mars: {bp['Mars']}\n"
        f"♃ Jupiter: {bp['Jupiter']}\n"
        f"♄ Saturn: {bp['Saturn']}\n\n"
        f"{analysis}\n\n"
        f"{t('trial_activated', lang)}"
    )
    await send_long_message(message.chat.id, card, main_menu_keyboard(lang))
    await state.clear()

# ============================================================
# MENU CALLBACKS
# ============================================================
@dp.callback_query(F.data == "menu")
async def cb_menu(cb: types.CallbackQuery):
    await cb.answer()
    user = await get_user(cb.from_user.id)
    if not user:
        await cb.message.answer("Please /start first.")
        return
    lang = user.get("language_code", "en")
    access = await get_user_access(cb.from_user.id)
    await cb.message.edit_text(t("welcome_back", lang, name=user["name"], status=get_status_str(access, lang)), reply_markup=main_menu_keyboard(lang))

@dp.callback_query(F.data == "chart")
async def cb_chart(cb: types.CallbackQuery):
    await cb.answer()
    user = await get_user(cb.from_user.id)
    if not user:
        return
    lang = user.get("language_code", "en")
    b_utc = parse_birth_dt(user)
    bp = get_natal_blueprint(b_utc, float(user["lat"]), float(user["lon"]))
    
    title = "🌌 ВАША НАТАЛЬНАЯ КАРТА" if lang.startswith("ru") else "🌌 YOUR NATAL CHART"
    text = (
        f"{title}\n\n"
        f"☀️ Sun: {bp['Sun']}\n🌙 Moon: {bp['Moon']}\n🌅 Ascendant: {bp['Ascendant']}\n"
        f"☿ Mercury: {bp['Mercury']}\n♀ Venus: {bp['Venus']}\n♂ Mars: {bp['Mars']}\n"
        f"♃ Jupiter: {bp['Jupiter']}\n♄ Saturn: {bp['Saturn']}\n♅ Uranus: {bp['Uranus']}\n"
        f"♆ Neptune: {bp['Neptune']}\n♇ Pluto: {bp['Pluto']}\n\n"
        f"📍 {user['city']}\n🕰 {user['timezone']}"
    )
    await send_long_message(cb.message.chat.id, text, back_menu_keyboard(lang))

@dp.callback_query(F.data.in_({"forecast", "tomorrow"}))
async def cb_forecast(cb: types.CallbackQuery):
    await cb.answer()
    user = await get_user(cb.from_user.id)
    if not user:
        return
    lang = user.get("language_code", "en")
    access = await get_user_access(cb.from_user.id)
    if not access["has_access"]:
        await cb.message.answer(t("paywall_msg", lang), reply_markup=pricing_keyboard(lang))
        return

    local_date = datetime.now(UTC).astimezone(ZoneInfo(user["timezone"])).date()
    target_date = local_date if cb.data == "forecast" else local_date + timedelta(days=1)

    b_utc = parse_birth_dt(user)
    t_utc = datetime(target_date.year, target_date.month, target_date.day, 12, 0, tzinfo=UTC)
    bp = get_natal_blueprint(b_utc, float(user["lat"]), float(user["lon"]))
    transits = find_transits(b_utc, t_utc)
    
    reading = await generate_daily_forecast(user["name"], bp, transits, target_date, lang)
    header = f"🔮 {target_date.strftime('%d.%m.%Y')}\n\n"
    await send_long_message(cb.message.chat.id, header + reading, back_menu_keyboard(lang))

@dp.callback_query(F.data == "ask_chart")
async def cb_ask(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    user = await get_user(cb.from_user.id)
    if not user:
        return
    lang = user.get("language_code", "en")
    access = await get_user_access(cb.from_user.id)
    if not access["has_access"]:
        await cb.message.answer(t("paywall_msg", lang), reply_markup=pricing_keyboard(lang))
        return
    await state.set_state(AskChartState.question)
    await cb.message.answer(t("ask_prompt", lang), parse_mode="Markdown")

@dp.message(AskChartState.question)
async def process_question(m: types.Message, state: FSMContext):
    q = (m.text or "").strip()
    user = await get_user(m.from_user.id)
    if not user or not q:
        await state.clear()
        return
    lang = user.get("language_code", "en")
    
    if not await ai_request_allowed(m.from_user.id):
        await state.clear()
        await m.answer(t("limit_reached", lang), reply_markup=main_menu_keyboard(lang))
        return

    status = await m.answer(t("analyzing", lang))
    b_utc = parse_birth_dt(user)
    bp = get_natal_blueprint(b_utc, float(user["lat"]), float(user["lon"]))
    transits = find_transits(b_utc, datetime.now(UTC))
    
    ans = await generate_chart_answer(user, bp, transits, q)
    await status.delete()
    await send_long_message(m.chat.id, f"💬 {q}\n\n{ans}", main_menu_keyboard(lang))
    await state.clear()

# ============================================================
# TOPIC HANDLERS (RELATIONSHIPS, CAREER, MONEY)
# ============================================================
@dp.callback_query(F.data.in_({"topic_relationships", "topic_career", "topic_money"}))
async def cb_topics(cb: types.CallbackQuery):
    await cb.answer()
    user = await get_user(cb.from_user.id)
    if not user:
        return
    
    lang = user.get("language_code", "en")
    access = await get_user_access(cb.from_user.id)
    if not access["has_access"]:
        await cb.message.answer(t("paywall_msg", lang), reply_markup=pricing_keyboard(lang))
        return

    topic_keys = {
        "topic_relationships": {
            "title_ru": "❤️ АСТРОЛОГИЯ ОТНОШЕНИЙ",
            "title_en": "❤️ RELATIONSHIP BLUEPRINT",
            "focus": "Venus, Moon, Mars, 7th house dynamic, emotional intimacy, attraction patterns, and conflict resolution",
        },
        "topic_career": {
            "title_ru": "💼 КАРЬЕРА И ПРИЗВАНИЕ",
            "title_en": "💼 CAREER & VOCATION",
            "focus": "Midheaven (MC), Saturn, Mars, professional ambition, leadership style, and strategic career moves",
        },
        "topic_money": {
            "title_ru": "💰 ФИНАНСОВЫЙ ПОТЕНЦИАЛ",
            "title_en": "💰 FINANCIAL BLUEPRINT",
            "focus": "Jupiter, Venus, 2nd & 8th house motifs, wealth habits, relationship with abundance and financial discipline",
        }
    }
    
    info = topic_keys[cb.data]
    status_msg = await cb.message.answer(t("analyzing", lang))
    
    b_utc = parse_birth_dt(user)
    bp = get_natal_blueprint(b_utc, float(user["lat"]), float(user["lon"]))
    transits = find_transits(b_utc, datetime.now(UTC))

    prompt = f"""
Client: {user['name']}
Analysis Domain: {info['focus']}
Chart Placements: {chr(10).join(f"- {k}: {v}" for k, v in bp.items())}
Active Transits: {format_transits(transits)}

Write an in-depth psychological and strategic astrology reading focused strictly on this domain (around 220 words).
Structure strictly into 3 sections:
1. Core Pattern (Inherent psychological tendencies and subconscious drives in this area)
2. Current Transit Momentum (How today's planetary transits activate this dynamic)
3. 2 Tactical Recommendations (Concrete, high-value behavioral adjustments)

Tone: deep, pragmatic, empowering, zero clichés. Complete every sentence.
"""
    reading = await call_gemini_safe(prompt, lang, 2000)
    await status_msg.delete()
    
    title = info["title_ru"] if lang.startswith("ru") else info["title_en"]
    full_text = f"✨ {title}\n\n{reading}"
    await send_long_message(cb.message.chat.id, full_text, back_menu_keyboard(lang))

@dp.callback_query(F.data == "subscription")
async def cb_sub(cb: types.CallbackQuery):
    await cb.answer()
    user = await get_user(cb.from_user.id)
    lang = user.get("language_code", "en") if user else "en"
    access = await get_user_access(cb.from_user.id)
    txt = f"⭐ AURA ASTRO MEMBERSHIP\n\nСтатус: {get_status_str(access, lang)}\n\nВыберите план доступа:"
    await cb.message.edit_text(txt, reply_markup=pricing_keyboard(lang))

# ============================================================
# PAYMENTS (STARS)
# ============================================================
@dp.callback_query(F.data.startswith("buy_plan_"))
async def process_buy(cb: types.CallbackQuery):
    await cb.answer()
    plan_key = cb.data.replace("buy_", "")
    plan = PRICING_PLANS.get(plan_key)
    if not plan:
        return
    prices = [LabeledPrice(label=plan["title"], amount=plan["stars"])]
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=plan["title"],
        description=plan["description"],
        payload=f"{plan_key}:{cb.from_user.id}",
        currency="XTR",
        prices=prices,
        provider_token="",
    )

@dp.pre_checkout_query()
async def process_pre_checkout(q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(q.id, ok=True)

@dp.message(F.successful_payment)
async def process_success(m: types.Message):
    plan_key = m.successful_payment.invoice_payload.split(":")[0]
    plan = PRICING_PLANS.get(plan_key, PRICING_PLANS["plan_1m"])
    await add_premium_days(m.from_user.id, plan["days"])
    user = await get_user(m.from_user.id)
    lang = user.get("language_code", "en") if user else "en"
    success_text = f"🎉 Доступ активирован на {plan['days']} дней!" if lang.startswith("ru") else f"🎉 Access extended for {plan['days']} days!"
    await m.answer(success_text, reply_markup=main_menu_keyboard(lang))

# ============================================================
# SCHEDULER & WEB SERVER
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

            f_date = local_now.date() + timedelta(days=1)
            f_key = f_date.isoformat()
            if await was_forecast_sent(user["user_id"], f_key):
                continue

            lang = user.get("language_code", "en")
            access = await get_user_access(user["user_id"])
            if not access["has_access"]:
                await bot.send_message(user["user_id"], t("paywall_msg", lang), reply_markup=pricing_keyboard(lang))
                await asyncio.sleep(0.3)
                continue

            b_utc = parse_birth_dt(user)
            t_utc = datetime(f_date.year, f_date.month, f_date.day, 12, 0, tzinfo=UTC)
            bp = get_natal_blueprint(b_utc, float(user["lat"]), float(user["lon"]))
            transits = find_transits(b_utc, t_utc)
            reading = await generate_daily_forecast(user["name"], bp, transits, f_date, lang)

            title = "✨ ПРОГНОЗ НА ЗАВТРА" if lang.startswith("ru") else "✨ TOMORROW'S ALIGNMENT"
            msg = f"{title} ({f_date.strftime('%d.%m.%Y')})\n\n{reading}\n\n⏳ {get_status_str(access, lang)}"
            await send_long_message(user["user_id"], msg, main_menu_keyboard(lang))
            await mark_forecast_sent(user["user_id"], f_key)
            await asyncio.sleep(1.0)
        except TelegramForbiddenError:
            await deactivate_user(user["user_id"])
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            print(f"[Scheduler User {user.get('user_id')}] {e}", flush=True)

async def health_check(request):
    return web.Response(text="Aura Engine v2.2 Online", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main():
    await start_web_server()
    await init_db()
    init_available_models()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(send_daily_cycle, "cron", minute=0, id="daily_cycle", replace_existing=True)
    scheduler.start()
    await bot.delete_webhook(drop_pending_updates=True)
    print("🚀 Aura Astro v2.2 is running.", flush=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot safely shutdown.")
