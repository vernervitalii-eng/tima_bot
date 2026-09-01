from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_keyboard(is_sleeping: bool) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💤 Уснул сейчас"), KeyboardButton(text="☀️ Проснулся")],
            [KeyboardButton(text="📅 Хронология дня"), KeyboardButton(text="📋 История записей")],
            [KeyboardButton(text="🧠 AI-Режим (Gemini)"), KeyboardButton(text="📊 График снов")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="👥 Семья")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )
