import base64
import copy
import unittest

from zfly.management import PreviewManagement, validate_network, validate_profile_command
from zfly.controller import ControllerError, PreviewController


class ManagementValidationTests(unittest.TestCase):
    def test_linked_preview_keeps_core_edits_in_other_management_responses(self):
        core = PreviewController()
        manager = PreviewManagement(core)
        baseline = core.configuration()
        core.configure({**baseline, "mode": "global", "ipv6": True}, baseline)
        for command in ({"action": "catalog.list", "section": "proxies"},
                        {"action": "workspace.read", "section": "dns"}):
            with self.subTest(command=command):
                state = manager.perform(command)["state"]
                self.assertEqual(state["network"]["mode"], "global")
                self.assertTrue(state["network"]["ipv6"])

    def test_linked_preview_rejects_stale_network_writes_after_core_edits(self):
        core = PreviewController()
        manager = PreviewManagement(core)
        before = manager.snapshot()["network"]
        baseline = core.configuration()
        core.configure({**baseline, "mode": "global"}, baseline)
        with self.assertRaisesRegex(ControllerError, "已修改"):
            manager.perform({"action": "network.save", "network": before, "expected": before})
        self.assertEqual(core.configuration()["mode"], "global")

    def test_linked_preview_rejects_port_conflicts_without_partial_state_changes(self):
        core = PreviewController()
        manager = PreviewManagement(core)
        baseline = core.configuration()
        core.configure({**baseline, "port": 8000}, baseline)
        before = manager.snapshot()
        network = {**copy.deepcopy(before["network"]), "mixedPort": 8000, "ipv6": True}
        with self.assertRaisesRegex(ControllerError, "不能重复"):
            manager.perform({"action": "network.save", "network": network, "expected": before["network"]})
        self.assertEqual(manager.snapshot(), before)
        self.assertEqual(core.configuration()["mixed-port"], 7890)

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
