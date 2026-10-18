import base64
import unittest

from zfly.management import PreviewManagement, validate_network, validate_profile_command
from zfly.controller import ControllerError


class ManagementValidationTests(unittest.TestCase):
    def test_network_validation_rejects_non_objects_and_unsafe_devices(self):
        with self.assertRaises(ControllerError):
            validate_network([])
        with self.assertRaises(ControllerError):
            validate_network({"tunDevice": "bad name"})

    def test_profile_validation_rejects_invalid_management_input(self):
        commands = (
            {"action": "profile.add", "url": 42},
            {"action": "profile.add", "url": "https://example.test/a", "label": []},
            {"action": "profile.import", "data": "not-base64"},
        )
        for command in commands:
            with self.subTest(command=command), self.assertRaises(ControllerError):
                validate_profile_command(command)

    def test_preview_uses_the_same_profile_validation(self):
        manager = PreviewManagement()
        with self.assertRaises(ControllerError):
            manager.perform({"action": "profile.add", "url": "http://user:pass@example.test/a"})
        result = manager.perform({
            "action": "profile.import",
            "label": "导入配置",
            "data": base64.b64encode(b"mixed-port: 7890\n").decode(),
        })
        self.assertEqual(result["state"]["profiles"][-1]["label"], "导入配置")

    def test_preview_rejects_stale_profile_mutations_and_bad_file_data(self):
        manager = PreviewManagement()
        profile = manager.snapshot()["profiles"][0]
        with self.assertRaisesRegex(ControllerError, "已被更新"):
            manager.perform({"action": "profile.edit", "id": profile["id"], "revision": "stale", "label": "新名称"})

        result = manager.perform({"action": "profile.edit", "id": profile["id"], "revision": profile["revision"], "label": "新名称"})
        updated = result["state"]["profiles"][0]
        self.assertNotEqual(updated["revision"], profile["revision"])

        imported = manager.perform({
            "action": "profile.import",
            "label": "可编辑",
            "data": base64.b64encode(b"mixed-port: 7890\n").decode(),
        })
        file_profile = imported["state"]["profiles"][-1]
        file = manager.perform({"action": "profile.read", "id": file_profile["id"]})["file"]
        with self.assertRaisesRegex(ControllerError, "编码无效"):
            manager.perform({"action": "profile.write", "id": file_profile["id"], "revision": file["revision"], "data": "%%%"})

    def test_preview_rejects_malformed_state_commands(self):
        manager = PreviewManagement()
        state = manager.snapshot()
        with self.assertRaisesRegex(ControllerError, "运行状态无效"):
            manager.perform({"action": "running.set", "running": 1})
        with self.assertRaisesRegex(ControllerError, "设置已修改"):
            manager.perform({"action": "network.save", "expected": {}, "network": state["network"]})


if __name__ == "__main__":
    unittest.main()
