from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    name = State()
    birth_date = State()


class EditTime(StatesGroup):
    value = State()


class SettingsEdit(StatesGroup):
    birth_date = State()


class JoinFamily(StatesGroup):
    code = State()
    display_name = State()


class InviteFamily(StatesGroup):
    telegram_id = State()


class AIState(StatesGroup):
    in_dialog = State()
