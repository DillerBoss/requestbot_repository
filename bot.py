import logging
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import sqlite3
import datetime
import asyncio
import os

#----Создаем если нет---
os.makedirs("data", exist_ok=True)

# === Конфиг ===
from config import TOKEN, ADMIN_ID

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# === Список городов для автоподсказки ===
AVAILABLE_CITIES = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург",
    "Казань", "Нижний Новгород", "Челябинск", "Омск",
    "Самара", "Ростов-на-Дону", "Уфа", "Красноярск"
]

# === Инициализация БД ===
conn = sqlite3.connect("data/applications.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    city TEXT,
    topic TEXT,
    description TEXT,
    status TEXT DEFAULT 'Не решена',
    user_id INTEGER
)
""")
conn.commit()

# === FSM состояния ===
class Form(StatesGroup):
    city = State()
    topic = State()
    description = State()

class AdminReply(StatesGroup):
    waiting_for_reply = State()

class ProblemReason(StatesGroup):
    waiting_for_reason = State()

class StatsForm(StatesGroup):
    start_date = State()
    end_date = State()
    city = State()
    topic = State()


# === Роутеры ===
router = Router()


# ====== Пользовательская часть ======

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer("Введите город:")
    await state.set_state(Form.city)


@router.message(Form.city)
async def process_city(message: Message, state: FSMContext):
    text = message.text.strip()
    matches = [c for c in AVAILABLE_CITIES if c.lower().startswith(text.lower())]

    if len(matches) == 1 and matches[0].lower() == text.lower():
        await state.update_data(city=matches[0])
        await message.answer(f"Город выбран: {matches[0]}\nВведите тему:")
        await state.set_state(Form.topic)
    elif matches:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[msg] for msg in matches],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer("Выберите город из списка или введите заново:", reply_markup=keyboard)
    else:
        await message.answer("Город не найден, попробуйте снова.")


@router.message(Form.topic)
async def process_topic(message: Message, state: FSMContext):
    await state.update_data(topic=message.text.strip())
    await message.answer("Опишите заявку:")
    await state.set_state(Form.description)


@router.message(Form.description)
async def process_description(message: Message, state: FSMContext):
    user_data = await state.get_data()
    city = user_data["city"]
    topic = user_data["topic"]
    description = message.text.strip()
    date = datetime.datetime.now().strftime("%d.%m.%Y")
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    cursor.execute(
        "INSERT INTO applications (date, city, topic, description, user_id) VALUES (?, ?, ?, ?, ?)",
        (date, city, topic, description, message.from_user.id)
    )
    conn.commit()

    app_id = cursor.lastrowid  # Начинаем с 0

    await message.answer(
        f"Заявка №{app_id} принята. Статус: Не решена.",
        reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True)
    )
    await state.clear()
    try:
        await message.bot.send_message(
            ADMIN_ID,
            f"🆕 <b>Новая заявка №{app_id}</b>\n"
            f"👤 От: {first_name} (@{username} | ID: {user_id})\n"
            f"🏙 Город: {city}\n"
            f"📌 Тема: {topic}\n"
            f"📝 Описание: {description}\n"
            f"🕒 Дата: {date}\n"
            f"📊 Статус: <b>Не решена</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление админу: {e}")


# ====== Админская часть ======

@router.message(Command("admin"))
async def admin_list(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT id, date, city, topic, description, status FROM applications ORDER BY id")
    apps = cursor.fetchall()

    if not apps:
        await message.answer("Заявок пока нет.")
        return

    text = "📋 Список заявок:\n\n"
    for app in apps:
        text += (f"#{app[0]} [{app[5]}]\nДата: {app[1]}\nГород: {app[2]}\n"
                 f"Тема: {app[3]}\nОписание: {app[4]}\n\n")
    text += "Чтобы ответить, используйте:\n/reply <id>"
    await message.answer(text)


@router.message(Command("reply"))
async def admin_reply_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Используйте команду так: /reply <номер заявки>")
        return

    app_id = int(parts[1])
    cursor.execute("SELECT user_id FROM applications WHERE id=?", (app_id,))
    row = cursor.fetchone()
    if not row:
        await message.answer("Заявка с таким номером не найдена.")
        return

    await state.update_data(app_id=app_id)
    await message.answer(f"Введите ответ пользователю по заявке #{app_id}:")
    await state.set_state(AdminReply.waiting_for_reply)


@router.message(AdminReply.waiting_for_reply)
async def admin_send_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    app_id = data["app_id"]

    cursor.execute("SELECT user_id FROM applications WHERE id=?", (app_id,))
    user_row = cursor.fetchone()
    if not user_row:
        await message.answer("Пользователь для этой заявки не найден.")
        await state.clear()
        return

    user_id = user_row[0]
    await message.bot.send_message(user_id, f"Ответ администратора по заявке #{app_id}:\n\n{message.text}")
    await message.answer("Ответ отправлен пользователю. Через 10 минут он получит вопрос о решении заявки.")
    await state.clear()

    asyncio.create_task(ask_resolution_later(message.bot, user_id, app_id))


async def ask_resolution_later(bot: Bot, user_id: int, app_id: int):
    await asyncio.sleep(60)  # 1 минут
    cursor.execute("SELECT status FROM applications WHERE id=?", (app_id,))
    status_row = cursor.fetchone()
    if not status_row or status_row[0] == "Решена":
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да", callback_data=f"resolve_yes:{app_id}"),
         InlineKeyboardButton(text="Нет", callback_data=f"resolve_no:{app_id}")]
    ])
    await bot.send_message(user_id, f"Заявка #{app_id} решена?", reply_markup=keyboard)


@router.callback_query(F.data.startswith("resolve_yes"))
async def callback_resolve_yes(callback: CallbackQuery):
    app_id = int(callback.data.split(":")[1])
    cursor.execute("UPDATE applications SET status='Решена' WHERE id=?", (app_id,))
    conn.commit()
    await callback.message.edit_text(f"Спасибо, заявка #{app_id} отмечена как решённая.")
    await callback.answer()


@router.callback_query(F.data.startswith("resolve_no"))
async def callback_resolve_no(callback: CallbackQuery, state: FSMContext):
    app_id = int(callback.data.split(":")[1])
    await state.update_data(app_id=app_id)
    await callback.message.edit_text("Что не так? Опишите проблему:")
    await state.set_state(ProblemReason.waiting_for_reason)
    await callback.answer()


@router.message(ProblemReason.waiting_for_reason)
async def process_problem_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    app_id = data["app_id"]

    await message.bot.send_message(
        ADMIN_ID,
        f"Пользователь по заявке #{app_id} пишет:\n\n{message.text}\n\n"
        f"Ответьте /reply {app_id}"
    )

    cursor.execute("UPDATE applications SET status='Не решена' WHERE id=?", (app_id,))
    conn.commit()

    await message.answer("Спасибо, передал администратору. Он свяжется с вами, и я снова спрошу через 10 минут.")
    await state.clear()


# ====== Статистика ======

@router.message(Command("stats"))
async def stats_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Введите начальную дату в формате ДД.ММ.ГГГГ или '-' для пропуска:")
    await state.set_state(StatsForm.start_date)


@router.message(StatsForm.start_date)
async def stats_get_start_date(message: Message, state: FSMContext):
    await state.update_data(start_date=message.text.strip())
    await message.answer("Введите конечную дату в формате ДД.ММ.ГГГГ или '-' для пропуска:")
    await state.set_state(StatsForm.end_date)


@router.message(StatsForm.end_date)
async def stats_get_end_date(message: Message, state: FSMContext):
    await state.update_data(end_date=message.text.strip())
    await message.answer("Введите город или '-' для пропуска:")
    await state.set_state(StatsForm.city)


@router.message(StatsForm.city)
async def stats_get_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await message.answer("Введите тему или '-' для пропуска:")
    await state.set_state(StatsForm.topic)


@router.message(StatsForm.topic)
async def stats_show(message: Message, state: FSMContext):
    data = await state.get_data()
    start_date = data["start_date"]
    end_date = data["end_date"]
    city = data["city"]
    topic = message.text.strip()

    query = "SELECT status, city, topic, date FROM applications WHERE 1=1"
    params = []

    def date_to_compare(date_str):
        d, m, y = date_str.split(".")
        return f"{y}-{m}-{d}"

    if start_date != "-":
        try:
            query += " AND date(substr(date,7,4) || '-' || substr(date,4,2) || '-' || substr(date,1,2)) >= ?"
            params.append(start_date)
        except Exception:
            await message.answer("Ошибка в формате начальной даты. Используйте ДД.ММ.ГГГГ")
            await state.clear()
            return

    if end_date != "-":
        try:
            query += " AND date(substr(date,7,4) || '-' || substr(date,4,2) || '-' || substr(date,1,2)) <= ?"
            params.append(end_date)
        except Exception:
            await message.answer("Ошибка в формате конечной даты. Используйте ДД.ММ.ГГГГ")
            await state.clear()
            return

    if city != "-":
        query += " AND city = ?"
        params.append(city)

    if topic != "-":
        query += " AND topic = ?"
        params.append(topic)

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    total = len(rows)
    solved = sum(1 for r in rows if r[0] == "Решена")
    unsolved = total - solved

    city_stats = {}
    topic_stats = {}

    if city == "-":
        for r in rows:
            city_stats[r[1]] = city_stats.get(r[1], 0) + 1
    if topic == "-":
        for r in rows:
            topic_stats[r[2]] = topic_stats.get(r[2], 0) + 1

    msg = (f"📊 Статистика заявок:\n\n"
           f"Общее количество: {total}\n"
           f"✅ Решено: {solved}\n"
           f"❌ Не решено: {unsolved}\n\n")

    if city_stats:
        msg += "По городам:\n"
        for c, count in city_stats.items():
            msg += f" - {c}: {count}\n"

    if topic_stats:
        msg += "\nПо темам:\n"
        for t, count in topic_stats.items():
            msg += f" - {t}: {count}\n"

    await message.answer(msg)
    await state.clear()


# ====== Запуск бота ======

async def main():
    bot = Bot(token=TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    logging.info("Бот запущен")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
