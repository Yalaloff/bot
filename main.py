import os
import time
import json
import random
import threading
import traceback
from datetime import datetime
from typing import List, Dict, Any

import schedule
import requests
from dotenv import load_dotenv
from openai import OpenAI
from flask import Flask, request

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
MANUAL_PUBLISH_TOKEN = os.getenv("MANUAL_PUBLISH_TOKEN", "")

LOG_FILE = "posts_log.json"

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID or not OPENAI_API_KEY:
    raise ValueError("❌ Не заданы TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID / OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================
# LOGS
# =========================
def load_logs() -> List[Dict[str, Any]]:
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_logs(logs: List[Dict[str, Any]]) -> None:
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def log_post(slot: str, text: str, image_url: str, time_planned: str) -> None:
    logs = load_logs()
    logs.append({
        "timestamp": datetime.now().isoformat(),
        "slot": slot,
        "time_planned": time_planned,
        "time_sent": datetime.now().strftime("%H:%M"),
    })
    save_logs(logs)


# =========================
# WEEKDAY TOPIC
# =========================
def get_weekday_topic() -> str:
    topics = [
        "Эмоции во снах и скрытые чувства",
        "Природные символы: вода, лес, животные",
        "Архетипы: дом, коридоры, двери",
        "Знаки и предчувствия",
        "Повторяющиеся сны",
        "Энергия сна и подсознания",
        "Восстановление и медитативные образы",
    ]
    return f"Сегодня тема: {topics[datetime.now().weekday()]}"


# =========================
# TEXT GENERATION
# =========================
def generate_post_text(slot: str) -> str:
    print(f"[GEN] Генерируем текст для слота: {slot}", flush=True)

    r = client.chat.completions.create(
        model=OPENAI_MODEL_CHAT,
        messages=[
            {"role": "system", "content": "Ты эзотерический автор, пишешь мягко и красиво."},
            {"role": "user", "content": f"{get_weekday_topic()}. Объём 500–700 символов."},
        ],
        max_tokens=600,
        temperature=0.9,
    )

    text = r.choices[0].message.content.strip()
    return text + "\n\n—\nНапиши свой сон 👉 @whatdreams_bot 🌙"


# =========================
# IMAGE GENERATION
# =========================
def generate_image_url(slot: str) -> str:
    print("[GEN] Генерируем изображение…", flush=True)
    img = client.images.generate(
        model=OPENAI_MODEL_IMAGE,
        prompt="Мистическая иллюстрация сна, луна, символы, без текста",
        size="1024x1024",
    )
    return img.data[0].url


# =========================
# TELEGRAM SEND
# =========================
def send_photo_to_telegram(image_url: str, caption: str) -> None:
    print(f"[TG] Отправка в канал {TELEGRAM_CHANNEL_ID}", flush=True)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": TELEGRAM_CHANNEL_ID, "photo": image_url, "caption": caption}
    r = requests.post(url, data=payload, timeout=60)
    print("[TG RESPONSE]", r.status_code, r.text, flush=True)


def create_and_send_post(slot: str, time_planned: str) -> None:
    print(f"[POST] START slot={slot} plan={time_planned}", flush=True)
    text = generate_post_text(slot)
    image_url = generate_image_url(slot)
    send_photo_to_telegram(image_url, text)
    log_post(slot, text, image_url, time_planned)
    print("[POST] DONE", flush=True)


# =========================
# MANUAL PUBLISH
# =========================
def check_token(token: str):
    if not MANUAL_PUBLISH_TOKEN:
        return "MANUAL_PUBLISH_TOKEN not set", 500
    if token != MANUAL_PUBLISH_TOKEN:
        return "forbidden", 403
    return None


@app.get("/publish-now")
def publish_now():
    token = request.args.get("token", "")
    slot = request.args.get("slot", "day")

    bad = check_token(token)
    if bad:
        return bad

    print(f"[MANUAL] Trigger slot={slot}", flush=True)

    def run():
        try:
            create_and_send_post(slot, f"manual-{datetime.now().strftime('%H:%M')}")
        except Exception as e:
            print("[MANUAL ERROR]", repr(e), flush=True)
            print(traceback.format_exc(), flush=True)

    threading.Thread(target=run, daemon=True).start()
    return f"started {slot}", 200


@app.get("/panel")
def panel():
    token = request.args.get("token", "")
    bad = check_token(token)
    if bad:
        return bad

    return f"""
    <h2>Dream Bot Panel</h2>
    <a href="/publish-now?token={token}&slot=morning">Утро</a><br><br>
    <a href="/publish-now?token={token}&slot=day">День</a><br><br>
    <a href="/publish-now?token={token}&slot=evening">Вечер</a>
    """, 200


# =========================
# SCHEDULER
# =========================
def random_time(start, end):
    return f"{random.randint(start, end-1):02d}:{random.randint(0,59):02d}"


def schedule_daily_posts():
    print("[SCHED] Перенастраиваем расписание", flush=True)
    schedule.clear()
    schedule.every().day.at(random_time(8, 9)).do(create_and_send_post, "morning", "auto")
    schedule.every().day.at(random_time(13, 14)).do(create_and_send_post, "day", "auto")
    schedule.every().day.at(random_time(18, 19)).do(create_and_send_post, "evening", "auto")


def scheduler_loop():
    schedule_daily_posts()
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
