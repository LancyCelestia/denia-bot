"""`reply_guard_logic` 的断言测试 —— 不 import nonebot、不碰 QQ、不调模型，直接跑：

    cd C:\\Software\\ChatBot\\nonebot
    .venv\\Scripts\\python.exe tests\\test_reply_guard_logic.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reply_guard_logic import StreamGuard, is_planning, sanitize  # noqa: E402

FAILED: list[str] = []

LEAK_ONE = (
    'Initial response: Rover\'s playful "beat you to death" calls for Shorekeeper\'s response, '
    "keeping her character in mind. Considering her personality and the playful context, a subtle, "
    "teasing retort seems fitting. Finalizing the character's response, after careful consideration "
    'of tone and brevity, the response is "……隔着海，你碰不到我。……而且，你舍得么。" '
    "(Across the sea, you can't touch me. Also, would you really?)"
)
LEAK_TWO = (
    '"……隔着这片海，你碰不到我。……就算真站在你面前，漂泊者，你舍得么。" seems best, '
    "aligning with the character's mysterious detachment and the playful context."
)


def run(name, fn):
    try:
        fn()
    except AssertionError as e:
        FAILED.append(f"{name}: {e}")
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        FAILED.append(f"{name}: {type(e).__name__}: {e}")
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
    else:
        print(f"  ok    {name}")


def t_the_observed_planning_paragraphs_are_dropped():
    for text in (LEAK_ONE, LEAK_TWO):
        clean, dropped = sanitize(text)
        assert dropped and clean == "", f"计划文本必须整段丢掉：{clean!r}"


def t_a_normal_reply_is_left_alone():
    text = "……嗯，我在的。"
    assert sanitize(text) == (text, False), "正常回复一个字都不该动"


def t_a_technical_reply_with_english_terms_is_left_alone():
    text = "Python 的 GIL 是进程级的，别拿线程绕。"
    assert sanitize(text) == (text, False), "夹英文术语不等于自述计划"


def t_a_valid_paragraph_survives_when_mixed_with_planning():
    text = "……嗯。\nInitial response: I should keep the character tone and reply with brevity."
    clean, dropped = sanitize(text)
    assert clean == "……嗯。" and dropped, f"只丢计划段，别把正文一起吃了：{clean!r}"


def t_the_english_fallback_catches_planning_without_an_exact_marker():
    text = "The response should keep the character tone and reply with playful brevity here."
    assert is_planning(text), "英文占比高且计划词足够多时也要认出来"


def t_a_technical_english_reply_is_not_mistaken_for_planning():
    text = (
        "The HTTP response body uses the character encoding declared in the header, "
        "and the reply should be decoded before parsing."
    )
    assert not is_planning(text), "技术英文里的 response/character 不该被当成自述计划"


def t_stream_guard_releases_a_normal_reply_immediately():
    guard = StreamGuard()
    assert guard.feed("……嗯，我在的。") == "……嗯，我在的。", "中文正文不该被扣住"
    assert guard.flush() == "", "已经放行过就没有尾部"


def t_stream_guard_flushes_a_short_english_reply():
    guard = StreamGuard()
    assert guard.feed("OK") == "", "英文短句先扣住，等判定"
    assert guard.flush() == "OK", "流结束时必须补发，不能把正常回复吞掉"


def t_stream_guard_drops_planning_and_everything_after():
    guard = StreamGuard()
    assert guard.feed("Initial response:") == "", "计划文本不能漏出去"
    assert guard.feed("……嗯。") == "", "已经确认是计划，后面整条流都别发"
    assert guard.flush() == "", "尾部也不能补发"
    assert guard.dropped is True, "要留下「丢过东西」的痕迹给日志"


def t_stream_guard_keeps_later_planning_out_of_a_normal_reply():
    guard = StreamGuard()
    assert guard.feed("……嗯。") == "……嗯。"
    assert guard.feed(" Initial response: I should...") == "", "后半段冒出计划也要拦"
    assert guard.dropped is True


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    print(f"跑 {len(tests)} 条断言：")
    for name, fn in tests:
        run(name, fn)
    print(f"\n{len(tests) - len(FAILED)} 通过 / {len(FAILED)} 失败")
    sys.exit(1 if FAILED else 0)
