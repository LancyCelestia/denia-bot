# 移植自 Pancakes-Labs/astrbot_plugin_disaster_warning v1.6.5（提交 20ba182），
# 上游作者：DBJD-CR & Aloys233；本文件与上游同源衍生，按 AGPL-3.0 分发。
# 修改说明：为 denia-bot 重写 NoneBot2 接线/纯逻辑/测试；详见 docs/third-party/disaster-warning-NOTICE.md

"""预警身份去重用例。"""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disaster_tone as tone
from disaster_logic import sample_event
from tests.test_disaster_china_logic import EQSC_TYPHOON, JIAN_TSUNAMI, JIAN_WEATHER
import disaster_china_logic as china


class IdentityTest(unittest.TestCase):
    def test_earthquake_identity_ignores_report_number(self):
        first = sample_event()
        later = replace(sample_event(), report_num=3)
        self.assertEqual(
            tone.alert_identity('earthquake', first),
            tone.alert_identity('earthquake', later),
            '同一地震的多报应共用同一个去重标识',
        )

    def test_weather_identity_ignores_content_updates(self):
        alert = china.parse_jian_weather(JIAN_WEATHER)
        updated = replace(alert, description='更新后的正文')
        self.assertEqual(
            tone.alert_identity('weather', alert),
            tone.alert_identity('weather', updated),
            '同一预警的正文更新不应产生新的去重标识',
        )

    def test_tsunami_and_typhoon_identity_are_stable(self):
        tsunami = china.parse_jian_tsunami(JIAN_TSUNAMI)
        typhoon = china.parse_eqsc_typhoon_item(EQSC_TYPHOON)
        self.assertEqual(
            tone.alert_identity('tsunami', tsunami),
            tone.alert_identity('tsunami', replace(tsunami, number=tsunami.number + 1)),
            '同一海啸的新报次不应重复播报',
        )
        self.assertEqual(
            tone.alert_identity('typhoon', typhoon),
            tone.alert_identity('typhoon', replace(typhoon, updated_at='2025/09/23 12:00:00')),
            '同一台风的新轨迹不应重复播报',
        )
        self.assertNotEqual(
            tone.alert_identity('typhoon', typhoon),
            tone.alert_identity('typhoon_stop', typhoon),
            '停编通知与台风本体是不同的播报',
        )


if __name__ == '__main__':
    unittest.main()
