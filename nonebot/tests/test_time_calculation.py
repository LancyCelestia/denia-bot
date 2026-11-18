from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import time_calculation as tc

NOW = datetime(2026, 11, 10, 14, 10, 38, tzinfo=timezone(timedelta(hours=8)))


def test_composite_day_and_hours_are_added_together():
    result = tc.calculate('一天二十小时后是啥时候吖', NOW)
    assert result == datetime(2026, 11, 12, 10, 10, 38, tzinfo=NOW.tzinfo)
    assert tc.calculate('诶呀 是现在的 一天二十小时后啦', NOW) == result
    assert tc.calculate('那从现在开始一天二十小时后是几点？', NOW) == result
    note = tc.render(result, NOW)
    assert '11月12日' in note and '后天' in note and '10:10' in note


def test_hours_plus_minutes_and_previous_day():
    assert tc.calculate('现在的时间加上十七个小时是什么时候吖', NOW) == NOW + timedelta(hours=17)
    assert tc.calculate('现在往前推三小时二十分钟是几点', NOW) == NOW - timedelta(hours=3, minutes=20)
    assert tc.calculate('半小时后是几点', NOW) == NOW + timedelta(minutes=30)
    assert tc.calculate('1天20小时之后是几号', NOW) == NOW + timedelta(days=1, hours=20)
    assert tc.calculate('再过一天二十小时是什么时候', NOW) == NOW + timedelta(days=1, hours=20)


def test_calendar_rollover_and_relative_day():
    now = datetime(2024, 2, 28, 23, 30, tzinfo=NOW.tzinfo)
    result = tc.calculate('一天二小时后是几号', now)
    assert result == datetime(2024, 3, 1, 1, 30, tzinfo=NOW.tzinfo)
    assert '后天' in tc.render(result, now)


def test_non_calculations_and_ambiguous_reference_do_not_invent_facts():
    for message in (
        '晚上给你买小甜品吃',
        '昨天十点过两个小时是几点',
        '明天上午十点后两小时是几点',
        '今天上了四个小时课，好累',
        '一个半小时后是几点',
        '两小时后又过三小时是几点',
        '明天是星期几',
        '1周3天后是几号',
        '一整天20小时后是几点',
        '二百三小时后是几点',
        '1.5天20小时后是几点',
        '从出发算两小时后是几点？',
        '三小时后到家，什么时候出门？',
        '那两小时后是几点？',
        '会议开始两小时后是几点？',
        '生日两天后是几号？',
        '从那开始两小时后是几点？',
        '请问那两小时后是几点？',
        '从开始两小时后是几点？',
        '开始两小时后是几点？',
        '这两小时后是几点？',
    ):
        assert tc.calculate(message, NOW) is None, message
