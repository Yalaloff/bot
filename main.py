import os
import time
import json
import random
from datetime import datetime
from typing import List, Dict, Any

import schedule
import requests
from dotenv import load_dotenv
from openai import OpenAI

# Загружаем переменные окружения (локально). На Render можно задавать их через панель.
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL_CHAT = os.getenv("OPENAI_MODEL_CHAT", "gpt-4o-mini")
OPENAI_MODEL_IMAGE = os.getenv("OPENAI_MODEL_IMAGE", "gpt-image-1")

LOG_FILE = "posts_log.json"

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID or not OPENAI_API_KEY:
    raise ValueError("Не заполнены TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID или OPENAI_API_KEY в окружении.")

client = OpenAI(api_key=OPENAI_API_KEY)


# --------- ЛОГИ ---------
def load_logs() -> List[Dict[str, Any]]:
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
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
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "slot": slot,
        "text": text,
        "image_url": image_url,
        "time_planned": time_planned,
        "time_sent": datetime.now().strftime("%H:%M"),
    }
    logs.append(entry)
    save_logs(logs)


def get_last_texts_for_slot(slot: str, limit: int = 5) -> List[str]:
    logs = load_logs()
    slot_logs = [l for l in logs if l.get("slot") == slot]
    texts = [l.get("text", "") for l in slot_logs[-limit:]]
    return [t for t in texts if t]


# --------- ТЕМЫ ПО ДНЯМ НЕДЕЛИ ---------
def get_weekday_topic() -> str:
    """
    Возвращает текстовое описание темы дня на русском.
    Monday = 0, Sunday = 6.
    """
    weekday = datetime.now().weekday()
    topics = {
        0: "Сегодня понедельник. Тема дня — эмоции во снах: чувства, которые прячутся за образами.",
        1: "Сегодня вторник. Тема дня — природные символы во снах: вода, лес, животные и то, как они говорят с тобой.",
        2: "Сегодня среда. Тема дня — архетипы: дом, коридоры, комнаты, двери и их сокровенный смысл.",
        3: "Сегодня четверг. Тема дня — знаки и предчувствия, сны-подсказки и внутренние ориентиры.",
        4: "Сегодня пятница. Тема дня — повторяющиеся и навязчивые сны, циклы, которые просят быть замеченными.",
        5: "Сегодня суббота. Тема дня — подсознание и энергия сна: как ночные образы заряжают или забирают силу.",
        6: "Сегодня воскресенье. Тема дня — спокойствие, восстановление и медитативные образы перед новой неделей.",
    }
    return topics.get(weekday, "")


# --------- ГЕНЕРАЦИЯ ТЕКСТА (СТИЛЬ В — ЭЗОТЕРИКА) ---------
def generate_post_text(slot: str) -> str:
    """
    slot: 'morning' | 'day' | 'evening'
    """
    slot_prompt = {
        "morning": "Утро. Напиши мягкий, вдохновляющий эзотерический пост о снах, как о посланиях от тонкого мира. "
                   "Пусть он помогает человеку мягко войти в день, вспоминая ночные знаки.",
        "day": "День. Напиши более объясняющий, но всё ещё мистический пост о символах во снах. "
               "Раскрой, как сны подают знаки через образы, энергии и повторяющиеся символы.",
        "evening": "Вечер. Напиши спокойный, почти ритуальный пост о переходе в ночное пространство снов, "
                   "о доверии подсознанию и тонким подсказкам Вселенной перед сном.",
    }.get(slot, "Напиши эзотерический пост о снах как посланиях души и Вселенной.")

    weekday_topic = get_weekday_topic()

    system_message = (
        "Ты — автор эзотерического Telegram-канала о толковании снов. "
        "Ты говоришь языком символов, энергии и знаков, но остаёшься доброжелательным и понятным. "
        "Твоя задача — не пугать, а мягко направлять, помогать читателю чувствовать поддержку. "
        "Ты опираешься на символизм, архетипы, внутренние состояния, говоришь о Вселенной, подсказках, потоках."
    )

    last_texts = get_last_texts_for_slot(slot, limit=3)
    history_block = ""
    if last_texts:
        joined = "\n---\n".join(last_texts)
        history_block = (
            "\n\nВот примеры последних постов по этой теме. Не повторяй дословно формулировки и идеи, "
            "добавь новое звучание и свежий взгляд:\n"
            f"{joined}"
        )

    user_message = (
        slot_prompt
        + " Объём 500–900 символов. Пиши как мягкий эзотерический проводник, но без страшилок и жёстких предсказаний. "
          "Избегай чрезмерно мрачных сцен. Не используй хэштеги. Не начинай строки с эмодзи, "
          "но можешь использовать 1–3 эмодзи внутри текста по смыслу. "
          "Можно упоминать энергию, вибрации, Вселенную, подсказки, но не нужно сложных ритуалов."
        + "\n\n" + weekday_topic
        + history_block
    )

    response = client.chat.completions.create(
        model=OPENAI_MODEL_CHAT,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        max_tokens=700,
        temperature=0.95,
    )

    text = response.choices[0].message.content.strip()

    footer = (
        "\n\n—\n"
        "Хочешь получить личное послание из своих снов? "
        "Отправь свой сон нашему боту @whatdreams_bot — он аккуратно расшифрует его для тебя 🌙"
    )

    full_text = text + footer
    if len(full_text) > 1024:
        allowed_main = 1024 - len(footer) - 3
        text = text[:allowed_main].rstrip() + "..."
        full_text = text + footer

    return full_text


# --------- ГЕНЕРАЦИЯ КАРТИНКИ ---------
def generate_image_url(slot: str) -> str:
    weekday = datetime.now().weekday()
    weekday_style = {
        0: "эмоции, светящиеся контуры сердца, мягкие волны энергии вокруг фигуры во сне",
        1: "вода, лес, силуэты животных, немного мистический туман",
        2: "дом, коридоры, двери, многослойное пространство, архетипичные формы",
        3: "ночное небо, знаки, созвездия, символические дорожные указатели",
        4: "повторяющиеся спирали, циклы, лестницы, символы повторения",
        5: "энергетические потоки, аура, человек в поле света или звёзд",
        6: "медитативный ландшафт, луна, спокойная вода, ощущение восстановления",
    }.get(weekday, "абстрактный мистический пейзаж, связанный со снами и подсознанием")

    base_style = {
        "morning": "мягкий рассвет, тёплые тона, ощущение пробуждения после магического сна",
        "day": "чуть более ясные контуры, символы снов в полуреалистичном стиле",
        "evening": "глубокие тёмные оттенки, звёздное небо, ощущение погружения в мир снов",
    }.get(slot, "мистическая атмосфера, связанная со снами")

    prompt = (
        "Иллюстрация для эзотерического Telegram-канала о толковании снов: "
        f"{weekday_style}, {base_style}. "
        "Без текста, без надписей, без логотипов. Современный, минималистичный, атмосферный стиль."
    )

    img = client.images.generate(
        model=OPENAI_MODEL_IMAGE,
        prompt=prompt,
        n=1,
        size="1024x1024",
    )

    image_url = img.data[0].url
    return image_url


# --------- ОТПРАВКА В TELEGRAM ---------
def send_photo_to_telegram(image_url: str, caption: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "photo": image_url,
        "caption": caption,
    }

    try:
        response = requests.post(url, data=payload, timeout=60)
        if not response.ok:
            print("Ошибка отправки в Telegram:", response.status_code, response.text)
        else:
            print("Пост успешно отправлен в канал.")
    except Exception as e:
        print("Исключение при отправке в Telegram:", e)


# --------- РАНДОМИЗАЦИЯ ВРЕМЕНИ ---------
def random_time_in_range(start_hour: int, end_hour_exclusive: int) -> str:
    hour = random.randint(start_hour, end_hour_exclusive - 1)
    minute = random.randint(0, 59)
    return f"{hour:02d}:{minute:02d}"


def schedule_daily_posts() -> None:
    """
    Настраивает расписание на текущий день с рандомным временем
    для утреннего, дневного и вечернего поста.
    """
    print("Перенастраиваем расписание на новый день...")

    schedule.clear("morning")
    schedule.clear("day")
    schedule.clear("evening")

    t_morning = random_time_in_range(8, 9)   # 08:00–09:00
    t_day = random_time_in_range(12, 14)     # 12:00–14:00
    t_evening = random_time_in_range(18, 19) # 18:00–19:00

    def job_morning():
        create_and_send_post("morning", t_morning)

    def job_day():
        create_and_send_post("day", t_day)

    def job_evening():
        create_and_send_post("evening", t_evening)

    schedule.every().day.at(t_morning).do(job_morning).tag("morning")
    schedule.every().day.at(t_day).do(job_day).tag("day")
    schedule.every().day.at(t_evening).do(job_evening).tag("evening")

    print("Текущее расписание:")
    print(f" - morning в {t_morning}")
    print(f" - day в {t_day}")
    print(f" - evening в {t_evening}")


# --------- ОСНОВНАЯ ЛОГИКА ПОСТА ---------
def create_and_send_post(slot: str, time_planned: str) -> None:
    print(f"--- Генерируем пост для слота: {slot} (запланировано на {time_planned}) ---")
    try:
        text = generate_post_text(slot)
        print("Текст сгенерирован.")
        image_url = generate_image_url(slot)
        print("Картинка сгенерирована.")
        send_photo_to_telegram(image_url, text)
        log_post(slot, text, image_url, time_planned)
    except Exception as e:
        print("Ошибка при создании или отправке поста:", e)


def main():
    # Первичная настройка расписания
    schedule_daily_posts()

    # Каждый день в 00:05 обновляем расписание
    schedule.every().day.at("00:05").do(schedule_daily_posts).tag("rescheduler")

    print("Автопостинг для эзотерического канала о снах запущен. Ожидаем расписание...")

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
