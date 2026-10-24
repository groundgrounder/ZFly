import os
import unittest
from unittest.mock import patch

from zfly.i18n import get_language, set_language, tr, trn


class I18nTests(unittest.TestCase):
    def setUp(self):
        self.previous_language = get_language()

    def tearDown(self):
        set_language(self.previous_language)

    @classmethod
    def tearDownClass(cls):
        set_language("zh-Hans")

    def test_explicit_language_preference_controls_translation(self):
        set_language("en")
        self.assertEqual(get_language(), "en")
        self.assertEqual(tr("概览"), "Overview")

        set_language("zh-Hans")
        self.assertEqual(get_language(), "zh-Hans")
        self.assertEqual(tr("概览"), "概览")

    def test_system_language_uses_locale_priority_and_chinese_default(self):
        cases = [
            ({"LANGUAGE": "zh_CN.UTF-8", "LC_ALL": "en_US.UTF-8"}, "概览"),
            ({"LANGUAGE": "en_US.UTF-8", "LC_ALL": "zh_CN.UTF-8"}, "Overview"),
            ({"LC_ALL": "zh_TW.UTF-8", "LANG": "en_US.UTF-8"}, "概览"),
            ({"LC_CTYPE": "en_GB.UTF-8", "LANG": "zh_CN.UTF-8"}, "Overview"),
            ({"LANG": "en_US.UTF-8"}, "Overview"),
            ({"LANG": "C"}, "概览"),
            ({}, "概览"),
        ]
        for environment, expected in cases:
            with self.subTest(environment=environment):
                with patch.dict(os.environ, environment, clear=True):
                    set_language("system")
                    self.assertEqual(get_language(), "system")
                    self.assertEqual(tr("概览"), expected)

    def test_unknown_source_falls_back_to_chinese(self):
        set_language("en")

        self.assertEqual(tr("尚未收录的可见文案"), "尚未收录的可见文案")

    def test_named_placeholders_are_formatted_after_translation(self):
        set_language("en")

        message = tr("将删除“{name}”及其本地文件。", name="Node A")
        self.assertEqual(message, "Delete “Node A” and its local files.")
        self.assertEqual(tr("未收录：{name}", name="节点"), "未收录：节点")

    def test_plural_translation_uses_english_singular_and_plural(self):
        set_language("en")

        self.assertEqual(trn("{count} 个节点", "{count} 个节点", 1), "1 node")
        self.assertEqual(trn("{count} 个节点", "{count} 个节点", 2), "2 nodes")

        set_language("zh-Hans")
        self.assertEqual(trn("{count} 个节点", "{count} 个节点", 2), "2 个节点")


if __name__ == "__main__":
    unittest.main()
