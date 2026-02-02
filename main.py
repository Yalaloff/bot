import os
import time
import json
import random
import threading
import traceback
import base64
import html
import re
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
# TEXT UTILITIES
# =========================
def parse_hook_and_body(text: str) -> Tuple[str, str]:
    """
    Ожидаем формат:
    HOOK: ...
    TEXT: ...
    """
    hook = ""
    body = ""
    lines = [l.rstrip() for l in text.splitlines()]

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.lower().startswith("hook:"):
            hook = s.split(":", 1)[1].strip()
        elif s.lower().startswith("text:"):
            body = s.split(":", 1)[1].strip()
        else:
            if not hook:
                hook = s
            else:
                body = (body + "\n" + s).strip() if body else s

    return hook.strip(), body.strip()


def count_emojis(s: str) -> int:
    """
    Приблизительный подсчёт эмодзи по Unicode диапазонам.
    Достаточно для контроля 3–5.
    """
    emoji_pattern = re.compile(
        "["
        "\U0001F300-\U0001F5FF"
        "\U0001F600-\U0001F64F"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\u2600-\u26FF"
        "\u2700-\u27BF"
        "]+"
    )
    # Каждое совпадение может содержать несколько символов; посчитаем по длине строки совпадений
    matches = emoji_pattern.findall(s)
    return sum(len(m) for m in matches)


def enforce_emoji_range(hook: str, body: str, min_e: int = 3, max_e: int = 5) -> Tuple[str, str]:
    """
    Если эмодзи не 3–5 — просим модель отредактировать ТОЛЬКО TEXT,
    сохранив структуру и смысл. HOOK оставляем без эмодзи.
    """
    total = count_emojis(body)
    if min_e <= total <= max_e:
        return hook, body

    print(f"[FIX] Emoji count={total}, исправляем до {min_e}-{max_e}", flush=True)

    system_msg = (
        "Ты редактор Telegram-постов. Правь только текст, не меняя структуру блоков. "
        "Держи стиль дерзко/современно, без воды."
    )
    user_msg = (
        "Отредактируй только блок TEXT так, чтобы:\n"
        f"- В TEXT было {min_e}-{max_e} эмодзи (сейчас их {total}).\n"
        "- Эмодзи распределены по тексту, не в каждой строке.\n"
        "- Смысл, тон и структура блоков сохраняются.\n"
        "- Не добавляй хэштеги.\n"
        "- Не добавляй эмодзи в HOOK.\n\n"
        f"HOOK: {hook}\n"
        f"TEXT:\n{body}\n\n"
        "Верни строго:\n"
        "HOOK: ...\n"
        "TEXT: ...\n"
    )

    r = client.chat.completions.create(
        model=OPENAI_MODEL_CHAT,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=500,
        temperature=0.4,
    )

    raw = r.choices[0].message.content.strip()
    h2, b2 = parse_hook_and_body(raw)

    # Safety: если модель сломала формат — просто вернём исходное, но слегка поправим вручную
    if not h2:
        h2 = hook
    if not b2:
        b2 = body

    # Повторная проверка; если всё равно не ок — не зацикливаем, принимаем как есть
    total2 = count_emojis(b2)
    if not (min_e <= total2 <= max_e):
        print(f"[FIX] Emoji after fix={total2} (оставляю как есть)", flush=True)

    return h2, b2


def build_caption_html(hook: str, body: str) -> str:
    footer = "—\nНапиши свой сон 👉 @whatdreams_bot 🌙"

    hook_html = f"<b>{html.escape(hook)}</b>"
    body_html = html.escape(body)
    footer_html = html.escape(footer)

    caption = f"{hook_html}\n\n{body_html}\n\n{footer_html}"

    # Безопасный лимит (Telegram caption ~1024). Режем по длине строки (грубо, но стабильно).
    if len(caption) > 1000:
        excess = len(caption) - 1000
        if excess > 0 and len(body_html) > excess + 10:
            body_html = body_html[: max(0, len(body_html) - excess - 10)].rstrip() + "…"
            caption = f"{hook_html}\n\n{body_html}\n\n{footer_html}"
        caption = caption[:1000]

    return caption


# =========================
# SLOT STRUCTURES (утро/день/вечер)
# =========================
def slot_rules(slot: str, topic: str) -> str:
    """
    Отличаем посты по структуре:
    - утро: "Намерение дня" + микро-практика
    - день: "Символ дня" + трактовка + вывод
    - вечер: "Ритуал на ночь" + аффирмация
    """
    if slot == "morning":
        return (
            f"Тема: {topic}\n"
            "Тон: дерзко/современно, но тепло. Короткие строки. Без воды.\n"
            "Структура TEXT:\n"
            "1) 4–6 коротких строк (до 90 символов каждая)\n"
            "2) Блок: «Намерение дня: …» (1 строка)\n"
            "3) Блок: «Практика на 30 секунд: …» (1 строка)\n"
        )
    if slot == "day":
        return (
            f"Тема: {topic}\n"
            "Тон: ясно, практично, без мистики-страшилок. Короткие строки.\n"
            "Структура TEXT:\n"
            "1) 4–6 коротких строк\n"
            "2) Блок: «Символ дня: <одно слово/образ> — <трактовка в 1 строку>»\n"
            "3) Блок: «Вывод: …» (1 строка)\n"
            "4) Блок: «Практика на 30 секунд: …» (1 строка)\n"
        )
    # evening
    return (
        f"Тема: {topic}\n"
        "Тон: спокойный, мягкий, как мини-ритуал. Короткие строки.\n"
        "Структура TEXT:\n"
        "1) 4–6 коротких строк\n"
        "2) Блок: «Ритуал на ночь: …» (1 строка)\n"
        "3) Блок: «Аффирмация: …» (1 строка)\n"
        "4) Блок: «Практика на 30 секунд: …» (1 строка)\n"
    )


# =========================
# TEXT GENERATION (Variant B)
# =========================
def generate_post_parts(slot: str) -> Tuple[str, str]:
    print(f"[GEN] Генерируем текст для слота: {slot}", flush=True)

    topic = get_weekday_topic()

    system_msg = (
        "Ты автор Telegram-канала о снах. Пиши дерзко и современно (как автор Reels), "
        "короткими фразами, лёгкая ирония допустима. "
        "Но без грубости. Без воды. Без хэштегов. "
        "Не делай предсказаний. Не пугай."
    )

    user_msg = f"""
{slot_rules(slot, topic)}

Ограничения:
- HOOK: 1 строка, 7–12 слов, вопрос/интрига, БЕЗ эмодзи.
- TEXT: общий объём 450–650 символов, 4–6 коротких строк + обязательные блоки из структуры.
- В TEXT: 3–5 эмодзи, распределены по тексту (не в каждой строке).
- Не используй слова: "подписывайся", "лайк", "репост".
- Не начинай текст с "сегодня поговорим".

Верни строго:
HOOK: ...
TEXT: ...
""".strip()

    r = client.chat.completions.create(
        model=OPENAI_MODEL_CHAT,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=900,
        temperature=0.9,
    )

    raw = r.choices[0].message.content.strip()
    hook, body = parse_hook_and_body(raw)

    # Safety fallback
    if not hook:
        hook = "Твой сон сегодня намекает… но ты это заметил?"
    if not body:
        body = raw

    # enforce emoji 3–5
    hook, body = enforce_emoji_range(hook, body, min_e=3, max_e=5)

    return hook, body


# =========================
# IMAGE PROMPT FROM TEXT
# =========================
def extract_image_prompt(hook: str, body: str) -> str:
    """
    Делаем короткий визуальный промпт под конкретный пост.
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
                    "Один ряд, конкретные объекты/сцена/свет/настроение. "
                    "Без кавычек, без хэштегов, без упоминания Telegram/ботов. "
                    "Никакого текста на изображении."
                )
            },
            {
                "role": "user",
                "content": (
                    "Сделай визуальный prompt (1 предложение) для мягкой сюрреалистичной иллюстрации сна.\n\n"
                    f"Текст:\n{text}"
                )
            },
        ],
        max_tokens=90,
        temperature=0.6,
    )
    prompt = r.choices[0].message.content.strip()
    return prompt[:240]


# =========================
# IMAGE GENERATION (OpenAI GPT-Image-1)
# =========================
def generate_image_bytes(image_prompt: str) -> bytes:
    print("[GEN] Генерируем изображение (gpt-image-1)...", flush=True)

    final_prompt = (
        f"Dreamlike surreal illustration: {image_prompt}. "
        "soft light, cinematic, atmospheric, modern style, high quality, no text, no logo."
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

    # анти-дубликаты: если любой пост был < N минут назад — пропускаем
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
        <div class="muted">Если был пост менее 7 минут назад — отправка будет пропущена (анти-дубликаты).</div>
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
