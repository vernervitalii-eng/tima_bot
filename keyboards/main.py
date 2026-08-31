from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_keyboard(is_sleeping: bool) -> ReplyKeyboardMarkup:
    sleep_button = "☀️ Проснулся" if is_sleeping else "💤 Уснул"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=sleep_button)],
            [KeyboardButton(text="🍼 Активность"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="📅 Хронология дня"), KeyboardButton(text="🧠 AI-анализ")],
            [KeyboardButton(text="📌 Текущий статус")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="👥 Семья")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )
