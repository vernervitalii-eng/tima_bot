from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👶 Создать новую семью", callback_data="onboarding:create")],
        [InlineKeyboardButton(text="🔗 Подключиться по коду", callback_data="onboarding:join")],
    ])


def edit_time_keyboard(log_id: int, field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="−10 мин", callback_data=f"adjust:{field}:{log_id}:-10"),
            InlineKeyboardButton(text="−30 мин", callback_data=f"adjust:{field}:{log_id}:-30"),
            InlineKeyboardButton(text="+10 мин", callback_data=f"adjust:{field}:{log_id}:10"),
        ],
        [InlineKeyboardButton(text="✏️ Ввести вручную", callback_data=f"edit:{field}:{log_id}")],
    ])


def activity_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍼 Грудь", callback_data="activity:feeding:грудь"),
         InlineKeyboardButton(text="🍼 Смесь", callback_data="activity:feeding:смесь")],
        [InlineKeyboardButton(text="🥣 Прикорм", callback_data="activity:feeding:прикорм")],
        [InlineKeyboardButton(text="💩 Подгузник", callback_data="activity:diaper:подгузник")],
        [InlineKeyboardButton(text="💊 Лекарство / Зубы", callback_data="activity:notes:ask")],
    ])


def join_role_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мама", callback_data="join-role:Мама"),
         InlineKeyboardButton(text="Папа", callback_data="join-role:Папа")],
        [InlineKeyboardButton(text="Бабушка", callback_data="join-role:Бабушка"),
         InlineKeyboardButton(text="Няня", callback_data="join-role:Няня")],
        [InlineKeyboardButton(text="✏️ Другое имя", callback_data="join-role:custom")],
    ])


def family_keyboard(is_admin: bool) -> InlineKeyboardMarkup | None:
    if not is_admin:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Пригласить по Telegram ID", callback_data="family:invite")],
    ])


def settings_keyboard(silent_mode: bool, is_admin: bool = True) -> InlineKeyboardMarkup:
    label = "🔕 Тихий режим: ВКЛ" if silent_mode else "🔔 Тихий режим: ВЫКЛ"
    reset_label = "🗑 Полный сброс" if is_admin else "🚪 Покинуть семью"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data="settings:toggle-silent")],
        [InlineKeyboardButton(text="📤 Экспорт данных", callback_data="export:menu")],
        [InlineKeyboardButton(text=reset_label, callback_data="settings:reset")],
    ])


def reset_confirmation_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    action = "family" if is_admin else "leave"
    label = "Да, удалить всё" if is_admin else "Да, выйти"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚠️ {label}", callback_data=f"confirm-reset:{action}")],
        [InlineKeyboardButton(text="Отмена", callback_data="confirm-reset:cancel")],
    ])


def export_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="CSV для Excel — 7 дней", callback_data="export:csv:7")],
        [InlineKeyboardButton(text="CSV для Excel — 30 дней", callback_data="export:csv:30")],
    ])


def day_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="day:today"),
            InlineKeyboardButton(text="⏪ Вчера", callback_data="day:yesterday"),
        ],
        [InlineKeyboardButton(text="🗓 Выбрать дату", callback_data="day:pick")],
    ])


def day_date_keyboard(today: date, days: int = 14) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=(today - timedelta(days=offset)).strftime("%d.%m"),
            callback_data=f"day:date:{today - timedelta(days=offset):%Y-%m-%d}",
        )
        for offset in range(days)
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="day:today")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ai_refresh_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить анализ", callback_data="ai:refresh")],
    ])
