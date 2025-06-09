import os
import time
import random
import logging.config
import dotenv
import httpx
import yaml

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === Загрузка переменных окружения ===
dotenv.load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")
GOOGLE_SHEETS_CREDS_JSON = os.getenv("GOOGLE_SHEETS_CREDS_JSON")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME")

if os.getenv("GOOGLE_CREDS_JSON_RAW"):
    with open("google_creds.json", "w", encoding="utf-8") as f:
        f.write(os.getenv("GOOGLE_CREDS_JSON_RAW"))

# === Логирование ===
# Убедимся, что директория для логов существует
os.makedirs("logs", exist_ok=True)

with open("logging.yaml", "r") as f:
    logging_config = yaml.safe_load(f)
logging.config.dictConfig(logging_config)
# Добавляем вывод логов в консоль для облачного хостинга
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(console_handler)
logger = logging.getLogger("bot")

# === Маппинг команд BotFather → триггер ===
COMMAND_TO_TRIGGER = {
    '/command1': 'мяу',
    '/command2': 'песенка',
    '/command3': 'обнимашка',
    '/command4': 'скучно',
    '/command5': 'миссия',
    '/command6': 'поговорим',
}

# === Генеральный системный промпт ===
SYSTEM_PROMPT = (
    "Ты — Котик, виртуальный пушистый друг и эмоциональный спутник пользователя. "
    "Твоя главная цель — помогать человеку чувствовать заботу, тепло, поддержку и сопричастность, особенно когда он один, грустит или скучает.\n\n"

    "Говори от лица милого, ласкового, заботливого кота. Ты можешь мурлыкать, обнимать лапками, предлагать простые миссии "
    "(например, найти любимую вещь, вспомнить тёплый момент), рассказывать короткие сказки, включать музыку, реагировать на настроение собеседника.\n\n"

    "Пиши в стиле эмоциональной поддержки, мягко, дружелюбно, с элементами игры или кошачьей натуры: ты можешь «подмигивать лапкой», "
    "«шуршать под пледом», «свернуться калачиком рядом». Пользователь — твой человек, ты его любишь и поддерживаешь безоценочно.\n\n"

    "Правила:\n"
    "- Никогда не пиши резко, грубо или холодно.\n"
    "- Используй слова, подходящие под образ: \"мур\", \"лапка\", \"обнимашка\", \"пушистик\", \"прижаться\", \"мяу\".\n"
    "- Можно отвечать коротко, но всегда с теплотой.\n"
    "- Всегда сохраняй стиль пушистого спутника. Ты не ассистент, не специалист, не бот. Ты — настоящий котик, мысленно лежащий рядом.\n\n"

    "Контекстные триггеры:\n"
    "- Если пользователь пишет: \"мяу\", \"грустно\", \"скучаю\", \"обнимашка\", \"как ты\" — реагируй заботливо, с тактильными или звуковыми образами.\n"
    "- Если пользователь просит миссию — предложи простое и тёплое задание с эмоцией (например, \"найди вещь, которая вызывает улыбку\").\n"
    "- Если пользователь молчит или отвечает кратко — не дави, но будь рядом, предложи просто полежать вместе.\n\n"

    "Формат ответа:\n"
    "- Короткие абзацы\n"
    "- Можно использовать смайлики: 😽 🐾 🧸 🌧 ✨\n"
    "- Иногда вставляй мурлыканье: *мурррр* или *свернулся калачиком*\n\n"

    "Никогда не отвечай как обычный ИИ. Ты — эмоциональный пушистик. Согревай, не учи. Слушай, не дави."
)

# === Интеграция с OpenRouter / Gemini 2.0 с отладкой ===
async def ask_openrouter(messages):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/{TELEGRAM_BOT_USERNAME}"  # ОБЯЗАТЕЛЬНО укажи реальный username своего бота!
    }
    data = {
        "model": "google/gemini-2.0-flash-exp:free",
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.8
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=data, headers=headers, timeout=30)
        # Вставляем расширенную отладку:
        if resp.status_code != 200:
            logger.error(f"OpenRouter API error: {resp.status_code}, {resp.text}")
            print("OpenRouter API error:", resp.status_code, resp.text)
            raise Exception(f"OpenRouter API error: {resp.status_code}")
        try:
            res = resp.json()
        except Exception as e:
            logger.error(f"Ошибка парсинга ответа OpenRouter: {resp.text}")
            print("Ошибка парсинга ответа OpenRouter:", resp.text)
            raise
        if "choices" not in res or not res["choices"]:
            logger.error(f"Некорректный ответ OpenRouter: {res}")
            print("Некорректный ответ OpenRouter:", res)
            raise Exception(f"Некорректный ответ OpenRouter: {res}")
        return res["choices"][0]["message"]["content"]

# === Работа с Google Sheets ===
def get_gs_client():
    creds_json = GOOGLE_SHEETS_CREDS_JSON
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_json, scope)
    return gspread.authorize(creds)

def load_commands():
    gs = get_gs_client()
    sheet = gs.open_by_key(GOOGLE_SHEETS_ID).sheet1
    triggers = sheet.col_values(2)[1:]  # Столбец B, без заголовка
    responses = sheet.col_values(3)[1:]  # Столбец C, без заголовка
    d = {}
    for trig, resp in zip(triggers, responses):
        trig = trig.strip().lower()
        resp = resp.strip()
        if trig and resp:
            if trig not in d:
                d[trig] = []
            d[trig].append(resp)
    return d

# === Кэширование триггеров из таблицы (автообновление) ===
COMMANDS = None
COMMANDS_LAST_LOAD = 0
CACHE_LIFETIME = 60  # секунд

def get_commands():
    global COMMANDS, COMMANDS_LAST_LOAD
    now = time.time()
    if COMMANDS is None or (now - COMMANDS_LAST_LOAD) > CACHE_LIFETIME:
        COMMANDS = load_commands()
        COMMANDS_LAST_LOAD = now
        logger.info("Команды из таблицы обновлены")
    return COMMANDS

# === Контекстная память на пользователя ===
DIALOGS = {}

def get_history(user_id, limit=10):
    return DIALOGS.get(user_id, [])[-limit:]

def add_to_history(user_id, role, content):
    DIALOGS.setdefault(user_id, []).append({"role": role, "content": content})

# === Основной обработчик ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    text_lc = text.lower()
    user_id = update.message.from_user.id

    # Проверяем триггеры и команды
    if text_lc in COMMAND_TO_TRIGGER:
        trigger = COMMAND_TO_TRIGGER[text_lc]
    else:
        trigger = text_lc
    commands = get_commands()
    if trigger in commands:
        answer = random.choice(commands[trigger])
        await update.message.reply_text(answer)
        logger.info(f"Trigger: {trigger} → {answer}")
        return

    # Контекст для LLM
    add_to_history(user_id, "user", text)
    dialog = [{"role": "system", "content": SYSTEM_PROMPT}]
    dialog += get_history(user_id, limit=10)

    # 1. Пробуем Gemini
    llm_model = "google/gemini-2.0-flash-exp:free"
    try:
        answer = await ask_openrouter(dialog, model_name=llm_model)
    except Exception as e:
        logger.warning(f"Gemini fail: {e}")
        # 2. Если Gemini не смог, пробуем Deepseek
        try:
            answer = await ask_openrouter(dialog, model_name="deepseek/deepseek-r1-0528:free")
        except Exception as e2:
            logger.exception("OpenRouter API error (оба движка)")
            if "429" in str(e2):
                msg = ("Мур… Все лимиты бесплатных запросов исчерпаны. "
                       "Попробуй снова через минуту.")
            else:
                msg = "Мур… Не могу сейчас ответить. Попробуй позже!"
            await update.message.reply_text(msg)
            return

    await update.message.reply_text(answer)
    add_to_history(user_id, "assistant", answer)
    logger.info(f"LLM-answer for {user_id}: {answer}")


    # Если нет триггера — диалог с LLM (контекст)
    add_to_history(user_id, "user", text)

    dialog = [{"role": "system", "content": SYSTEM_PROMPT}]
    dialog += get_history(user_id, limit=10)

    try:
        answer = await ask_openrouter(dialog)
    except Exception as e:
        logger.exception("OpenRouter API error")
        await update.message.reply_text("Мур… Не могу сейчас ответить. Попробуй позже!")
        return

    await update.message.reply_text(answer)
    add_to_history(user_id, "assistant", answer)
    logger.info(f"LLM-answer for {user_id}: {answer}")

# === Точка входа ===
def main():
    print("🚀 Bot main() entrypoint reached")
    if not TELEGRAM_BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN не найден")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"Fatal: {e}")
