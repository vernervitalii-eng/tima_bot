from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_keyboard(is_sleeping: bool | None) -> ReplyKeyboardMarkup:
    primary_row = (
        [KeyboardButton(text="💤 Уснул"), KeyboardButton(text="☀️ Проснулся")]
        if is_sleeping is None
        else [KeyboardButton(text="☀️ Проснулся" if is_sleeping else "💤 Уснул")]
    )
    return ReplyKeyboardMarkup(
        keyboard=[
            primary_row,
            [KeyboardButton(text="📅 Хронология дня")],
            [KeyboardButton(text="🧠 AI-Режим"), KeyboardButton(text="📊 График снов")],
            [KeyboardButton(text="📋 История записей")],
            [KeyboardButton(text="💬 Чат с ИИ-консультантом")],
            [KeyboardButton(text="⭐️ Premium подписка")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )
