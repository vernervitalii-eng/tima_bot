from datetime import date, timedelta
from collections.abc import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def start_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать семью", callback_data="onboarding:create")],
        [InlineKeyboardButton(text="Ввести семейный код", callback_data="onboarding:join")],
    ])


def edit_time_keyboard(log_id: int, field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="−10 мин", callback_data=f"adjust:{field}:{log_id}:-10"),
            InlineKeyboardButton(text="−30 мин", callback_data=f"adjust:{field}:{log_id}:-30"),
            InlineKeyboardButton(text="+10 мин", callback_data=f"adjust:{field}:{log_id}:10"),
        ],
        [InlineKeyboardButton(text="Ввести время", callback_data=f"edit:{field}:{log_id}")],
    ])


def join_role_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мама", callback_data="join-role:Мама"),
         InlineKeyboardButton(text="Папа", callback_data="join-role:Папа")],
        [InlineKeyboardButton(text="Бабушка", callback_data="join-role:Бабушка"),
         InlineKeyboardButton(text="Няня", callback_data="join-role:Няня")],
        [InlineKeyboardButton(text="Другое имя", callback_data="join-role:custom")],
    ])


def family_keyboard(is_admin: bool) -> InlineKeyboardMarkup | None:
    if not is_admin:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пригласить по ID", callback_data="family:invite")],
    ])


def settings_keyboard(silent_mode: bool, is_admin: bool = True) -> InlineKeyboardMarkup:
    label = "Тихий режим: вкл." if silent_mode else "Тихий режим: выкл."
    reset_label = "Сбросить данные" if is_admin else "Покинуть семью"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data="settings:toggle-silent")],
        [InlineKeyboardButton(text="Экспорт данных", callback_data="export:menu")],
        [InlineKeyboardButton(text=reset_label, callback_data="settings:reset")],
    ])


def reset_confirmation_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    action = "family" if is_admin else "leave"
    label = "Да, удалить всё" if is_admin else "Да, выйти"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"confirm-reset:{action}")],
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
            InlineKeyboardButton(text="Сегодня", callback_data="day:today"),
            InlineKeyboardButton(text="Вчера", callback_data="day:yesterday"),
        ],
        [InlineKeyboardButton(text="Выбрать дату", callback_data="day:pick")],
    ])


def chart_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="7 дней", callback_data="chart:7"),
            InlineKeyboardButton(text="14 дней", callback_data="chart:14"),
        ],
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


def day_navigation_keyboard(selected: date, today: date) -> InlineKeyboardMarkup:
    previous = selected - timedelta(days=1)
    next_day = selected + timedelta(days=1)
    rows = [[
        InlineKeyboardButton(text="◀️ Вчера", callback_data=f"day:nav:{previous:%Y-%m-%d}"),
        InlineKeyboardButton(text="Выбрать дату", callback_data="day:pick"),
        InlineKeyboardButton(text="Завтра ▶️", callback_data=f"day:nav:{next_day:%Y-%m-%d}")
        if next_day <= today else InlineKeyboardButton(text="Сегодня", callback_data="day:today"),
    ]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ai_refresh_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обновить", callback_data="ai:refresh")],
    ])


def ai_dialog_exit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Завершить", callback_data="ai:dialog:exit")],
    ])


def premium_tariffs_keyboard(plans: Iterable[object]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=plan.button_label,
            callback_data=f"premium:buy:{plan.code}",
        )]
        for plan in plans
    ])


def history_keyboard(log_ids: list[int], page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = []
    for index, log_id in enumerate(log_ids, start=1):
        position = page * 10 + index
        rows.append([
            InlineKeyboardButton(
                text=f"✏️ Изменить №{position}",
                callback_data=f"history:edit:{log_id}:{page}",
            ),
            InlineKeyboardButton(
                text=f"🗑 Удалить №{position}",
                callback_data=f"history:delete:{log_id}:{page}",
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text="➕ Добавить пропущенный сон",
            callback_data=f"history:add:{page}",
        )
    ])
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"history:page:{page - 1}"))
    if page + 1 < total_pages:
        navigation.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"history:page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_edit_keyboard(log_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🕒 Изменить начало",
                callback_data=f"history:field:start:{log_id}:{page}",
            ),
            InlineKeyboardButton(
                text="🕓 Изменить окончание",
                callback_data=f"history:field:end:{log_id}:{page}",
            ),
        ],
        [InlineKeyboardButton(text="↩️ Назад к истории", callback_data=f"history:page:{page}")],
    ])


def history_edit_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Сохранить изменение", callback_data="history:edit:save"),
            InlineKeyboardButton(text="↩️ Отмена", callback_data="history:edit:cancel"),
        ]
    ])


def history_add_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Сохранить сон", callback_data="history:add:save"),
            InlineKeyboardButton(text="✏️ Ввести заново", callback_data="history:add:retry"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="history:add:cancel")],
    ])


def history_delete_confirmation_keyboard(log_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"history:confirm:{log_id}:{page}"),
            InlineKeyboardButton(text="↩️ Отмена", callback_data=f"history:cancel:{page}"),
        ]
    ])
