import os
import time
import json
import random
import threading
from datetime import datetime
from typing import List, Dict, Any

import schedule
import requests
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask

# =========================
# WEB SERVER (Render Web Service)
# =========================
app = Flask(__name__)

@app.get("/")
def index():
    return "ok", 200

@app.get("/healthz")
def healthz():
    return "ok", 200


# =========================
# ENV
# =========================
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL_CHAT = os.getenv("OPENAI_MODEL_CHAT", "gpt-4o-mini")
OPENAI_MODEL_IMAGE = os.getenv("OPENAI_MODEL_IMAGE", "gpt-image-1")

LOG_FILE = "posts_log.json"

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID or not OPENAI_API_KEY:
    raise ValueError("Не заданы TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID или OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================
# LOGS
# =========================
def load_logs() -> List[Dict[str, Any]]:
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print("Ошибка чтения логов:", e)
        return []


def save_logs(logs: List[Dict[str, Any]]) -> None:
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Ошибка записи логов:", e)


def log_post(slot: str, text: str, image_url: str, time_planned: str) -> None:
    logs = load_logs()
    logs.append({
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "slot": slot,
        "text": text,
        "image_url": image_url,
        "time_planned": time_planned,
        "time_sent": datetime.now().strftime("%H:%M"),
    })
    save_logs(logs)


def get_last_texts_for_slot(slot: str, limit: int = 3) -> List[str]:
    logs = load_logs()
    slot_logs = [l for l in logs if l.get("slot") == slot]
    return [l.get("text", "") for l in slot_logs[-limit:] if l.get("text")]


# =========================
# WEEKDAY TOPICS
# =========================
def get_weekday_topic() -> str:
    weekday = datetime.now().weekday()
    topics = {
        0: "Сегодня понедельник — эмоции во снах и чувства, спрятанные за образами.",
        1: "Сегодня вторник — природные символы: вода, лес, животные и их подсказки.",
        2: "Сегодня среда — архетипы: дом, коридоры, комнаты, двери.",
        3: "Сегодня четверг — знаки и предчувствия, сны-подсказки.",
        4: "Сегодня пятница — повторяющиеся и навязчивые сны.",
        5: "Сегодня суббота — энергия сна и внутренние потоки.",
        6: "Сегодня воскресенье — восстановление и медитативные образы.",
    }
    return topics.get(weekday, "")


# =========================
# TEXT GENERATION (эзотерика)
# =========================
def generate_post_text(slot: str) -> str:
    slot_prompt = {
        "morning": "Утренний мягкий эзотерический пост о снах как ночных посланиях.",
        "day": "Дневной пост о символах во снах, знаках и подсознательных подсказках.",
        "evening": "Вечерний спокойный пост-ритуал перед погружением в сон.",
    }.get(slot, "Эзотерический пост о снах.")

    system_message = (
        "Ты — автор эзотерического Telegram-канала о толковании снов. "
        "Пиши мягко, красиво, без страшилок. Символы, энергия, знаки, Вселенная — "
        "но понятным и тёплым языком."
    )

    history = ""
    last_texts = get_last_texts_for_slot(slot)
    if last_texts:
        history = "\n\nНе повторяй идеи этих недавних постов:\n" + "\n---\n".join(last_texts)

    user_message = (
        f"{slot_prompt}\n\n"
        f"{get_weekday_topic()}\n\n"
        "Объём 500–900 символов. Без хэштегов. 1–3 эмодзи внутри текста."
        f"{history}"
    )

    r = client.chat.completions.create(
        model=OPENAI_MODEL_CHAT,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        max_tokens=700,
        temperature=0.95,
    )

    text = r.choices[0].message.content.strip()

    footer = (
        "\n\n—\n"
        "Хочешь получить личное послание из своих снов? "
        "Отправь сон боту @whatdreams_bot — он аккуратно расшифрует его 🌙"
    )

    full = text + footer
    if len(full) > 1024:
        allowed = 1024 - len(footer) - 3
        full = text[:allowed].rstrip() + "..." + footer

    return full


# =========================
# IMAGE GENERATION
# =========================
def generate_image_url(slot: str) -> str:
    weekday = datetime.now().weekday()
    weekday_style = {
        0: "эмоции, сияние сердца, мягкие волны энергии",
        1: "вода, лес, силуэты животных, мистический туман",
        2: "дом, коридоры, двери, архетипичные формы",
        3: "созвездия, знаки, ночное небо",
        4: "повторяющиеся спирали, циклы, лестницы",
        5: "энергетические потоки, аура, свет",
        6: "спокойная вода, луна, медитативный пейзаж",
    }.get(weekday, "мистический пейзаж сна")

    base_style = {
        "morning": "рассвет, мягкое пробуждение, тёплые тона",
        "day": "ясные контуры, символы сна, лёгкий сюрреализм",
        "evening": "ночь, звёзды, глубокие оттенки",
    }.get(slot, "атмосфера сна")

    prompt = (
        "Иллюстрация для эзотерического Telegram-канала о толковании снов: "
        f"{weekday_style}, {base_style}. "
        "Без текста, без надписей, без логотипов."
    )

    img = client.images.generate(
        model=OPENAI_MODEL_IMAGE,
        prompt=prompt,
        n=1,
        size="1024x1024",
    )
    return img.data[0].url


# =========================
# TELEGRAM SEND
# =========================
def send_photo_to_telegram(image_url: str, caption: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "photo": image_url,
        "caption": caption,
    }
    r = requests.post(url, data=payload, timeout=60)
    if not r.ok:
        print("Ошибка Telegram:", r.status_code, r.text)
    else:
        print("Пост отправлен в канал.")


def create_and_send_post(slot: str, time_planned: str) -> None:
    print(f"--- {slot.upper()} пост (план {time_planned}) ---")
    text = generate_post_text(slot)
    image_url = generate_image_url(slot)
    send_photo_to_telegram(image_url, text)
    log_post(slot, text, image_url, time_planned)


# =========================
# SCHEDULER
# =========================
def random_time_in_range(start_hour: int, end_hour_exclusive: int) -> str:
    h = random.randint(start_hour, end_hour_exclusive - 1)
    m = random.randint(0, 59)
    return f"{h:02d}:{m:02d}"


def schedule_daily_posts() -> None:
    print("Перенастраиваем расписание на новый день...")

    schedule.clear("morning")
    schedule.clear("day")
    schedule.clear("evening")

    t_morning = random_time_in_range(8, 9)
    t_day = "15:19"          # 🔥 ФИКСИРОВАННО
    t_evening = random_time_in_range(18, 19)

    schedule.every().day.at(t_morning).do(lambda: create_and_send_post("morning", t_morning)).tag("morning")
    schedule.every().day.at(t_day).do(lambda: create_and_send_post("day", t_day)).tag("day")
    schedule.every().day.at(t_evening).do(lambda: create_and_send_post("evening", t_evening)).tag("evening")

    print("Текущее расписание:")
    print(f" - morning в {t_morning}")
    print(f" - day в {t_day}")
    print(f" - evening в {t_evening}")


def scheduler_loop():
    schedule_daily_posts()
    schedule.every().day.at("00:05").do(schedule_daily_posts).tag("rescheduler")
    print("Планировщик запущен и ждёт расписание...")
    while True:
        schedule.run_pending()
        time.sleep(1)


# =========================
# ENTRYPOINT
# =========================
if __name__ == "__main__":
    threading.Thread(target=scheduler_loop, daemon=True).start()

    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
