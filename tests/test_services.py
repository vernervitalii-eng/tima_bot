from datetime import date, datetime

from services.norms import norm_for_age
from services.time_utils import age_parts, is_quiet_hours, parse_relative_time
from keyboards.inline import settings_keyboard, start_choice_keyboard


def test_norm_boundaries():
    assert norm_for_age(5).wake_max == 135
    assert norm_for_age(24).sleep_min == 12


def test_age_parts():
    assert age_parts(date(2025, 1, 15), date(2025, 3, 20)) == (2, 5)


def test_parser_minutes_ago():
    now = datetime(2025, 3, 20, 12, 0)
    assert parse_relative_time("проснулся 20 минут назад", "UTC", now) == datetime(2025, 3, 20, 11, 40)


def test_parser_clock_rolls_to_previous_day():
    now = datetime(2025, 3, 20, 1, 0)
    assert parse_relative_time("уснул в 23:30", "UTC", now) == datetime(2025, 3, 19, 23, 30)


def test_quiet_hours():
    assert is_quiet_hours("UTC", datetime(2025, 3, 20, 23, 0))
    assert not is_quiet_hours("UTC", datetime(2025, 3, 20, 12, 0))


def test_start_and_reset_keyboards():
    start_callbacks = [button.callback_data for row in start_choice_keyboard().inline_keyboard for button in row]
    assert start_callbacks == ["onboarding:create", "onboarding:join"]
    admin_callbacks = [button.callback_data for row in settings_keyboard(False, True).inline_keyboard for button in row]
    assert "settings:reset" in admin_callbacks
