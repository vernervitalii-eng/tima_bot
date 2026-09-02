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
            [KeyboardButton(text="📅 День"), KeyboardButton(text="🧠 Режим (AI)")],
            [KeyboardButton(text="💬 Консультант"), KeyboardButton(text="⭐️ Премиум")],
            [KeyboardButton(text="📊 График"), KeyboardButton(text="📋 История")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Что записать?",
    )
