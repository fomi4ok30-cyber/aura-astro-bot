import os
import sys
import asyncio
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Any

# Веб-сервер для Uptime-пингов на Render
from aiohttp import web

# Telegram фреймворк
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
    PreCheckoutQuery
)

# Планировщик фоновых задач
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Астрономия, базы данных и LLM
import aiosqlite
import swisseph as swe
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
from google import genai
from google.genai import types as genai_types

# =====================================================================
# КОНФИГУРАЦИЯ И СЕКРЕТНЫЕ КЛЮЧИ
# =====================================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", 8080))
DB_PATH = os.getenv("DB_PATH", "aura_astro.db")
MODEL_NAME = "gemini-3.6-flash"

if not BOT_TOKEN or not GEMINI_KEY:
    print("[CRITICAL] Please provide TELEGRAM_BOT_TOKEN and GEMINI_API_KEY in Environment Variables!")
    sys.exit(1)

# Клиенты
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
geolocator = Nominatim(user_agent="aura_astro_engine_prod", timeout=7)
tf = TimezoneFinder()
ai_client = genai.Client(api_key=GEMINI_KEY)

# =====================================================================
# ТАРИФНАЯ СЕТКА (TELEGRAM STARS)
# 1 USD ~ 50 Stars
# =====================================================================
PRICING_PLANS = {
    "plan_1m": {
        "title": "🌟 1 Month Access",
        "description": "30 days of full daily transits & psychological guidance.",
        "stars": 500,       # $10
        "days": 30
    },
    "plan_6m": {
        "title": "⚡ 6 Months Access (Save $20)",
        "description": "180 days of transit forecasts & cosmic tracking.",
        "stars": 2000,      # $40 ($6.6/mo)
        "days": 180
    },
    "plan_1y": {
        "title": "👑 1 Year Access (Best Value - Save $50)",
        "description": "365 days of full astrological coaching & updates.",
        "stars": 3500,      # $70 ($5.8/mo)
        "days": 365
    }
}

def get_pricing_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 1 Month — 500 Stars ($10)", callback_data="buy_plan_1m")],
        [InlineKeyboardButton(text="⚡ 6 Months — 2000 Stars ($40)", callback_data="buy_plan_6m")],
        [InlineKeyboardButton(text="👑 1 Year — 3500 Stars ($70)", callback_data="buy_plan_1y")]
    ])

# =====================================================================
# 1. СЛОЙ БАЗЫ ДАННЫХ
# =====================================================================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("""
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
        """)
        await db.commit()

async def save_user(user_id: int, name: str, b_date: str, b_time: str, city: str, lat: float, lon: float, tz_str: str):
    now_utc = datetime.now(timezone.utc)
    trial_end = (now_utc + timedelta(days=7)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (
                user_id, name, birth_date, birth_time, city, lat, lon, 
                timezone, trial_until, premium_until, is_active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?)
        """, (user_id, name, b_date, b_time, city, lat, lon, tz_str, trial_end, now_utc.isoformat()))
        await db.commit()

async def add_premium_days(user_id: int, days: int):
    now_utc = datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            current_until = row["premium_until"] if row and row["premium_until"] else None

        if current_until:
            current_dt = datetime.fromisoformat(current_until)
            base_dt = max(now_utc, current_dt)
        else:
            base_dt = now_utc

        new_until = (base_dt + timedelta(days=days)).isoformat()
        await db.execute("UPDATE users SET premium_until = ?, is_active = 1 WHERE user_id = ?", (new_until, user_id))
        await db.commit()

async def get_user_access(user_id: int) -> Dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT trial_until, premium_until FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return {"has_access": False, "status": "not_found", "days_left": 0}

            if row["premium_until"]:
                prem_dt = datetime.fromisoformat(row["premium_until"])
                if prem_dt > now_utc:
                    days_left = (prem_dt - now_utc).days + 1
                    return {"has_access": True, "status": "premium", "days_left": days_left}

            trial_dt = datetime.fromisoformat(row["trial_until"])
            if trial_dt > now_utc:
                days_left = (trial_dt - now_utc).days + 1
                return {"has_access": True, "status": "trial", "days_left": days_left}

            return {"has_access": False, "status": "expired", "days_left": 0}

async def get_active_users() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE is_active = 1") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def deactivate_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
        await db.commit()

# =====================================================================
# 2. АСТРОНОМИЧЕСКИЙ ДВИЖОК (SWISS EPHEMERIS)
# =====================================================================
ZODIAC = ["Aries ♈", "Taurus ♉", "Gemini ♊", "Cancer ♋", "Leo ♌", "Virgo ♍", 
          "Libra ♎", "Scorpio ♏", "Sagittarius ♐", "Capricorn ♑", "Aquarius ♒", "Pisces ♓"]

ASPECTS = {
    0: ("Conjunction", 2.0),
    60: ("Sextile", 1.5),
    90: ("Square", 2.0),
    120: ("Trine", 2.0),
    180: ("Opposition", 2.0)
}

TRACKED_PLANETS = {
    "Sun": swe.SUN, "Moon": swe.MOON, "Mercury": swe.MERCURY, "Venus": swe.VENUS,
    "Mars": swe.MARS, "Jupiter": swe.JUPITER, "Saturn": swe.SATURN, "Uranus": swe.URANUS,
    "Neptune": swe.NEPTUNE, "Pluto": swe.PLUTO
}

def deg_to_sign(deg: float) -> str:
    return f"{ZODIAC[int(deg // 30) % 12]} ({int(deg % 30)}°)"

def get_julian_day(dt_utc: datetime) -> float:
    return swe.julday(
        dt_utc.year, dt_utc.month, dt_utc.day, 
        dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0
    )

def get_natal_blueprint(birth_utc: datetime, lat: float, lon: float) -> Dict[str, str]:
    jd = get_julian_day(birth_utc)
    sun = swe.calc_ut(jd, swe.SUN)[0][0]
    moon = swe.calc_ut(jd, swe.MOON)[0][0]
    _, ascmc = swe.houses(jd, lat, lon, b'P')
    return {
        "sun": deg_to_sign(sun),
        "moon": deg_to_sign(moon),
        "ascendant": deg_to_sign(ascmc[0])
    }

def get_active_transits(birth_utc: datetime, target_utc: datetime) -> List[str]:
    jd_n = get_julian_day(birth_utc)
    jd_t = get_julian_day(target_utc)
    
    natal_pos = {name: swe.calc_ut(jd_n, pid)[0][0] for name, pid in TRACKED_PLANETS.items()}
    transit_pos = {name: swe.calc_ut(jd_t, pid)[0][0] for name, pid in TRACKED_PLANETS.items()}
    
    found = []
    for t_name, t_deg in transit_pos.items():
        for n_name, n_deg in natal_pos.items():
            diff = abs(t_deg - n_deg) % 360
            diff = min(diff, 360 - diff)
            for angle, (asp_name, max_orb) in ASPECTS.items():
                cur_orb = abs(diff - angle)
                if cur_orb <= max_orb:
                    found.append({"desc": f"Transit {t_name} {asp_name} Natal {n_name}", "orb": cur_orb})
                    break

    found.sort(key=lambda x: x["orb"])
    return [item["desc"] for item in found[:3]]

# =====================================================================
# 3. ГЕНЕРАТИВНЫЙ МОДУЛЬ (GEMINI 3.6 FLASH)
# =====================================================================
SYSTEM_PROMPT = """
You are a distinguished psychological astrologer and mindfulness mentor writing for an educated English-speaking audience.
Tone: Articulate, grounding, modern, empathetic. Strictly NO fortune-telling, clichés, or fatalism.
Focus on cognitive clarity, emotional dynamics, interpersonal relations, and concrete actions.
Always finish your sentences and deliver complete, coherent paragraphs. Output exclusively in clean Markdown.
"""

async def call_gemini_safe(prompt: str, max_tokens: int = 1500) -> str:
    """Генерация с защитой от сбоев и достаточным лимитом токенов."""
    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=MODEL_NAME,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.6,
                    max_output_tokens=max_tokens
                )
            )
            return response.text
        except Exception as e:
            print(f"[Gemini Retry {attempt+1}/3] Error: {e}")
            await asyncio.sleep(2.0 * (attempt + 1))
            
    return (
        "**Daily Planetary Focus**\n\n"
        "Today highlights introspection, emotional resilience, and thoughtful communication. "
        "Pause and evaluate before making critical decisions.\n\n"
        "- *Action:* Focus on high-priority objectives and clear boundaries.\n"
        "- *Reflection:* What matters most to your long-term growth right now?"
    )

async def generate_blueprint_text(name: str, bp: dict) -> str:
    prompt = f"""
Client: {name}
Placements:
- Sun: {bp['sun']}
- Moon: {bp['moon']}
- Ascendant: {bp['ascendant']}

Write a comprehensive, psychologically deep natal profile (around 250-320 words):
- **Core Architecture**: How the conscious will and vision of their Sun interact with the emotional instinct of their Moon.
- **The Outer Persona**: How their Ascendant shapes their presence, intuition, and first impression.
- **Distinct Superpower**: 1 signature psychological strength and strategic advantage.
Ensure all sections are completely finished without ending mid-sentence.
"""
    return await call_gemini_safe(prompt, max_tokens=1500)

async def generate_transit_text(name: str, transits: list) -> str:
    t_str = "; ".join(transits) if transits else "Harmonious planetary flow."
    prompt = f"""
Client: {name}
Active Transits for Tomorrow: {t_str}

Provide an empowering daily forecast (around 180-220 words):
- **Daily Theme**: 1-sentence evocative headline.
- **Psychological Climate**: Active emotional dynamics and mental clarity.
- **Tactical Directives**: Exactly 2 actionable steps for productivity and mindful communication.
- **Reflective Inquiry**: 1 thoughtful mindfulness prompt.
Ensure all sections are completely finished without ending mid-sentence.
"""
    return await call_gemini_safe(prompt, max_tokens=1500)

# =====================================================================
# 4. ШЕДУЛЕР РАССЫЛКИ (20:00 ПО МЕСТНОМУ ВРЕМЕНИ)
# =====================================================================
async def send_daily_cycle():
    users = await get_active_users()
    now_utc = datetime.now(ZoneInfo("UTC"))
    
    for user in users:
        try:
            u_tz = ZoneInfo(user["timezone"])
            u_local = now_utc.astimezone(u_tz)
            
            if u_local.hour != 20:
                continue

            access = await get_user_access(user["user_id"])
            if not access["has_access"]:
                paywall_msg = (
                    f"🔒 *Your Free Access has concluded, {user['name']}.*\n\n"
                    "Active transits continue shaping your chart tomorrow. Choose a plan below "
                    "to continue receiving daily alignments, psychological themes, and practical action plans."
                )
                await bot.send_message(
                    chat_id=user["user_id"],
                    text=paywall_msg,
                    parse_mode="Markdown",
                    reply_markup=get_pricing_keyboard()
                )
                await asyncio.sleep(2.0)
                continue

            target_date = u_local.date() + timedelta(days=1)
            target_utc = datetime(target_date.year, target_date.month, target_date.day, 12, 0, tzinfo=ZoneInfo("UTC"))
            
            b_local = datetime.fromisoformat(f"{user['birth_date']}T{user['birth_time']}").replace(tzinfo=u_tz)
            b_utc = b_local.astimezone(ZoneInfo("UTC"))

            transits = get_active_transits(b_utc, target_utc)
            reading = await generate_transit_text(user["name"], transits)
            
            status_tag = "Trial" if access["status"] == "trial" else "Premium"
            footer = f"\n\n⏳ *{status_tag}: {access['days_left']} {'day' if access['days_left'] == 1 else 'days'} remaining.*"
            msg = f"✨ *Tomorrow's Daily Alignment* ({target_date.strftime('%B %d')})\n\n{reading}{footer}"

            await bot.send_message(chat_id=user["user_id"], text=msg, parse_mode="Markdown")
            await asyncio.sleep(2.5)

        except TelegramForbiddenError:
            await deactivate_user(user["user_id"])
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            print(f"[Scheduler Error] User {user.get('user_id')}: {e}")
            await asyncio.sleep(1.0)

# =====================================================================
# 5. ДИАЛОГ РЕГИСТРАЦИИ (FSM)
# =====================================================================
class Registration(StatesGroup):
    name = State()
    birth_date = State()
    birth_time = State()
    birth_city = State()

@dp.message(CommandStart())
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "✨ *Welcome to Aura Astro.*\n\n"
        "We align real-time Swiss Ephemeris data with depth psychology "
        "to deliver personal daily forecasts.\n\n"
        "To begin, what is your **preferred name**?",
        parse_mode="Markdown"
    )
    await state.set_state(Registration.name)

@dp.message(Registration.name)
async def process_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await m.answer(
        "What is your **date of birth**?\n"
        "Use format: `YYYY-MM-DD` (e.g., `1994-08-23`):",
        parse_mode="Markdown"
    )
    await state.set_state(Registration.birth_date)

@dp.message(Registration.birth_date)
async def process_date(m: types.Message, state: FSMContext):
    try:
        date.fromisoformat(m.text.strip())
    except ValueError:
        await m.answer("⚠️ Invalid date. Please use `YYYY-MM-DD` format (e.g. `1995-11-04`):", parse_mode="Markdown")
        return
    await state.update_data(birth_date=m.text.strip())
    await m.answer(
        "What **time** were you born?\n"
        "Use 24h format `HH:MM` (e.g. `14:30`).\n"
        "_If unknown, simply type `12:00`._",
        parse_mode="Markdown"
    )
    await state.set_state(Registration.birth_time)

@dp.message(Registration.birth_time)
async def process_time(m: types.Message, state: FSMContext):
    try:
        time.fromisoformat(m.text.strip())
    except ValueError:
        await m.answer("⚠️ Invalid time. Please use `HH:MM` format (e.g. `08:45` or `12:00`):", parse_mode="Markdown")
        return
    await state.update_data(birth_time=m.text.strip())
    await m.answer(
        "Where were you born? Enter **city and country**\n"
        "(e.g., `London, UK` or `Chicago, USA`):",
        parse_mode="Markdown"
    )
    await state.set_state(Registration.birth_city)

@dp.message(Registration.birth_city)
async def process_city(m: types.Message, state: FSMContext):
    city_query = m.text.strip()
    status_msg = await m.answer("🔭 *Calculating chart coordinates...*", parse_mode="Markdown")

    location = await asyncio.to_thread(geolocator.geocode, city_query, language="en")
    if not location:
        await status_msg.delete()
        await m.answer("⚠️ Location not found. Please try entering 'City, Country' (e.g., 'Boston, USA'):")
        return

    lat, lon = location.latitude, location.longitude
    tz_str = tf.timezone_at(lng=lon, lat=lat) or "UTC"
    data = await state.get_data()

    b_local = datetime.fromisoformat(f"{data['birth_date']}T{data['birth_time']}").replace(tzinfo=ZoneInfo(tz_str))
    b_utc = b_local.astimezone(ZoneInfo("UTC"))

    bp = get_natal_blueprint(b_utc, lat, lon)
    analysis = await generate_blueprint_text(data["name"], bp)

    await save_user(m.from_user.id, data["name"], data["birth_date"], data["birth_time"], location.address, lat, lon, tz_str)
    await status_msg.delete()

    card = (
        f"🌌 *NATAL BLUEPRINT: {data['name'].upper()}*\n\n"
        f"☀️ *Sun:* {bp['sun']}\n"
        f"🌙 *Moon:* {bp['moon']}\n"
        f"🌅 *Ascendant:* {bp['ascendant']}\n\n"
        f"{analysis}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 *7-Day Full Access Activated.*\n"
        f"Your daily transit forecasts will arrive every evening at **20:00** local time.\n"
        f"Your first forecast arrives tonight!"
    )
    await m.answer(card, parse_mode="Markdown")
    await state.clear()

# =====================================================================
# 6. БИЛЛИНГ И ОПЛАТА ЧЕРЕЗ TELEGRAM STARS
# =====================================================================
@dp.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message):
    access = await get_user_access(message.from_user.id)
    status_str = f"Status: *{access['status'].capitalize()}* ({access['days_left']} days left)" if access["has_access"] else "Status: *Expired*"
    
    await message.answer(
        f"💫 *Aura Astro Membership*\n\n"
        f"{status_str}\n\n"
        "Choose your access plan to unlock daily planetary transits and mindfulness directives:",
        parse_mode="Markdown",
        reply_markup=get_pricing_keyboard()
    )

@dp.callback_query(F.data.startswith("buy_plan_"))
async def process_buy_plan(callback: types.CallbackQuery):
    await callback.answer()
    plan_key = callback.data.replace("buy_", "")
    plan = PRICING_PLANS.get(plan_key)
    
    if not plan:
        await callback.message.answer("Invalid plan selected.")
        return

    prices = [LabeledPrice(label=plan["title"], amount=plan["stars"])]
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=plan["title"],
        description=plan["description"],
        payload=f"{plan_key}:{callback.from_user.id}",
        currency="XTR",
        prices=prices,
        provider_token=""
    )

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

@dp.message(F.successful_payment)
async def process_payment_success(message: types.Message):
    payload = message.successful_payment.invoice_payload
    plan_key = payload.split(":")[0]
    plan = PRICING_PLANS.get(plan_key, PRICING_PLANS["plan_1m"])
    
    await add_premium_days(message.from_user.id, plan["days"])
    
    await message.answer(
        f"🎉 *Payment Successful!*\n\n"
        f"You have activated: **{plan['title']}**\n"
        f"Added **{plan['days']} days** of unlimited premium access.\n\n"
        f"Your daily forecasts will continue arriving every evening at 20:00 local time. ✨",
        parse_mode="Markdown"
    )

# =====================================================================
# 7. ВЕБ-СЕРВЕР ДЛЯ UPTIME 24/7 (RENDER HEALTH CHECK)
# =====================================================================
async def health_check(request):
    return web.Response(text="Aura Engine Online 24/7", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌍 Web health-check server running on port {PORT}")

# =====================================================================
# 8. ТОЧКА ВХОДА (MAIN)
# =====================================================================
async def main():
    await init_db()
    await start_web_server()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_daily_cycle, "cron", minute=0)
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    print("🚀 Aura Astro Engine is fully operational! Starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot safely shutdown.")
