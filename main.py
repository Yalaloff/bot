import os
import time
import json
import random
import threading
import traceback
import base64
import html
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

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
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # e.g. @my_dream4you
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


def log_post(slot: str, time_planned: str, tg_status: int, tg_body: str) -> None:
    logs = load_logs()
    logs.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "slot": slot,
        "time_planned": time_planned,
        "tg_status": tg_status,
        "tg_body": (tg_body or "")[:3000],
        "manual": str(time_planned).startswith("manual"),
    })
    save_logs(logs)


def last_post_time() -> Optional[datetime]:
    logs = load_logs()
    if not logs:
        return None
    try:
        return datetime.fromisoformat(logs[-1]["timestamp"])
    except Exception:
        return None


def was_recent_post_any(minutes: int = 7) -> bool:
    """
    Защита от дублей: если ЛЮБОЙ пост был менее N минут назад — пропускаем.
    """
    t = last_post_time()
    if not t:
        return False
    return (datetime.now() - t) < timedelta(minutes=minutes)


# =========================
# TOPIC BY WEEKDAY
# =========================
def get_weekday_topic() -> str:
    topics = [
        "эмоции во снах и скрытые чувства",
        "природные символы: вода, лес, животные",
        "архетипы: дом, коридоры, двери",
        "знаки и предчувствия во снах",
        "повторяющиеся сны и их смысл",
        "энергия сна и подсознания",
        "восстановление и медитативные образы",
    ]
    return topics[datetime.now().weekday()]


# =========================
# STYLE BY SLOT (утро/день/вечер)
# =========================
def slot_style(slot: str) -> str:
    if slot == "morning":
        return (
            "Стиль: утренний, мягкий и вдохновляющий. "
            "Больше света, надежды, лёгкая магия дня. "
            "Без страховок и мрачности."
        )
    if slot == "day":
        return (
            "Стиль: дневной, чуть более практичный. "
            "Символы, трактовки, подсказки подсознания. "
            "Без категоричных предсказаний."
        )
    return (
        "Стиль: вечерний, спокойный, как мини-ритуал перед сном. "
        "Тишина, благодарность, намерение, забота о себе."
    )


# =========================
# TEXT GENERATION: HOOK + BODY + 3-5 EMOJI
# =========================
def parse_hook_and_body(text: str) -> Tuple[str, str]:
    """
    Ожидаем формат:
    HOOK: ...
    TEXT: ...
    """
    hook = ""
    body = ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        if line.lower().startswith("hook:"):
            hook = line.split(":", 1)[1].strip()
        elif line.lower().startswith("text:"):
            body = line.split(":", 1)[1].strip()
        else:
            # если модель не соблюла формат — аккуратно собираем
            if not hook:
                hook = line
            else:
                body = (body + "\n" + line).strip() if body else line
    return hook.strip(), body.strip()


def generate_post_parts(slot: str) -> Tuple[str, str]:
    print(f"[GEN] Генерируем текст для слота: {slot}", flush=True)

    topic = get_weekday_topic()
    style = slot_style(slot)

    system_msg = (
        "Ты автор эзотерического Telegram-канала о снах. "
        "Пишешь красиво, мягко, без страшилок и без жёстких предсказаний. "
        "Никаких хэштегов."
    )

    user_msg = (
        f"Тема: {topic}\n"
        f"{style}\n\n"
        "Сделай пост 500–750 символов.\n"
        "В первой строке: крючок (вопрос/интрига), короткий.\n"
        "В тексте: 3–5 уместных эмодзи, равномерно (не в каждой строке).\n"
        "Верни строго в формате:\n"
        "HOOK: ...\n"
        "TEXT: ...\n"
        "Без лишних строк и без заголовков кроме HOOK/TEXT."
    )

    r = client.chat.completions.create(
        model=OPENAI_MODEL_CHAT,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=800,
        temperature=0.9,
    )

    raw = r.choices[0].message.content.strip()
    hook, body = parse_hook_and_body(raw)

    # fallback safety
    if not hook:
        hook = "Что твой сон пытается сказать тебе сегодня? 🌙"
    if not body:
        body = raw

    return hook, body


def build_caption_html(hook: str, body: str) -> str:
    """
    Telegram HTML parse_mode: экранируем всё и делаем hook жирным.
    """
    footer = "—\nНапиши свой сон 👉 @whatdreams_bot 🌙"

    hook_html = f"<b>{html.escape(hook)}</b>"
    body_html = html.escape(body)
    footer_html = html.escape(footer)

    caption = f"{hook_html}\n\n{body_html}\n\n{footer_html}"

    # лимит caption примерно 1024 символа (безопасно режем)
    # считаем по plain length грубо — Telegram считает иначе, но этого достаточно
    if len(caption) > 1000:
        # аккуратно урежем body
        excess = len(caption) - 1000
        if excess > 0 and len(body_html) > excess + 10:
            body_html_cut = body_html[: max(0, len(body_html) - excess - 10)].rstrip() + "…"
            caption = f"{hook_html}\n\n{body_html_cut}\n\n{footer_html}"
        # если всё равно больше — финальный срез
        caption = caption[:1000]

    return caption


# =========================
# IMAGE PROMPT FROM TEXT
# =========================
def extract_image_prompt(hook: str, body: str) -> str:
    """
    Делаем короткий визуальный промпт (1 строка) под конкретный пост.
    """
    print("[GEN] Делаем визуальный промпт из текста…", flush=True)

    text = f"{hook}\n{body}"

    r = client.chat.completions.create(
        model=OPENAI_MODEL_CHAT,
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты превращаешь текст поста о снах в краткий визуальный prompt для иллюстрации. "
                    "Один ряд, без кавычек, без хэштегов, без упоминания Telegram/ботов."
                )
            },
            {
                "role": "user",
                "content": (
                    "Сделай визуальный prompt (1 предложение) для мистической иллюстрации сна "
                    "в мягком сюрреализме. Без текста на изображении.\n\n"
                    f"Текст:\n{text}"
                )
            },
        ],
        max_tokens=80,
        temperature=0.6,
    )
    prompt = r.choices[0].message.content.strip()
    # safety: коротко
    return prompt[:220]


# =========================
# IMAGE GENERATION (OpenAI GPT-Image-1)
# =========================
def generate_image_bytes(image_prompt: str) -> bytes:
    """
    Для gpt-image-1: b64_json обычно приходит по умолчанию (на openai==2.14.0).
    """
    print("[GEN] Генерируем изображение (gpt-image-1)...", flush=True)

    final_prompt = (
        f"Мистическая иллюстрация сна: {image_prompt}. "
        "Мягкий свет, атмосферно, красиво, современный стиль, без текста, без логотипов."
    )

    img = client.images.generate(
        model=OPENAI_MODEL_IMAGE,
        prompt=final_prompt,
        size="1024x1024",
    )

    data0 = img.data[0]
    b64 = getattr(data0, "b64_json", None)
    if b64:
        image_bytes = base64.b64decode(b64)
        print(f"[GEN] Картинка получена из b64, bytes={len(image_bytes)}", flush=True)
        return image_bytes

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
def send_photo_to_telegram(image_bytes: bytes, caption_html: str) -> requests.Response:
    print(f"[TG] Отправка в канал {TELEGRAM_CHANNEL_ID}", flush=True)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {"photo": ("image.png", image_bytes)}
    data = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "caption": caption_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    r = requests.post(url, data=data, files=files, timeout=120)
    print("[TG RESPONSE]", r.status_code, r.text, flush=True)
    return r


# =========================
# POST PIPELINE
# =========================
def create_and_send_post(slot: str, time_planned: str) -> None:
    print(f"[POST] START slot={slot} plan={time_planned}", flush=True)

    # анти-дубликаты: если недавно уже был пост — не шлём
    if was_recent_post_any(minutes=7):
        print("[SKIP] Недавний пост был < 7 минут назад — пропускаем", flush=True)
        return

    try:
        hook, body = generate_post_parts(slot)
        caption_html = build_caption_html(hook, body)

        image_prompt = extract_image_prompt(hook, body)
        image_bytes = generate_image_bytes(image_prompt)

        resp = send_photo_to_telegram(image_bytes, caption_html)

        log_post(
            slot=slot,
            time_planned=time_planned,
            tg_status=resp.status_code,
            tg_body=resp.text,
        )

        print("[POST] DONE", flush=True)

    except Exception as e:
        print("[POST ERROR]", repr(e), flush=True)
        print(traceback.format_exc(), flush=True)
        log_post(slot=slot, time_planned=time_planned, tg_status=0, tg_body=f"ERROR: {repr(e)}")


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
      <head>
        <meta charset="utf-8">
        <title>Dream Bot Panel</title>
        <style>
          body {{ font-family: Arial, sans-serif; padding: 20px; }}
          a.btn {{
            display: inline-block; padding: 12px 16px; margin: 6px 0;
            background: #111827; color: white; text-decoration: none; border-radius: 10px;
          }}
          .muted {{ color: #6b7280; margin-top: 12px; }}
        </style>
      </head>
      <body>
        <h2>Dream Bot Panel</h2>
        <a class="btn" href="/publish-now?token={token}&slot=morning">Утро</a><br/>
        <a class="btn" href="/publish-now?token={token}&slot=day">День</a><br/>
        <a class="btn" href="/publish-now?token={token}&slot=evening">Вечер</a><br/>
        <div class="muted">Если был пост менее 7 минут назад — система пропустит отправку (анти-дубликаты).</div>
      </body>
    </html>
    """, 200, {"Content-Type": "text/html; charset=utf-8"}


# =========================
# SCHEDULER
# =========================
def random_time(start_hour: int, end_hour_exclusive: int) -> str:
    # (13,14) => 13:00–13:59
    return f"{random.randint(start_hour, end_hour_exclusive - 1):02d}:{random.randint(0, 59):02d}"


def schedule_daily_posts():
    print("[SCHED] Перенастраиваем расписание", flush=True)
    schedule.clear()

    t_m = random_time(8, 9)
    t_d = random_time(13, 14)
    t_e = random_time(18, 19)

    schedule.every().day.at(t_m).do(create_and_send_post, "morning", "auto")
    schedule.every().day.at(t_d).do(create_and_send_post, "day", "auto")
    schedule.every().day.at(t_e).do(create_and_send_post, "evening", "auto")

    print(f"[SCHED] today: morning={t_m} day={t_d} evening={t_e}", flush=True)


def scheduler_loop():
    schedule_daily_posts()
    # ежедневно обновляем расписание
    schedule.every().day.at("00:05").do(schedule_daily_posts)
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
