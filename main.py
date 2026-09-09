import os
import sys
import asyncio
import html
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional, Tuple

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
# AURA ASTRO v6.0
# ============================================================
# Улучшения:
# - дома планет
# - натальные аспекты
# - ретроградность
# - более информативные описания
# - структурированные данные для Gemini
# - локализованные транзиты
# - сохранены PostgreSQL, Stars, trial и рассылка
# ============================================================


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

DEFAULT_GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash",
).strip()

MODELS_CHAIN = [
    DEFAULT_GEMINI_MODEL,
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.1-pro-preview",
]


# ============================================================
# GLOBALS
# ============================================================

bot = Bot(token=BOT_TOKEN)

dp = Dispatcher(
    storage=MemoryStorage()
)

geolocator = Nominatim(
    user_agent="aura_astro_engine_prod_v6",
    timeout=7,
)

tf = TimezoneFinder()

ai_client = genai.Client(
    api_key=GEMINI_KEY
)

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
            "Ваш персональный астрологический проводник "
            "объединяет точные расчёты Swiss Ephemeris "
            "и психологическую интерпретацию.\n\n"
            "Давайте построим вашу натальную карту.\n\n"
            "Как к вам обращаться?"
        ),
        "ask_birth_date": (
            "Укажите дату рождения в формате `ГГГГ-ММ-ДД`.\n\n"
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
            "Например: `Москва, Россия`."
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
            "Ежедневный прогноз будет приходить в 20:00 "
            "по местному времени."
        ),
        "btn_chart": "🌌 Моя карта",
        "btn_forecast": "🔮 Прогноз на сегодня",
        "btn_tomorrow": "🌙 Завтра",
        "btn_ask": "💬 Спросить карту",
        "btn_rel": "❤️ Отношения",
        "btn_career": "💼 Карьера",
        "btn_money": "💰 Финансы",
        "btn_sub": "⭐ Подписка",
        "btn_language": "🌐 Язык",
        "language_title": "🌐 ВЫБОР ЯЗЫКА",
        "language_choose": "Выберите язык интерфейса:",
        "language_saved": "✅ Язык изменён на русский.",
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
            "Задайте вопрос о себе, работе, отношениях "
            "или важном выборе.\n\n"
            "Например:\n"
            "«Почему мне сложно доверять людям?»"
        ),
        "analyzing": "🧠 Анализирую вашу карту...",
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
        "house": "дом",
        "retrograde": "ретроградная",
        "direct": "прямая",
        "aspects_title": "🔺 КЛЮЧЕВЫЕ АСПЕКТЫ",
        "no_aspects": "Тесных основных аспектов не обнаружено.",
        "natal_data_title": "📊 ОСНОВНЫЕ ПОКАЗАТЕЛИ",
        "degree_hint": "градус уточняет положение планеты внутри знака",
        "house_hint": "дом показывает сферу жизни, где проявляется энергия",
        "aspect_hint": "аспект показывает взаимодействие двух планет",
        "retro_hint": "ретроградность в астрологической традиции связывается с более внутренней переработкой темы",
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
            "Swiss Ephemeris calculations with psychological interpretation.\n\n"
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
            "Please use `YYYY-MM-DD`."
        ),
        "ask_birth_time": (
            "What time were you born?\n\n"
            "Use 24-hour format `HH:MM`.\n"
            "Example: `14:30`.\n\n"
            "If unknown, type `12:00`."
        ),
        "invalid_time": (
            "⚠️ Invalid time.\n\n"
            "Please use `HH:MM`."
        ),
        "ask_city": (
            "Where were you born?\n\n"
            "Enter city and country.\n"
            "Example: `London, UK`."
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
        "btn_language": "🌐 Language",
        "language_title": "🌐 LANGUAGE",
        "language_choose": "Choose your interface language:",
        "language_saved": "✅ Language changed to English.",
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
            "Choose a subscription to continue receiving forecasts "
            "and asking your chart."
        ),
        "ask_prompt": (
            "💬 ASK MY CHART\n\n"
            "Ask a question about yourself, relationships, work, "
            "or an important decision.\n\n"
            "Example:\n"
            "\"Why do I find it hard to trust people?\""
        ),
        "analyzing": "🧠 Analyzing your chart...",
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
        "house": "house",
        "retrograde": "retrograde",
        "direct": "direct",
        "aspects_title": "🔺 KEY NATAL ASPECTS",
        "no_aspects": "No tight major aspects detected.",
        "natal_data_title": "📊 KEY INDICATORS",
        "degree_hint": "the degree refines the planet's position within its sign",
        "house_hint": "the house shows the life area where the energy is expressed",
        "aspect_hint": "an aspect describes the interaction between two planets",
        "retro_hint": "in traditional astrology, retrograde motion is associated with more internal processing of a theme",
    },
}


SUPPORTED_LANGS = ("ru", "en", "de", "es", "fr", "it", "pt", "tr", "uk")

# Additional interface languages. Russian and English remain the original full dictionaries.\n# The compact dictionaries below cover every UI key and are merged into TEXTS at startup.
EXTRA_TEXTS = {
    "de": {
        "welcome_back":"✨ Willkommen zurück, {name}!\\n\\nStatus: {status}\\n\\nWas möchtest du erkunden?","start_intro":"✨ Willkommen bei Aura Astro.\\n\\nDein persönlicher Astrologie-Begleiter verbindet präzise Swiss-Ephemeris-Berechnungen mit psychologischer Interpretation.\\n\\nLass uns dein Geburtshoroskop erstellen.\\n\\nWie dürfen wir dich nennen?","ask_birth_date":"Gib dein Geburtsdatum im Format `JJJJ-MM-TT` ein.\\n\\nBeispiel: `1994-08-23`","invalid_date":"⚠️ Ungültiges Datum.\\n\\nBitte `JJJJ-MM-TT` verwenden.","ask_birth_time":"Wann wurdest du geboren?\\n\\nVerwende das 24-Stunden-Format `HH:MM`.\\nBeispiel: `14:30`.\\n\\nWenn unbekannt, schreibe `12:00`.","invalid_time":"⚠️ Ungültige Uhrzeit.\\n\\nBitte `HH:MM` verwenden.","ask_city":"Wo wurdest du geboren?\\n\\nGib Stadt und Land an.\\nBeispiel: `Berlin, Deutschland`.","calc_coords":"🔭 Koordinaten und Geburtshoroskop werden berechnet...","city_not_found":"⚠️ Ort nicht gefunden.\\n\\nVersuche `Stadt, Land`.","tz_error":"⚠️ Historische Zeitzone konnte nicht bestimmt werden.\\n\\nVersuche eine größere Stadt in der Nähe.","calc_error":"⚠️ Bei der Berechnung ist ein Fehler aufgetreten.\\n\\nBitte versuche es erneut.","trial_activated":"━━━━━━━━━━━━━━━━━━━━\\n🎁 Vollzugang für 7 Tage aktiviert.\\n\\nDeine tägliche Prognose kommt um 20:00 Uhr Ortszeit.","btn_chart":"🌌 Mein Horoskop","btn_forecast":"🔮 Prognose heute","btn_tomorrow":"🌙 Morgen","btn_ask":"💬 Mein Horoskop fragen","btn_rel":"❤️ Beziehungen","btn_career":"💼 Karriere","btn_money":"💰 Finanzen","btn_sub":"⭐ Abo","btn_language":"🌐 Sprache","language_title":"🌐 SPRACHE","language_choose":"Wähle die Sprache der Oberfläche:","language_saved":"✅ Sprache auf Deutsch geändert.","btn_menu":"⬅️ Hauptmenü","status_trial":"Probezeit","status_prem":"Premium","status_exp":"Abgelaufen","days_left":"noch {days} Tag(e)","limit_reached":"Das tägliche KI-Limit wurde erreicht.\\n\\nKomm morgen wieder.","paywall_msg":"🔒 Der kostenlose Zugang ist beendet.\\n\\nWähle ein Abo, um Prognosen und Fragen zu deinem Horoskop fortzusetzen.","ask_prompt":"💬 MEIN HOROSKOP FRAGEN\\n\\nStelle eine Frage über dich, Beziehungen, Arbeit oder eine wichtige Entscheidung.","analyzing":"🧠 Dein Horoskop wird analysiert...","subscription_title":"⭐ AURA ASTRO — ABO","subscription_choose":"Wähle deinen Zugang:","plan_1m_btn":"⭐ 1 Monat — 199 Stars","plan_3m_btn":"✨ 3 Monate — 450 Stars","plan_6m_btn":"⚡ 6 Monate — 800 Stars","plan_1y_btn":"👑 1 Jahr — 1200 Stars","payment_success":"🎉 Zugang für {days} Tage aktiviert!","payment_already_processed":"✅ Diese Zahlung wurde bereits verarbeitet.","chart_title":"🌌 DEIN GEBURTSHOROSKOP","chart_title_short":"🌌 GEBURTSHOROSKOP","sun":"Sonne","moon":"Mond","ascendant":"Aszendent","mc":"MC","mercury":"Merkur","venus":"Venus","mars":"Mars","jupiter":"Jupiter","saturn":"Saturn","uranus":"Uranus","neptune":"Neptun","pluto":"Pluto","meaning_sun":"Persönlichkeit, Wille und Lebensenergie","meaning_moon":"Emotionen, innere Bedürfnisse und Sicherheitsgefühl","meaning_ascendant":"Außenwirkung, erster Eindruck und Ausdrucksweise","meaning_mc":"Karriere, Status und berufliche Richtung","meaning_mercury":"Denken, Kommunikation und Informationsverarbeitung","meaning_venus":"Liebe, Anziehung, Werte und persönlicher Geschmack","meaning_mars":"Handlung, Energie, Antrieb und Durchsetzungsvermögen","meaning_jupiter":"Wachstum, Überzeugungen, Chancen und Horizonterweiterung","meaning_saturn":"Disziplin, Grenzen, Verantwortung und Reife","meaning_uranus":"Freiheit, Veränderung und Unabhängigkeit","meaning_neptune":"Intuition, Ideale, Fantasie und Sensibilität","meaning_pluto":"Tiefe Veränderung, Kraft und innere Transformation","location":"Geburtsort","tomorrow_title":"✨ PROGNOSE FÜR MORGEN","forecast_title":"🔮 PROGNOSE","trial":"Probezeit","premium":"Premium","expired":"Zugang abgelaufen","house":"Haus","retrograde":"rückläufig","direct":"direkt","aspects_title":"🔺 WICHTIGE NATALASPEKTE","no_aspects":"Keine engen Hauptaspekte gefunden.","natal_data_title":"📊 WICHTIGE INDIKATOREN","degree_hint":"Der Grad verfeinert die Position des Planeten im Zeichen","house_hint":"Das Haus zeigt den Lebensbereich, in dem sich die Energie ausdrückt","aspect_hint":"Ein Aspekt beschreibt die Wechselwirkung zwischen zwei Planeten","retro_hint":"In der traditionellen Astrologie wird Rückläufigkeit mit stärker innerer Verarbeitung eines Themas verbunden."},
    "es": {}, "fr": {}, "it": {}, "pt": {}, "tr": {}, "uk": {}
}

# Build the remaining languages from concise translations of the same complete key set.
_BASE_TRANSLATIONS = {
"es": {"language_saved":"✅ Idioma cambiado a español.","language_title":"🌐 IDIOMA","language_choose":"Elige el idioma de la interfaz:","btn_language":"🌐 Idioma","btn_menu":"⬅️ Menú principal","btn_chart":"🌌 Mi carta","btn_forecast":"🔮 Pronóstico de hoy","btn_tomorrow":"🌙 Mañana","btn_ask":"💬 Preguntar a mi carta","btn_rel":"❤️ Relaciones","btn_career":"💼 Carrera","btn_money":"💰 Dinero","btn_sub":"⭐ Suscripción","status_trial":"Prueba","status_prem":"Premium","status_exp":"Caducado","chart_title":"🌌 TU CARTA NATAL","chart_title_short":"🌌 CARTA NATAL","sun":"Sol","moon":"Luna","ascendant":"Ascendente","mc":"MC","mercury":"Mercurio","venus":"Venus","mars":"Marte","jupiter":"Júpiter","saturn":"Saturno","uranus":"Urano","neptune":"Neptuno","pluto":"Plutón","location":"Lugar de nacimiento","house":"casa","retrograde":"retrógrado","direct":"directo","forecast_title":"🔮 PRONÓSTICO","tomorrow_title":"✨ PRONÓSTICO DE MAÑANA","trial":"Prueba","premium":"Premium","expired":"Acceso caducado","aspects_title":"🔺 ASPECTOS NATALES CLAVE","no_aspects":"No se detectaron aspectos mayores estrechos.","natal_data_title":"📊 INDICADORES CLAVE","analyzing":"🧠 Analizando tu carta..."},
"fr": {"language_saved":"✅ Langue changée en français.","language_title":"🌐 LANGUE","language_choose":"Choisissez la langue de l’interface :","btn_language":"🌐 Langue","btn_menu":"⬅️ Menu principal","btn_chart":"🌌 Ma carte","btn_forecast":"🔮 Prévision du jour","btn_tomorrow":"🌙 Demain","btn_ask":"💬 Interroger ma carte","btn_rel":"❤️ Relations","btn_career":"💼 Carrière","btn_money":"💰 Finances","btn_sub":"⭐ Abonnement","status_trial":"Essai","status_prem":"Premium","status_exp":"Expiré","chart_title":"🌌 VOTRE CARTE NATALE","chart_title_short":"🌌 CARTE NATALE","sun":"Soleil","moon":"Lune","ascendant":"Ascendant","mc":"MC","mercury":"Mercure","venus":"Vénus","mars":"Mars","jupiter":"Jupiter","saturn":"Saturne","uranus":"Uranus","neptune":"Neptune","pluto":"Pluton","location":"Lieu de naissance","house":"maison","retrograde":"rétrograde","direct":"direct","forecast_title":"🔮 PRÉVISION","tomorrow_title":"✨ PRÉVISION DE DEMAIN","trial":"Essai","premium":"Premium","expired":"Accès expiré","aspects_title":"🔺 ASPECTS NATAUX CLÉS","no_aspects":"Aucun aspect majeur serré détecté.","natal_data_title":"📊 INDICATEURS CLÉS","analyzing":"🧠 Analyse de votre carte..."},
"it": {"language_saved":"✅ Lingua cambiata in italiano.","language_title":"🌐 LINGUA","language_choose":"Scegli la lingua dell’interfaccia:","btn_language":"🌐 Lingua","btn_menu":"⬅️ Menu principale","btn_chart":"🌌 La mia carta","btn_forecast":"🔮 Previsione di oggi","btn_tomorrow":"🌙 Domani","btn_ask":"💬 Chiedi alla mia carta","btn_rel":"❤️ Relazioni","btn_career":"💼 Carriera","btn_money":"💰 Finanze","btn_sub":"⭐ Abbonamento","status_trial":"Prova","status_prem":"Premium","status_exp":"Scaduto","chart_title":"🌌 LA TUA CARTA NATALE","chart_title_short":"🌌 CARTA NATALE","sun":"Sole","moon":"Luna","ascendant":"Ascendente","mc":"MC","mercury":"Mercurio","venus":"Venere","mars":"Marte","jupiter":"Giove","saturn":"Saturno","uranus":"Urano","neptune":"Nettuno","pluto":"Plutone","location":"Luogo di nascita","house":"casa","retrograde":"retrogrado","direct":"diretto","forecast_title":"🔮 PREVISIONE","tomorrow_title":"✨ PREVISIONE DI DOMANI","trial":"Prova","premium":"Premium","expired":"Accesso scaduto","aspects_title":"🔺 ASPETTI NATALI CHIAVE","no_aspects":"Nessun aspetto maggiore stretto rilevato.","natal_data_title":"📊 INDICATORI CHIAVE","analyzing":"🧠 Analizzo la tua carta..."},
"pt": {"language_saved":"✅ Idioma alterado para português.","language_title":"🌐 IDIOMA","language_choose":"Escolha o idioma da interface:","btn_language":"🌐 Idioma","btn_menu":"⬅️ Menu principal","btn_chart":"🌌 Meu mapa","btn_forecast":"🔮 Previsão de hoje","btn_tomorrow":"🌙 Amanhã","btn_ask":"💬 Perguntar ao meu mapa","btn_rel":"❤️ Relacionamentos","btn_career":"💼 Carreira","btn_money":"💰 Finanças","btn_sub":"⭐ Assinatura","status_trial":"Teste","status_prem":"Premium","status_exp":"Expirado","chart_title":"🌌 SEU MAPA NATAL","chart_title_short":"🌌 MAPA NATAL","sun":"Sol","moon":"Lua","ascendant":"Ascendente","mc":"MC","mercury":"Mercúrio","venus":"Vênus","mars":"Marte","jupiter":"Júpiter","saturn":"Saturno","uranus":"Urano","neptune":"Netuno","pluto":"Plutão","location":"Local de nascimento","house":"casa","retrograde":"retrógrado","direct":"direto","forecast_title":"🔮 PREVISÃO","tomorrow_title":"✨ PREVISÃO DE AMANHÃ","trial":"Teste","premium":"Premium","expired":"Acesso expirado","aspects_title":"🔺 ASPECTOS NATAIS PRINCIPAIS","no_aspects":"Nenhum aspecto maior próximo foi detectado.","natal_data_title":"📊 INDICADORES PRINCIPAIS","analyzing":"🧠 Analisando seu mapa..."},
"tr": {"language_saved":"✅ Dil Türkçe olarak değiştirildi.","language_title":"🌐 DİL","language_choose":"Arayüz dilini seçin:","btn_language":"🌐 Dil","btn_menu":"⬅️ Ana menü","btn_chart":"🌌 Haritam","btn_forecast":"🔮 Bugünün yorumu","btn_tomorrow":"🌙 Yarın","btn_ask":"💬 Haritama sor","btn_rel":"❤️ İlişkiler","btn_career":"💼 Kariyer","btn_money":"💰 Finans","btn_sub":"⭐ Abonelik","status_trial":"Deneme","status_prem":"Premium","status_exp":"Süresi doldu","chart_title":"🌌 DOĞUM HARİTANIZ","chart_title_short":"🌌 DOĞUM HARİTASI","sun":"Güneş","moon":"Ay","ascendant":"Yükselen","mc":"MC","mercury":"Merkür","venus":"Venüs","mars":"Mars","jupiter":"Jüpiter","saturn":"Satürn","uranus":"Uranüs","neptune":"Neptün","pluto":"Plüton","location":"Doğum yeri","house":"ev","retrograde":"retrograd","direct":"direkt","forecast_title":"🔮 YORUM","tomorrow_title":"✨ YARININ YORUMU","trial":"Deneme","premium":"Premium","expired":"Erişim sona erdi","aspects_title":"🔺 ÖNEMLİ NATAL AÇILAR","no_aspects":"Yakın majör açı tespit edilmedi.","natal_data_title":"📊 TEMEL GÖSTERGELER","analyzing":"🧠 Haritanız analiz ediliyor..."},
"uk": {"language_saved":"✅ Мову змінено на українську.","language_title":"🌐 МОВА","language_choose":"Оберіть мову інтерфейсу:","btn_language":"🌐 Мова","btn_menu":"⬅️ Головне меню","btn_chart":"🌌 Моя карта","btn_forecast":"🔮 Прогноз на сьогодні","btn_tomorrow":"🌙 Завтра","btn_ask":"💬 Запитати карту","btn_rel":"❤️ Стосунки","btn_career":"💼 Кар’єра","btn_money":"💰 Фінанси","btn_sub":"⭐ Підписка","status_trial":"Пробний період","status_prem":"Преміум","status_exp":"Завершено","chart_title":"🌌 ВАША НАТАЛЬНА КАРТА","chart_title_short":"🌌 НАТАЛЬНА КАРТА","sun":"Сонце","moon":"Місяць","ascendant":"Асцендент","mc":"MC","mercury":"Меркурій","venus":"Венера","mars":"Марс","jupiter":"Юпітер","saturn":"Сатурн","uranus":"Уран","neptune":"Нептун","pluto":"Плутон","location":"Місце народження","house":"дім","retrograde":"ретроградна","direct":"пряма","forecast_title":"🔮 ПРОГНОЗ","tomorrow_title":"✨ ПРОГНОЗ НА ЗАВТРА","trial":"Пробний період","premium":"Преміум","expired":"Доступ завершено","aspects_title":"🔺 КЛЮЧОВІ НАТАЛЬНІ АСПЕКТИ","no_aspects":"Тісних основних аспектів не виявлено.","natal_data_title":"📊 ОСНОВНІ ПОКАЗНИКИ","analyzing":"🧠 Аналізую вашу карту..."}
}

# Merge translated overrides; every missing key falls back to English.
for _lang, _data in EXTRA_TEXTS.items():
    TEXTS[_lang] = dict(TEXTS["en"])
    TEXTS[_lang].update(_data)
for _lang, _data in _BASE_TRANSLATIONS.items():
    TEXTS[_lang] = dict(TEXTS.get(_lang, TEXTS["en"]))
    TEXTS[_lang].update(_data)



def normalize_lang(lang: Optional[str]) -> str:
    code = (lang or "").lower().replace("_", "-").strip()
    if code.startswith("ru"): return "ru"
    if code.startswith("de"): return "de"
    if code.startswith("es"): return "es"
    if code.startswith("fr"): return "fr"
    if code.startswith("it"): return "it"
    if code.startswith("pt"): return "pt"
    if code.startswith("tr"): return "tr"
    if code.startswith("uk"): return "uk"
    return "en"


def t(key: str, lang: str = "en", **kwargs) -> str:
    lang = normalize_lang(lang)
    text = TEXTS.get(lang, TEXTS["en"]).get(
        key,
        TEXTS["en"].get(key, ""),
    )
    return text.format(**kwargs) if kwargs else text


# ============================================================
# ZODIAC
# ============================================================

ZODIAC_RU = [
    "Овен ♈", "Телец ♉", "Близнецы ♊", "Рак ♋",
    "Лев ♌", "Дева ♍", "Весы ♎", "Скорпион ♏",
    "Стрелец ♐", "Козерог ♑", "Водолей ♒", "Рыбы ♓",
]

ZODIAC_EN = [
    "Aries ♈", "Taurus ♉", "Gemini ♊", "Cancer ♋",
    "Leo ♌", "Virgo ♍", "Libra ♎", "Scorpio ♏",
    "Sagittarius ♐", "Capricorn ♑", "Aquarius ♒", "Pisces ♓",
]

ZODIAC_LOCALIZED = {
 "de":["Widder ♈","Stier ♉","Zwillinge ♊","Krebs ♋","Löwe ♌","Jungfrau ♍","Waage ♎","Skorpion ♏","Schütze ♐","Steinbock ♑","Wassermann ♒","Fische ♓"],
 "es":["Aries ♈","Tauro ♉","Géminis ♊","Cáncer ♋","Leo ♌","Virgo ♍","Libra ♎","Escorpio ♏","Sagitario ♐","Capricornio ♑","Acuario ♒","Piscis ♓"],
 "fr":["Bélier ♈","Taureau ♉","Gémeaux ♊","Cancer ♋","Lion ♌","Vierge ♍","Balance ♎","Scorpion ♏","Sagittaire ♐","Capricorne ♑","Verseau ♒","Poissons ♓"],
 "it":["Ariete ♈","Toro ♉","Gemelli ♊","Cancro ♋","Leone ♌","Vergine ♍","Bilancia ♎","Scorpione ♏","Sagittario ♐","Capricorno ♑","Acquario ♒","Pesci ♓"],
 "pt":["Áries ♈","Touro ♉","Gêmeos ♊","Câncer ♋","Leão ♌","Virgem ♍","Libra ♎","Escorpião ♏","Sagitário ♐","Capricórnio ♑","Aquário ♒","Peixes ♓"],
 "tr":["Koç ♈","Boğa ♉","İkizler ♊","Yengeç ♋","Aslan ♌","Başak ♍","Terazi ♎","Akrep ♏","Yay ♐","Oğlak ♑","Kova ♒","Balık ♓"],
 "uk":["Овен ♈","Телець ♉","Близнюки ♊","Рак ♋","Лев ♌","Діва ♍","Терези ♎","Скорпіон ♏","Стрілець ♐","Козеріг ♑","Водолій ♒","Риби ♓"],
}

SIGN_TRAITS = {
    "ru": [
        "инициатива, прямота, самостоятельность",
        "стабильность, чувственность, практичность",
        "любознательность, гибкость, коммуникация",
        "эмоциональность, забота, потребность в безопасности",
        "самовыражение, уверенность, творчество",
        "анализ, практичность, внимание к деталям",
        "гармония, дипломатия, чувство баланса",
        "глубина, интенсивность, проницательность",
        "расширение горизонтов, смысл, свобода",
        "цели, структура, ответственность",
        "независимость, оригинальность, свобода мышления",
        "интуиция, эмпатия, воображение",
    ],
    "en": [
        "initiative, directness, independence",
        "stability, sensuality, practicality",
        "curiosity, flexibility, communication",
        "emotion, care, need for security",
        "self-expression, confidence, creativity",
        "analysis, practicality, attention to detail",
        "harmony, diplomacy, balance",
        "depth, intensity, perception",
        "expansion, meaning, freedom",
        "goals, structure, responsibility",
        "independence, originality, freedom of thought",
        "intuition, empathy, imagination",
    ],
}


SIGN_TRAITS.update({
 "de":["Initiative, Direktheit, Unabhängigkeit","Stabilität, Sinnlichkeit, Praktikabilität","Neugier, Flexibilität, Kommunikation","Emotionen, Fürsorge, Sicherheitsbedürfnis","Selbstausdruck, Selbstvertrauen, Kreativität","Analyse, Praktikabilität, Detailgenauigkeit","Harmonie, Diplomatie, Balance","Tiefe, Intensität, Wahrnehmung","Horizonterweiterung, Sinn, Freiheit","Ziele, Struktur, Verantwortung","Unabhängigkeit, Originalität, freies Denken","Intuition, Empathie, Vorstellungskraft"],
 "es":["iniciativa, franqueza, independencia","estabilidad, sensualidad, practicidad","curiosidad, flexibilidad, comunicación","emoción, cuidado, necesidad de seguridad","expresión, confianza, creatividad","análisis, practicidad, atención al detalle","armonía, diplomacia, equilibrio","profundidad, intensidad, percepción","expansión, sentido, libertad","metas, estructura, responsabilidad","independencia, originalidad, libertad de pensamiento","intuición, empatía, imaginación"],
 "fr":["initiative, franchise, indépendance","stabilité, sensualité, pragmatisme","curiosité, flexibilité, communication","émotion, soin, besoin de sécurité","expression, confiance, créativité","analyse, pragmatisme, attention aux détails","harmonie, diplomatie, équilibre","profondeur, intensité, perception","expansion, sens, liberté","objectifs, structure, responsabilité","indépendance, originalité, liberté de pensée","intuition, empathie, imagination"],
 "it":["iniziativa, franchezza, indipendenza","stabilità, sensualità, praticità","curiosità, flessibilità, comunicazione","emozione, cura, bisogno di sicurezza","espressione, fiducia, creatività","analisi, praticità, attenzione ai dettagli","armonia, diplomazia, equilibrio","profondità, intensità, percezione","espansione, significato, libertà","obiettivi, struttura, responsabilità","indipendenza, originalità, libertà di pensiero","intuizione, empatia, immaginazione"],
 "pt":["iniciativa, franqueza, independência","estabilidade, sensualidade, praticidade","curiosidade, flexibilidade, comunicação","emoção, cuidado, necessidade de segurança","expressão, confiança, criatividade","análise, praticidade, atenção aos detalhes","harmonia, diplomacia, equilíbrio","profundidade, intensidade, percepção","expansão, sentido, liberdade","metas, estrutura, responsabilidade","independência, originalidade, liberdade de pensamento","intuição, empatia, imaginação"],
 "tr":["girişim, doğrudanlık, bağımsızlık","istikrar, duyusallık, pratiklik","merak, esneklik, iletişim","duygu, bakım, güvenlik ihtiyacı","kendini ifade, özgüven, yaratıcılık","analiz, pratiklik, ayrıntılara dikkat","uyum, diplomasi, denge","derinlik, yoğunluk, sezgi","genişleme, anlam, özgürlük","hedefler, yapı, sorumluluk","bağımsızlık, özgünlük, özgür düşünce","sezgi, empati, hayal gücü"],
 "uk":["ініціатива, прямота, самостійність","стабільність, чуттєвість, практичність","допитливість, гнучкість, комунікація","емоційність, турбота, потреба в безпеці","самовираження, впевненість, творчість","аналіз, практичність, увага до деталей","гармонія, дипломатія, баланс","глибина, інтенсивність, проникливість","розширення горизонтів, сенс, свобода","цілі, структура, відповідальність","незалежність, оригінальність, свобода думки","інтуїція, емпатія, уява"]
})

HOUSE_MEANINGS.update({'de': ['Identität, Körper und Selbstausdruck', 'Geld, persönliche Ressourcen und Selbstwert', 'Denken, Lernen und Alltagkommunikation', 'Zuhause, Familie, Wurzeln und innere Sicherheit', 'Kreativität, Freude, Romantik und Selbstausdruck', 'Arbeit, Routinen, Gesundheit und Organisation', 'Partnerschaften, Beziehungen und Austausch', 'Intimität, gemeinsame Ressourcen und tiefe Veränderung', 'Überzeugungen, Lernen, Reisen und Sinn', 'Karriere, Status und öffentliche Leistung', 'Freunde, Gemeinschaften, Pläne und Zukunft', 'Rückzug, unbewusste Muster, Innenleben und Erholung'], 'es': ['identidad, cuerpo y expresión personal', 'dinero, recursos personales y autoestima', 'pensamiento, aprendizaje y comunicación cotidiana', 'hogar, familia, raíces y seguridad interior', 'creatividad, placer, romance y expresión', 'trabajo, rutinas, salud y organización', 'parejas, relaciones e interacción', 'intimidad, recursos compartidos y transformación', 'creencias, aprendizaje, viajes y sentido', 'carrera, estatus y realización pública', 'amigos, comunidades, planes y futuro', 'soledad, patrones inconscientes, vida interior y recuperación'], 'fr': ['identité, corps et expression personnelle', 'argent, ressources personnelles et estime de soi', 'pensée, apprentissage et communication quotidienne', 'foyer, famille, racines et sécurité intérieure', 'créativité, plaisir, romance et expression', 'travail, routines, santé et organisation', 'partenariats, relations et interaction', 'intimité, ressources partagées et transformation', 'croyances, apprentissage, voyages et sens', 'carrière, statut et réalisation publique', 'amis, communautés, projets et avenir', 'solitude, inconscient, vie intérieure et récupération'], 'it': ['identità, corpo ed espressione personale', 'denaro, risorse personali e autostima', 'pensiero, apprendimento e comunicazione quotidiana', 'casa, famiglia, radici e sicurezza interiore', 'creatività, piacere, romanticismo ed espressione', 'lavoro, routine, salute e organizzazione', 'partnership, relazioni e interazione', 'intimità, risorse condivise e trasformazione', 'credenze, apprendimento, viaggi e significato', 'carriera, status e realizzazione pubblica', 'amici, comunità, piani e futuro', 'solitudine, inconscio, vita interiore e recupero'], 'pt': ['identidade, corpo e expressão pessoal', 'dinheiro, recursos pessoais e autoestima', 'pensamento, aprendizagem e comunicação diária', 'casa, família, raízes e segurança interior', 'criatividade, prazer, romance e expressão', 'trabalho, rotinas, saúde e organização', 'parcerias, relacionamentos e interação', 'intimidade, recursos compartilhados e transformação', 'crenças, aprendizagem, viagens e sentido', 'carreira, status e realização pública', 'amigos, comunidades, planos e futuro', 'recolhimento, inconsciente, vida interior e recuperação'], 'tr': ['kimlik, beden ve kendini ifade', 'para, kişisel kaynaklar ve özdeğer', 'düşünme, öğrenme ve günlük iletişim', 'ev, aile, kökler ve iç güvenlik', 'yaratıcılık, keyif, romantizm ve ifade', 'iş, rutinler, sağlık ve düzen', 'ortaklıklar, ilişkiler ve birebir etkileşim', 'yakınlık, ortak kaynaklar ve derin dönüşüm', 'inançlar, öğrenme, seyahat ve anlam', 'kariyer, statü ve toplumsal başarı', 'arkadaşlar, topluluklar, planlar ve gelecek', 'yalnızlık, bilinçdışı, iç dünya ve iyileşme'], 'uk': ['особистість, тіло та самовираження', 'гроші, особисті ресурси та самоцінність', 'мислення, навчання та щоденне спілкування', 'дім, сім’я, корені та внутрішня опора', 'творчість, задоволення, романтика та самовираження', 'робота, звички, здоров’я та організація', 'партнерство, стосунки та взаємодія', 'близькість, спільні ресурси та глибокі зміни', 'світогляд, навчання, подорожі та сенс', 'кар’єра, статус і суспільна реалізація', 'друзі, спільноти, плани та майбутнє', 'усамітнення, несвідоме, внутрішній світ і відновлення']})

# ============================================================
# ASTRO LABELS
# ============================================================

PLACEMENT_KEYS = [
    "Sun", "Moon", "Ascendant", "MC",
    "Mercury", "Venus", "Mars", "Jupiter",
    "Saturn", "Uranus", "Neptune", "Pluto",
]

PLANET_KEYS = [
    "Sun", "Moon", "Mercury", "Venus", "Mars",
    "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto",
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

PLANET_WEIGHTS = {
    "Sun": 1.0,
    "Moon": 1.0,
    "Mercury": 0.9,
    "Venus": 1.0,
    "Mars": 1.2,
    "Jupiter": 1.3,
    "Saturn": 1.5,
    "Uranus": 1.4,
    "Neptune": 1.4,
    "Pluto": 1.6,
}

ASPECTS = {
    0: ("Conjunction", 2.0),
    60: ("Sextile", 1.5),
    90: ("Square", 2.0),
    120: ("Trine", 2.0),
    180: ("Opposition", 2.0),
}

ASPECT_RU = {
    "Conjunction": "соединение",
    "Sextile": "секстиль",
    "Square": "квадрат",
    "Trine": "тригон",
    "Opposition": "оппозиция",
}

ASPECT_EN = {
    "Conjunction": "conjunction",
    "Sextile": "sextile",
    "Square": "square",
    "Trine": "trine",
    "Opposition": "opposition",
}

HOUSE_MEANINGS = {
    "ru": [
        "личность, тело и способ проявляться",
        "деньги, личные ресурсы и чувство ценности",
        "мышление, обучение и повседневное общение",
        "дом, семья, корни и внутреннее чувство опоры",
        "творчество, удовольствие, романтика и самовыражение",
        "работа, привычки, здоровье и организация жизни",
        "партнёрство, отношения и открытые взаимодействия",
        "близость, общие ресурсы и глубокие изменения",
        "мировоззрение, обучение, путешествия и смысл",
        "карьера, статус и общественная реализация",
        "друзья, сообщества, планы и будущее",
        "уединение, бессознательное, внутренний мир и восстановление",
    ],
    "en": [
        "identity, body and self-expression",
        "money, personal resources and self-worth",
        "thinking, learning and everyday communication",
        "home, family, roots and inner security",
        "creativity, pleasure, romance and self-expression",
        "work, routines, health and organization",
        "partnerships, relationships and one-to-one interaction",
        "intimacy, shared resources and deep transformation",
        "beliefs, learning, travel and meaning",
        "career, status and public achievement",
        "friends, communities, plans and the future",
        "solitude, unconscious patterns, inner life and recovery",
    ],
}


def planet_label(key: str, lang: str) -> str:
    return t(key.lower(), lang)


def format_deg_only(deg: float) -> str:
    deg = normalize_deg(deg)
    sign_degree = deg % 30
    whole = int(sign_degree)
    minutes = int(round((sign_degree - whole) * 60))
    if minutes >= 60:
        whole += 1
        minutes = 0
    if whole >= 30:
        whole = 0
    return f"{whole}°{minutes:02d}'"


def deg_to_sign(deg: float, lang: str = "ru") -> str:
    deg = normalize_deg(deg)
    sign_index = int(deg // 30)
    zodiac = ZODIAC_RU if normalize_lang(lang) == "ru" else (ZODIAC_EN if normalize_lang(lang) == "en" else ZODIAC_LOCALIZED.get(normalize_lang(lang), ZODIAC_EN))
    return f"{zodiac[sign_index]} {format_deg_only(deg)}"


def get_sign_index(deg: float) -> int:
    return int(normalize_deg(deg) // 30)


def get_sign_trait(deg: float, lang: str) -> str:
    return SIGN_TRAITS[normalize_lang(lang)][get_sign_index(deg)]


def house_for_degree(deg: float, cusps: List[float]) -> int:
    """Определяет дом для эклиптической долготы с учётом перехода 360/0."""
    deg = normalize_deg(deg)

    if len(cusps) < 12:
        return 1

    cusps = [normalize_deg(x) for x in cusps]

    for i in range(12):
        start = cusps[i]
        end = cusps[(i + 1) % 12]

        if start <= end:
            if start <= deg < end:
                return i + 1
        else:
            if deg >= start or deg < end:
                return i + 1

    return 12


# ============================================================
# ASTRO ENGINE
# ============================================================

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


def normalize_deg(deg: float) -> float:
    return deg % 360.0


def angular_distance(a: float, b: float) -> float:
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


def get_julian_day(dt_utc: datetime) -> float:
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


def planet_positions_raw(dt_utc: datetime) -> Dict[str, Dict[str, Any]]:
    jd = get_julian_day(dt_utc)
    result = {}

    for name, planet_id in TRACKED_PLANETS.items():
        calc = swe.calc_ut(jd, planet_id)
        values = calc[0]
        longitude = normalize_deg(values[0])
        speed = float(values[3]) if len(values) > 3 else 0.0

        result[name] = {
            "longitude": longitude,
            "speed": speed,
            "retrograde": speed < 0,
        }

    return result


def planet_positions(dt_utc: datetime) -> Dict[str, float]:
    return {
        key: value["longitude"]
        for key, value in planet_positions_raw(dt_utc).items()
    }


def calculate_houses(
    birth_utc: datetime,
    lat: float,
    lon: float,
) -> Tuple[List[float], float, float]:
    jd = get_julian_day(birth_utc)

    cusps, ascmc = swe.houses(
        jd,
        lat,
        lon,
        b"P",
    )

    cusps = list(cusps)
    asc = normalize_deg(ascmc[0])
    mc = normalize_deg(ascmc[1])

    return cusps, asc, mc


def calculate_natal_aspects(
    positions: Dict[str, Dict[str, Any]],
    max_results: int = 20,
) -> List[Dict[str, Any]]:
    found = []

    keys = list(positions.keys())

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            p1 = keys[i]
            p2 = keys[j]

            d1 = positions[p1]["longitude"]
            d2 = positions[p2]["longitude"]

            diff = angular_distance(d1, d2)

            for angle, (aspect_name, max_orb) in ASPECTS.items():
                orb = abs(diff - angle)

                if orb <= max_orb:
                    strength = (
                        (max_orb - orb + 0.1)
                        / (max_orb + 0.1)
                    )

                    importance = round(
                        strength
                        * PLANET_WEIGHTS.get(p1, 1.0)
                        * PLANET_WEIGHTS.get(p2, 1.0)
                        * 5.0,
                        2,
                    )

                    found.append({
                        "planet1": p1,
                        "planet2": p2,
                        "aspect": aspect_name,
                        "orb": round(orb, 2),
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


def get_natal_blueprint(
    birth_utc: datetime,
    lat: float,
    lon: float,
    lang: str = "ru",
) -> Dict[str, Any]:

    positions = planet_positions_raw(birth_utc)
    cusps, asc, mc = calculate_houses(
        birth_utc,
        lat,
        lon,
    )

    placements = {}

    for key in PLANET_KEYS:
        raw = positions[key]
        longitude = raw["longitude"]
        house = house_for_degree(
            longitude,
            cusps,
        )

        placements[key] = {
            "longitude": round(longitude, 6),
            "sign": deg_to_sign(longitude, lang),
            "degree": format_deg_only(longitude),
            "house": house,
            "house_meaning": HOUSE_MEANINGS[
                normalize_lang(lang)
            ][house - 1],
            "sign_trait": get_sign_trait(
                longitude,
                lang,
            ),
            "retrograde": raw["retrograde"],
            "speed": round(raw["speed"], 6),
        }

    placements["Ascendant"] = {
        "longitude": round(asc, 6),
        "sign": deg_to_sign(asc, lang),
        "degree": format_deg_only(asc),
        "house": 1,
        "house_meaning": HOUSE_MEANINGS[
            normalize_lang(lang)
        ][0],
        "sign_trait": get_sign_trait(asc, lang),
        "retrograde": False,
        "speed": 0,
    }

    placements["MC"] = {
        "longitude": round(mc, 6),
        "sign": deg_to_sign(mc, lang),
        "degree": format_deg_only(mc),
        "house": 10,
        "house_meaning": HOUSE_MEANINGS[
            normalize_lang(lang)
        ][9],
        "sign_trait": get_sign_trait(mc, lang),
        "retrograde": False,
        "speed": 0,
    }

    aspects = calculate_natal_aspects(
        positions
    )

    return {
        "placements": placements,
        "aspects": aspects,
        "houses": cusps,
        "ascendant": asc,
        "mc": mc,
    }


# ============================================================
# FORMAT NATAL DATA
# ============================================================

def format_placement_line(
    key: str,
    data: Any,
    lang: str,
) -> str:

    icon = PLACEMENT_ICONS.get(
        key,
        "•",
    )
    label = planet_label(
        key,
        lang,
    )

    # Совместимость со старым форматом.
    if isinstance(data, str):
        return (
            f"{icon} {label}: {data}\n"
            f"   ↳ {t(f'meaning_{key.lower()}', lang)}"
        )

    sign = data.get("sign", "—")
    house = data.get("house")
    trait = data.get("sign_trait", "")
    retrograde = data.get("retrograde", False)

    retro = (
        f" · {t('retrograde', lang)}"
        if retrograde
        else ""
    )

    house_text = (
        f" · {house} {t('house', lang)}"
        if house
        else ""
    )

    meaning = t(
        f"meaning_{key.lower()}",
        lang,
    )

    return (
        f"{icon} {label} — {sign}{retro}\n"
        f"   ↳ {meaning}{house_text}\n"
        f"   • {trait}"
    )


def format_aspects(
    aspects: List[Dict[str, Any]],
    lang: str,
    limit: int = 10,
) -> str:

    if not aspects:
        return t(
            "no_aspects",
            lang,
        )

    lines = []

    for item in aspects[:limit]:
        p1 = planet_label(
            item["planet1"],
            lang,
        )
        p2 = planet_label(
            item["planet2"],
            lang,
        )

        aspect = (
            ASPECT_RU.get(
                item["aspect"],
                item["aspect"],
            )
            if normalize_lang(lang) == "ru"
            else ASPECT_EN.get(
                item["aspect"],
                item["aspect"],
            )
        )

        lines.append(
            f"• {p1} {aspect} {p2} "
            f"· orb {item['orb']}°"
        )

    return "\n".join(lines)


def build_chart_card(
    bp: Dict[str, Any],
    name: str,
    lang: str,
    include_all: bool = True,
) -> str:

    title = t(
        "chart_title",
        lang,
    )

    placements = bp.get(
        "placements",
        bp,
    )

    if include_all:
        keys = PLACEMENT_KEYS
    else:
        keys = [
            "Sun",
            "Moon",
            "Ascendant",
            "MC",
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
        if key in placements:
            lines.append(
                format_placement_line(
                    key,
                    placements[key],
                    lang,
                )
            )
            lines.append("")

    aspects = bp.get(
        "aspects",
        [],
    )

    if include_all:
        lines.append(
            t(
                "aspects_title",
                lang,
            )
        )
        lines.append(
            format_aspects(
                aspects,
                lang,
                10,
            )
        )
        lines.append("")

    return "\n".join(
        lines
    ).rstrip()


def serialize_chart_for_ai(
    bp: Dict[str, Any],
    lang: str,
) -> str:

    placements = bp.get(
        "placements",
        {},
    )

    lines = []

    for key in PLACEMENT_KEYS:
        if key not in placements:
            continue

        data = placements[key]

        if isinstance(data, str):
            lines.append(
                f"- {key}: {data}"
            )
            continue

        retro = (
            "retrograde"
            if data.get("retrograde")
            else "direct"
        )

        lines.append(
            f"- {key}: "
            f"{data.get('sign')} | "
            f"house {data.get('house')} | "
            f"{retro} | "
            f"sign traits: {data.get('sign_trait')}"
        )

    lines.append("")
    lines.append("Natal aspects:")

    for item in bp.get("aspects", [])[:15]:
        lines.append(
            f"- {item['planet1']} "
            f"{item['aspect']} "
            f"{item['planet2']} "
            f"(orb {item['orb']}°)"
        )

    return "\n".join(lines)


# ============================================================
# TRANSITS
# ============================================================

def find_transits(
    birth_utc: datetime,
    target_utc: datetime,
    max_results: int = 8,
) -> List[Dict[str, Any]]:

    natal = planet_positions_raw(
        birth_utc
    )
    transit = planet_positions_raw(
        target_utc
    )
    previous = planet_positions_raw(
        target_utc - timedelta(days=1)
    )

    found = []

    for t_name, t_data in transit.items():
        t_deg = t_data["longitude"]

        for n_name, n_data in natal.items():
            # Быстрые планеты к самим себе не запрещаем,
            # кроме Солнца и Луны — это малоинформативно
            # для текущей логики прогноза.
            if (
                t_name == n_name
                and t_name in {"Sun", "Moon"}
            ):
                continue

            diff = angular_distance(
                t_deg,
                n_data["longitude"],
            )

            for angle, (
                aspect_name,
                max_orb,
            ) in ASPECTS.items():

                orb = abs(
                    diff - angle
                )

                if orb <= max_orb:
                    previous_diff = angular_distance(
                        previous[t_name]["longitude"],
                        n_data["longitude"],
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
                                    max_orb - orb + 0.2
                                )
                                * 3.2
                                * PLANET_WEIGHTS.get(
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
                        "orb": round(orb, 2),
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
    lang: str = "en",
) -> str:

    if not transits:
        return t(
            "no_aspects",
            lang,
        )

    lines = []

    for item in transits:
        tplanet = planet_label(
            item["transit_planet"],
            lang,
        )
        nplanet = planet_label(
            item["natal_planet"],
            lang,
        )

        aspect = (
            ASPECT_RU.get(
                item["aspect"],
                item["aspect"],
            )
            if normalize_lang(lang) == "ru"
            else ASPECT_EN.get(
                item["aspect"],
                item["aspect"],
            )
        )

        lines.append(
            f"- {tplanet} {aspect} "
            f"{nplanet} "
            f"(orb {item['orb']}°, "
            f"{item['motion']})"
        )

    return "\n".join(lines)


def serialize_transits_for_ai(
    transits: List[Dict[str, Any]],
) -> str:

    if not transits:
        return "No major tight transits detected."

    return "\n".join(
        (
            f"- {item['transit_planet']} "
            f"{item['aspect']} "
            f"natal {item['natal_planet']} "
            f"(orb {item['orb']}°, "
            f"{item['motion']}, "
            f"importance {item['importance']}/10)"
        )
        for item in transits
    )


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
            [InlineKeyboardButton(
                text=t("plan_1m_btn", lang),
                callback_data="buy_plan_1m",
            )],
            [InlineKeyboardButton(
                text=t("plan_3m_btn", lang),
                callback_data="buy_plan_3m",
            )],
            [InlineKeyboardButton(
                text=t("plan_6m_btn", lang),
                callback_data="buy_plan_6m",
            )],
            [InlineKeyboardButton(
                text=t("plan_1y_btn", lang),
                callback_data="buy_plan_1y",
            )],
            [InlineKeyboardButton(
                text=t("btn_menu", lang),
                callback_data="menu",
            )],
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
            [
                InlineKeyboardButton(
                    text=t("btn_language", lang),
                    callback_data="language",
                )
            ],
        ]
    )


def language_keyboard(current_lang: str = "en") -> InlineKeyboardMarkup:
    current_lang = normalize_lang(current_lang)
    labels = {
        "ru": "🇷🇺 Русский", "en": "🇬🇧 English", "de": "🇩🇪 Deutsch",
        "es": "🇪🇸 Español", "fr": "🇫🇷 Français", "it": "🇮🇹 Italiano",
        "pt": "🇵🇹 Português", "tr": "🇹🇷 Türkçe", "uk": "🇺🇦 Українська",
    }
    rows = []
    langs = list(labels)
    for i in range(0, len(langs), 2):
        row = []
        for code in langs[i:i+2]:
            mark = " ✓" if code == current_lang else ""
            row.append(InlineKeyboardButton(text=labels[code] + mark, callback_data=f"set_lang_{code}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text=t("btn_menu", current_lang), callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



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


async def update_user_language(user_id: int, lang: str) -> bool:
    lang = normalize_lang(lang)
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE users SET language_code = $1 WHERE user_id = $2",
            lang,
            user_id,
        )
    return result.endswith("1")


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
            await conn.execute(
                """
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
                name, b_date, b_time, city,
                lat, lon, tz_str, lang, user_id,
            )
        else:
            trial_end = (
                now_utc + timedelta(days=7)
            ).isoformat()

            await conn.execute(
                """
                INSERT INTO users (
                    user_id, name, birth_date, birth_time,
                    city, lat, lon, timezone,
                    trial_until, premium_until, is_active,
                    created_at, trial_used, language_code
                )
                VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,NULL,
                    1,$10,1,$11
                )
                """,
                user_id, name, b_date, b_time,
                city, lat, lon, tz_str,
                trial_end, now_utc.isoformat(), lang,
            )


async def add_premium_days(user_id: int, days: int):
    now_utc = datetime.now(UTC)

    async with db_pool.acquire() as conn:
        current_until = await conn.fetchval(
            "SELECT premium_until FROM users WHERE user_id = $1",
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
            SET premium_until = $1, is_active = 1
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
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (telegram_payment_charge_id)
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
            "SELECT * FROM users WHERE is_active = 1"
        )
        return [dict(row) for row in rows]


async def deactivate_user(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_active = 0 WHERE user_id = $1",
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
                user_id, forecast_date, sent_at
            )
            VALUES ($1,$2,$3)
            ON CONFLICT (user_id, forecast_date)
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
                user_id, usage_date, requests
            )
            VALUES ($1,$2,1)
            ON CONFLICT (user_id, usage_date)
            DO UPDATE SET
                requests = ai_usage.requests + 1
            """,
            user_id,
            local_today,
        )

        return True


# ============================================================
# GEMINI
# ============================================================

SYSTEM_PROMPT = """
You are Aura Astro, an elite psychological astrologer,
self-awareness mentor, and reflective guide.

Astrology is a symbolic psychological reflection tool.
Never present it as deterministic or scientifically proven.

RULES:

1. Never use fear-based predictions.
2. Never claim that an event is guaranteed.
3. Never say a person is doomed, cursed, or destined.
4. Never make medical, legal, or financial guarantees.
5. Never use generic horoscope clichés.
6. Use the ACTUAL natal placements supplied.
7. Combine planets, signs, houses and aspects.
8. Explain psychological mechanisms, not isolated sign stereotypes.
9. Prioritize personality, emotions, communication,
   motivation, boundaries, behavior and decision-making.
10. Give practical and psychologically useful insights.
11. Never invent placements, houses, aspects or transits.
12. If birth time is uncertain, avoid overconfidence
    about Ascendant, MC and houses.
13. Do not claim exact life events.
14. Use complete, properly punctuated sentences.
15. Never switch languages.
16. Do not mention being an AI unless directly asked.
17. Do not repeat the entire chart unnecessarily.
18. Avoid empty mystical phrases.
19. Make every answer feel written for THIS person.
20. When an aspect is supplied, explain the interaction
    between the two planets rather than merely naming it.
21. When a house is supplied, connect it to the life area
    without claiming certainty about events.
"""


async def call_gemini_safe(
    prompt: str,
    lang: str = "en",
    max_tokens: int = 2400,
) -> str:
    lang = normalize_lang(lang)
    lang_name = {
        "ru":"Russian", "en":"English", "de":"German", "es":"Spanish",
        "fr":"French", "it":"Italian", "pt":"Portuguese",
        "tr":"Turkish", "uk":"Ukrainian",
    }.get(lang, "English")
    full_prompt = prompt + f"\n\nCRITICAL LANGUAGE RULE:\nRespond entirely in {lang_name}. Do not switch languages. Use complete sentences."
    models = []
    for model in MODELS_CHAIN:
        if model and model not in models:
            models.append(model)
    for model_candidate in models:
        try:
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=model_candidate,
                contents=full_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=max(max_tokens, 4000),
                ),
            )
            text = (response.text or "").strip()
            finish_reason = ""
            try:
                finish_reason = str(response.candidates[0].finish_reason).upper() if response.candidates else ""
            except Exception:
                pass
            if text and "MAX_TOKENS" in finish_reason:
                parts = [text]
                for _ in range(2):
                    continuation = await asyncio.to_thread(
                        ai_client.models.generate_content,
                        model=model_candidate,
                        contents=(
                            f"Continue the following {lang_name} response EXACTLY from where it stopped. "
                            f"Do not restart, repeat or summarize. Finish incomplete thoughts and sections. "
                            f"Output only the continuation.\n\n{text[-7000:]}"
                        ),
                        config=genai_types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            max_output_tokens=max(max_tokens, 4000),
                        ),
                    )
                    extra = (continuation.text or "").strip()
                    if not extra:
                        break
                    parts.append(extra)
                    text += " " + extra
                    try:
                        fr = str(continuation.candidates[0].finish_reason).upper() if continuation.candidates else ""
                    except Exception:
                        fr = ""
                    if "MAX_TOKENS" not in fr:
                        break
                text = "\n".join(parts).strip()
            if text:
                print(f"[Gemini OK] model={model_candidate} finish={finish_reason}", flush=True)
                return text
        except Exception as e:
            print(f"[Gemini Failover] model={model_candidate} error={e}", flush=True)
    fallbacks = {
        "ru":"Не удалось получить AI-разбор прямо сейчас. Попробуйте ещё раз через несколько секунд.",
        "en":"The AI interpretation is temporarily unavailable. Please try again in a few seconds.",
        "de":"Die KI-Interpretation ist momentan nicht verfügbar. Bitte versuche es in einigen Sekunden erneut.",
        "es":"La interpretación de IA no está disponible ahora. Inténtalo de nuevo en unos segundos.",
        "fr":"L’interprétation IA est temporairement indisponible. Réessayez dans quelques secondes.",
        "it":"L’interpretazione IA non è temporaneamente disponibile. Riprova tra qualche secondo.",
        "pt":"A interpretação de IA está temporariamente indisponível. Tente novamente em alguns segundos.",
        "tr":"Yapay zekâ yorumu şu anda kullanılamıyor. Lütfen birkaç saniye sonra tekrar deneyin.",
        "uk":"Інтерпретація ШІ зараз недоступна. Спробуйте ще раз за кілька секунд.",
    }
    return fallbacks.get(lang, fallbacks["en"])

# ============================================================
# AI — NATAL
# ============================================================

async def generate_blueprint_text(
    name: str,
    bp: Dict[str, Any],
    lang: str,
) -> str:

    chart_data = serialize_chart_for_ai(
        bp,
        lang,
    )

    prompt = f"""
Client name:
{name}

FULL NATAL DATA:
{chart_data}

Create a deeply personal psychological interpretation
of this exact natal chart.

Use combinations of:
- Sun and Moon
- Ascendant and Sun
- Mercury and Mars
- Venus, Moon and Mars
- houses where relevant
- the strongest supplied natal aspects
- retrograde planets when relevant

Length: approximately 500–650 words.

Structure exactly:

🧠 АРХИТЕКТУРА ЛИЧНОСТИ

Explain Sun + Moon together.
Describe identity, emotional needs, inner tension
and psychological resources.

🌅 ВНЕШНЕЕ ПРОЯВЛЕНИЕ

Use Ascendant + Sun + relevant 1st-house information.
Explain first impression and outer behavior.

⚡ МЫШЛЕНИЕ И ДЕЙСТВИЯ

Use Mercury + Mars and relevant houses/aspects.
Explain thinking, communication, motivation,
decision-making and conflict style.

❤️ ОТНОШЕНИЯ

Use Venus + Moon + Mars plus relevant houses/aspects.
Explain attraction, intimacy, boundaries and needs.

💼 РЕАЛИЗАЦИЯ

Use MC, 10th-house information, Sun, Saturn, Mars
and relevant aspects.
Explain work identity and natural strategic strengths.

🔺 ВНУТРЕННЯЯ ДИНАМИКА

Choose the 2 most psychologically meaningful natal
aspects supplied and explain the tension or resource
they create.

💎 ГЛАВНАЯ СИЛА

Identify one distinctive strength emerging from
multiple chart factors.

🎯 ГЛАВНЫЙ ВЫВОД

Give a concise personal conclusion.

Do not simply list placements.
Connect them.
Do not invent anything.
Do not predict exact events.
Do not use generic motivational clichés.
"""

    return await call_gemini_safe(
        prompt,
        lang,
        4200,
    )


# ============================================================
# AI — DAILY
# ============================================================

async def generate_daily_forecast(
    name: str,
    bp: Dict[str, Any],
    transits: List[Dict[str, Any]],
    target_date: date,
    lang: str,
) -> str:

    chart_data = serialize_chart_for_ai(
        bp,
        lang,
    )

    prompt = f"""
Client:
{name}

Date:
{target_date.isoformat()}

Natal chart:
{chart_data}

Current transits:
{serialize_transits_for_ai(transits)}

Create a personalized daily psychological astrology forecast.

Length: approximately 280–350 words.

Structure:

🔮 ТЕМА ДНЯ

A concise meaningful theme based on actual transits.

🧠 ПСИХОЛОГИЧЕСКИЙ ФОН

Explain how the strongest transit may symbolically
highlight a natal pattern.

❤️ ОТНОШЕНИЯ

Explain the most relevant emotional or relational theme.

💼 ДЕЛА И РЕАЛИЗАЦИЯ

Explain the most useful approach to work and decisions.

⚡ ТАКТИЧЕСКИЕ ДЕЙСТВИЯ

Exactly two practical bullet points.

🎯 ВОПРОС ДЛЯ СЕБЯ

One thoughtful question.

Do not predict exact events.
Do not use fear.
Do not say "something unexpected will happen".
Use the supplied natal houses and aspects when relevant.
"""


    return await call_gemini_safe(
        prompt,
        lang,
        2800,
    )


# ============================================================
# AI — ASK
# ============================================================

async def generate_chart_answer(
    user: Dict[str, Any],
    bp: Dict[str, Any],
    transits: List[Dict[str, Any]],
    question: str,
) -> str:

    lang = normalize_lang(
        user.get(
            "language_code",
            "en",
        )
    )

    chart_data = serialize_chart_for_ai(
        bp,
        lang,
    )

    prompt = f"""
Client:
{user['name']}

Question:
{question}

Natal chart:
{chart_data}

Current transits:
{serialize_transits_for_ai(transits)}

Answer the client's actual question as a personal
psychological astrology mentor.

Length: approximately 300–380 words.

Requirements:

1. Answer the actual question directly.
2. Use 2–4 relevant natal factors.
3. Prefer combinations of planet + sign + house + aspect.
4. Explain the psychological mechanism.
5. If a current transit is relevant, explain why.
6. Give exactly two practical action steps.
7. Do not make deterministic predictions.
8. Do not invent information.
9. Make the answer feel personal and specific.
10. Do not simply repeat the question.
"""

    return await call_gemini_safe(
        prompt,
        lang,
        3000,
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
        f"{user['birth_date']}T{user['birth_time']}"
    )

    local_dt = local_dt.replace(
        tzinfo=tz
    )

    return local_dt.astimezone(UTC)


def get_status_str(
    access: Dict[str, Any],
    lang: str,
) -> str:

    if not access["has_access"]:
        return t(
            "status_exp",
            lang,
        )

    status = (
        t("status_prem", lang)
        if access["status"] == "premium"
        else t("status_trial", lang)
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

    paragraphs = text.split("\n\n")
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
                parts.append(current)

            if len(paragraph) > 3800:
                for i in range(
                    0,
                    len(paragraph),
                    3700,
                ):
                    parts.append(
                        paragraph[i:i + 3700]
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

        await message.answer(
            t(
                "welcome_back",
                user_lang,
                name=html.escape(
                    user["name"]
                ),
                status=get_status_str(
                    access,
                    user_lang,
                ),
            ),
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
# REGISTRATION
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
    ).strip()[:60]

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

        if (
            birth_date > date.today()
            or birth_date.year < 1900
        ):
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
        if len(value) == 5:
            normalized_time = value + ":00"
        elif len(value) == 8:
            normalized_time = value
        else:
            raise ValueError

        time.fromisoformat(
            normalized_time
        )

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

    lat = float(location.latitude)
    lon = float(location.longitude)

    tz_str = (
        tf.timezone_at(
            lng=lon,
            lat=lat,
        )
        or "UTC"
    )

    try:
        birth_local = datetime.fromisoformat(
            f"{data['birth_date']}T{data['birth_time']}"
        )

        birth_local = birth_local.replace(
            tzinfo=ZoneInfo(tz_str)
        )

        birth_utc = birth_local.astimezone(
            UTC
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
            lang,
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
        include_all=True,
    )

    full_text = (
        card
        + "\n\n"
        + f"📍 {t('location', lang)}: "
        + html.escape(
            location.address or city_query
        )
        + "\n"
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
        main_menu_keyboard(lang),
    )

    await state.clear()


# ============================================================
# LANGUAGE
# ============================================================

@dp.callback_query(F.data == "language")
async def cb_language(cb: types.CallbackQuery):
    user = await get_user(cb.from_user.id)
    if not user:
        await cb.answer("Please /start first.", show_alert=True)
        return

    lang = normalize_lang(user.get("language_code", "en"))
    await cb.answer()
    await cb.message.edit_text(
        f"{t('language_title', lang)}\n\n{t('language_choose', lang)}",
        reply_markup=language_keyboard(lang),
    )


@dp.callback_query(F.data.regexp(r"^set_lang_(ru|en|de|es|fr|it|pt|tr|uk)$"))
async def cb_set_language(cb: types.CallbackQuery):
    user = await get_user(cb.from_user.id)
    if not user:
        await cb.answer("Please /start first.", show_alert=True)
        return

    new_lang = cb.data.removeprefix("set_lang_")
    await update_user_language(cb.from_user.id, new_lang)
    await cb.answer(t("language_saved", new_lang))

    user = await get_user(cb.from_user.id)
    access = await get_user_access(cb.from_user.id)
    await cb.message.edit_text(
        t(
            "welcome_back",
            new_lang,
            name=html.escape(user["name"]),
            status=get_status_str(access, new_lang),
        ),
        reply_markup=main_menu_keyboard(new_lang),
    )


# ============================================================
# MAIN MENU
# ============================================================

@dp.callback_query(F.data == "menu")
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

@dp.callback_query(F.data == "chart")
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
            lang,
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
        back_menu_keyboard(lang),
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
            reply_markup=pricing_keyboard(lang),
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

    target_date = (
        local_date
        if cb.data == "forecast"
        else local_date + timedelta(days=1)
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
            lang,
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
        t("forecast_title", lang)
        if cb.data == "forecast"
        else t("tomorrow_title", lang)
    )

    header = (
        f"{title}\n"
        f"📅 {target_date.strftime('%d.%m.%Y')}\n\n"
    )

    await send_long_message(
        cb.message.chat.id,
        header + reading,
        back_menu_keyboard(lang),
    )


# ============================================================
# ASK MY CHART
# ============================================================

@dp.callback_query(F.data == "ask_chart")
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
            reply_markup=pricing_keyboard(lang),
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


@dp.message(AskChartState.question)
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
            reply_markup=pricing_keyboard(lang),
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
            reply_markup=main_menu_keyboard(lang),
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
            lang,
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
            reply_markup=main_menu_keyboard(lang),
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
        main_menu_keyboard(lang),
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
            reply_markup=pricing_keyboard(lang),
        )
        return

    topic_data = {
        "topic_relationships": {
            "title_ru": "❤️ ОТНОШЕНИЯ И БЛИЗОСТЬ",
            "title_en": "❤️ RELATIONSHIPS & INTIMACY",
            "focus": (
                "Venus, Moon, Mars, 5th/7th/8th house themes, "
                "natal aspects involving Venus/Moon/Mars, "
                "emotional intimacy, attraction, attachment patterns, "
                "communication, boundaries and conflict."
            ),
        },
        "topic_career": {
            "title_ru": "💼 КАРЬЕРА И ПРИЗВАНИЕ",
            "title_en": "💼 CAREER & VOCATION",
            "focus": (
                "MC, 10th house, Saturn, Mars, Sun, 2nd/6th/10th "
                "house themes, ambition, leadership, discipline, "
                "motivation, work identity and strategic choices."
            ),
        },
        "topic_money": {
            "title_ru": "💰 ФИНАНСЫ И РЕСУРСЫ",
            "title_en": "💰 MONEY & RESOURCES",
            "focus": (
                "2nd and 8th houses, Jupiter, Venus, Saturn, "
                "resource management, spending patterns, risk "
                "perception, self-worth, discipline and long-term strategy."
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
            lang,
        )

        transits = find_transits(
            birth_utc,
            datetime.now(UTC),
        )

        chart_data = serialize_chart_for_ai(
            bp,
            lang,
        )

        prompt = f"""
Client:
{user['name']}

Analysis domain:
{info['focus']}

Natal chart:
{chart_data}

Current transits:
{serialize_transits_for_ai(transits)}

Create a deep personal psychological astrology reading
focused strictly on the selected domain.

Length: approximately 300–380 words.

Structure exactly:

1. CORE PATTERN
Explain the person's natural pattern in this area
using several actual chart factors.

2. KEY STRENGTH
Identify the strongest useful resource.

3. CURRENT MOMENTUM
Explain which supplied transits symbolically
highlight the theme.

4. TACTICAL RECOMMENDATIONS
Give exactly two practical recommendations.

Do not invent houses, aspects or transits.
Do not make deterministic predictions.
Do not use generic horoscope clichés.
"""

        reading = await call_gemini_safe(
            prompt,
            lang,
            3000,
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
        back_menu_keyboard(lang),
    )


# ============================================================
# SUBSCRIPTION
# ============================================================

@dp.callback_query(F.data == "subscription")
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

    if access["status"] == "premium":
        status_text = t(
            "status_prem",
            lang,
        )
    elif access["status"] == "trial":
        status_text = t(
            "status_trial",
            lang,
        )
    else:
        status_text = t(
            "status_exp",
            lang,
        )

    days_text = (
        t(
            "days_left",
            lang,
            days=access["days_left"],
        )
        if access["has_access"]
        else ""
    )

    txt = (
        f"{t('subscription_title', lang)}\n\n"
        f"{status_text}\n"
        f"{days_text}\n\n"
        f"{t('subscription_choose', lang)}"
    )

    await cb.message.edit_text(
        txt,
        reply_markup=pricing_keyboard(lang),
    )


# ============================================================
# PAYMENTS
# ============================================================

@dp.callback_query(F.data.startswith("buy_plan_"))
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


@dp.message(F.successful_payment)
async def process_success(
    message: types.Message,
):

    payment = message.successful_payment

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
            f"[Payment Error] Unknown plan: {plan_key}",
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

    if not inserted:
        await message.answer(
            t(
                "payment_already_processed",
                lang,
            ),
            reply_markup=main_menu_keyboard(lang),
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
        reply_markup=main_menu_keyboard(lang),
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

            local_now = now_utc.astimezone(tz)

            if local_now.hour != 20:
                continue

            forecast_date = (
                local_now.date()
                + timedelta(days=1)
            )

            forecast_key = forecast_date.isoformat()

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
                await bot.send_message(
                    user_id,
                    t(
                        "paywall_msg",
                        lang,
                    ),
                    reply_markup=pricing_keyboard(lang),
                )

                await mark_forecast_sent(
                    user_id,
                    forecast_key,
                )

                await asyncio.sleep(0.3)
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
                lang,
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

            message_text = (
                f"{t('tomorrow_title', lang)}\n"
                f"📅 {forecast_date.strftime('%d.%m.%Y')}"
                f"\n\n"
                f"{reading}\n\n"
                f"⏳ {get_status_str(access, lang)}"
            )

            await send_long_message(
                user_id,
                message_text,
                main_menu_keyboard(lang),
            )

            await mark_forecast_sent(
                user_id,
                forecast_key,
            )

            await asyncio.sleep(1.0)

        except TelegramForbiddenError:
            print(
                f"[Scheduler] User {user_id} blocked bot.",
                flush=True,
            )

            await deactivate_user(
                user_id
            )

        except TelegramRetryAfter as e:
            print(
                f"[Scheduler] Rate limit: {e.retry_after}s",
                flush=True,
            )

            await asyncio.sleep(
                e.retry_after
            )

        except Exception as e:
            print(
                f"[Scheduler User {user_id}] {e}",
                flush=True,
            )


# ============================================================
# WEB SERVER
# ============================================================

async def health_check(request):
    return web.Response(
        text="Aura Astro v6.0 Online",
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

    runner = web.AppRunner(app)

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
        "🚀 Aura Astro v5.0 is running.",
        flush=True,
    )

    print(
        f"🤖 Gemini model: {DEFAULT_GEMINI_MODEL}",
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
