import os
import time
import json
import random
import threading
import traceback
import base64
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
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print("Ошибка чтения логов:", e, flush=True)
        return []


def save_logs(logs: List[Dict[str, Any]]) -> None:
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Ошибка записи логов:", e, flush=True)


def log_post(slot: str, time_planned: str, time_sent: str, tg_status: int, tg_body: str) -> None:
    logs = load_logs()
    logs.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "slot": slot,
        "time_planned": time_planned,
        "time_sent": time_sent,
        "tg_status": tg_status,
        "tg_body": tg_body[:3000],
        "manual": str(time_planned).startswith("manual"),
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
            {
                "role": "system",
                "content": "Ты эзотерический автор Telegram-канала о снах. Пиши мягко, красиво, без страшилок."
            },
            {
                "role": "user",
                "content": f"{get_weekday_topic()}. Объём 500–700 символов. 1–2 эмодзи."
            },
        ],
        max_tokens=700,
        temperature=0.9,
    )

    text = r.choices[0].message.content.strip()

    footer = "\n\n—\nНапиши свой сон 👉 @whatdreams_bot 🌙"
    full = text + footer

    # лимит caption в Telegram ≈ 1024
    if len(full) > 1024:
        full = full[:1020] + "…"

    return full


# =========================
# IMAGE GENERATION (OpenAI GPT-Image-1)
# =========================
def generate_image_bytes(slot: str) -> bytes:
    """
    Для gpt-image-1:
    b64_json возвращается по умолчанию, response_format НЕ используем.
    """
    print("[GEN] Генерируем изображение (gpt-image-1)...", flush=True)

    img = client.images.generate(
        model=OPENAI_MODEL_IMAGE,
        prompt="Мистическая иллюстрация сна, луна, символы, мягкий свет, без текста.",
        size="1024x1024",
    )

    data0 = img.data[0]

    # основной путь — base64
    b64 = getattr(data0, "b64_json", None)
    if b64:
        image_bytes = base64.b64decode(b64)
        print(f"[GEN] Картинка получена из b64, bytes={len(image_bytes)}", flush=True)
        return image_bytes

    # fallback — если вдруг вернулся url
    url = getattr(data0, "url", None)
    if url:
        print("[GEN] b64 отсутствует, fallback на url", flush=True)
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        return r.content

    raise ValueError("OpenAI image response не содержит ни b64_json, ни url")


# =========================
# TELEGRAM SEND
# =========================
def send_photo_to_telegram(image_bytes: bytes, caption: str) -> requests.Response:
    print(f"[TG] Отправка в канал {TELEGRAM_CHANNEL_ID}", flush=True)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {"photo": ("image.png", image_bytes)}
    data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption}

    r = requests.post(url, data=data, files=files, timeout=90)
    print("[TG RESPONSE]", r.status_code, r.text, flush=True)
    return r


def create_and_send_post(slot: str, time_planned: str) -> None:
    print(f"[POST] START slot={slot} plan={time_planned}", flush=True)

    try:
        text = generate_post_text(slot)
        image_bytes = generate_image_bytes(slot)

        resp = send_photo_to_telegram(image_bytes, text)

        log_post(
            slot=slot,
            time_planned=time_planned,
            time_sent=datetime.now().strftime("%H:%M"),
            tg_status=resp.status_code,
            tg_body=resp.text,
        )

        print("[POST] DONE", flush=True)

    except Exception as e:
        print("[POST ERROR]", repr(e), flush=True)
        print(traceback.format_exc(), flush=True)

        log_post(
            slot=slot,
            time_planned=time_planned,
            time_sent=datetime.now().strftime("%H:%M"),
            tg_status=0,
            tg_body=f"ERROR: {repr(e)}",
        )


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
    slot = request.args.get("slot", "day").strip().lower()

    bad = check_token(token)
    if bad:
        return bad

    if slot not in {"morning", "day", "evening"}:
        return "bad slot (use morning|day|evening)", 400

    print(f"[MANUAL] Trigger slot={slot}", flush=True)

    threading.Thread(
        target=create_and_send_post,
        args=(slot, f"manual-{datetime.now().strftime('%H:%M')}"),
        daemon=True
    ).start()

    return f"started {slot}", 200


@app.get("/panel")
def panel():
    token = request.args.get("token", "")
    bad = check_token(token)
    if bad:
        return bad

    return f"""
    <html>
      <head><meta charset="utf-8"><title>Dream Bot Panel</title></head>
      <body style="font-family:Arial;padding:20px;">
        <h2>Dream Bot Panel</h2>
        <p><a href="/publish-now?token={token}&slot=morning">Утро</a></p>
        <p><a href="/publish-now?token={token}&slot=day">День</a></p>
        <p><a href="/publish-now?token={token}&slot=evening">Вечер</a></p>
      </body>
    </html>
    """, 200, {"Content-Type": "text/html; charset=utf-8"}


# =========================
# SCHEDULER
# =========================
def random_time(start_hour: int, end_hour_exclusive: int) -> str:
    return f"{random.randint(start_hour, end_hour_exclusive - 1):02d}:{random.randint(0, 59):02d}"


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
