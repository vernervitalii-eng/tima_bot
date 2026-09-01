from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_keyboard(is_sleeping: bool) -> ReplyKeyboardMarkup:
    primary_action = "☀️ Проснулся" if is_sleeping else "💤 Уснул"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=primary_action)],
            [KeyboardButton(text="📅 Хронология дня")],
            [KeyboardButton(text="🧠 AI-Режим"), KeyboardButton(text="📊 График снов")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )
