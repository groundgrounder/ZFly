"""Offline macOS proxy leases, including empty and populated Keychain records."""
import copy
import json
import subprocess
import tempfile
import unittest

from zfly_runtime.network import SystemProxy
from zfly_runtime.storage import RuntimeErrorMessage


KINDS = ("web", "secureweb", "socksfirewall")


class MacNetworkSetup:
    def __init__(self, credentials, enabled=False):
        self.proxies = {kind: {"enabled": enabled, "server": "127.0.0.1", "port": "7891"}
                        for kind in KINDS}
        # networksetup reports a matching Keychain item even when it is empty.
        self.keychain = {(kind, "127.0.0.1", "7891"): copy.deepcopy(credentials) for kind in KINDS}
        self.bypass = ["old.example"]
        self.fail_command = None

    def run(self, arguments, **_kwargs):
        if arguments[0] != "/usr/sbin/networksetup":
            raise AssertionError("The proxy lease must not read or write Keychain credentials")
        command, *values = arguments[1:]
        if command == self.fail_command:
            self.fail_command = None
            return subprocess.CompletedProcess(arguments, 1, "** Error: test failure", "")
        if command == "-listallnetworkservices":
            output = "An asterisk (*) denotes that a network service is disabled.\nWi-Fi"
        elif command == "-getproxybypassdomains":
            output = "\n".join(self.bypass) or "There aren't any bypass domains set on Wi-Fi."
        elif command == "-setproxybypassdomains":
            self.bypass = values[1:] if values[1:] != ["Empty"] else []
            output = ""
        else:
            for kind in KINDS:
                if command == "-get" + kind + "proxy":
                    value = self.proxies[kind]
                    authenticated = int((kind, value["server"], value["port"]) in self.keychain)
                    output = (f"Enabled: {'Yes' if value['enabled'] else 'No'}\n"
                              f"Server: {value['server']}\nPort: {value['port']}\n"
                              f"Authenticated Proxy Enabled: {authenticated}")
                    break
                if command == "-set" + kind + "proxy":
                    if len(values) != 3:
                        raise AssertionError("Authentication arguments can modify Keychain records")
                    _, server, port = values
                    self.proxies[kind] = {"enabled": True, "server": server, "port": port}
                    output = ""
                    break
                if command == "-set" + kind + "proxystate":
                    self.proxies[kind]["enabled"] = values[1] == "on"
                    output = ""
                    break
            else:
                raise AssertionError(f"Unexpected networksetup command: {command}")
        return subprocess.CompletedProcess(arguments, 0, output, "")


class SystemProxyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def proxy(self, settings):
        return SystemProxy(self.directory.name, runner=settings.run, platform="darwin")

    def test_empty_keychain_item_does_not_block_start_or_restore(self):
        settings = MacNetworkSetup({"account": "", "password": ""})
        before = copy.deepcopy(settings.proxies)
        keychain = copy.deepcopy(settings.keychain)
        proxy = self.proxy(settings)

        proxy.enable(7890, ["localhost"])
        self.assertTrue(all(value == {"enabled": True, "server": "127.0.0.1", "port": "7890"}
                            for value in settings.proxies.values()))
        self.assertEqual(settings.bypass, ["localhost"])
        self.assertEqual(settings.keychain, keychain)
        self.assertEqual(proxy.path.stat().st_mode & 0o777, 0o600)

        proxy.disable()
        self.assertEqual(settings.proxies, before)
        self.assertEqual(settings.bypass, ["old.example"])
        self.assertEqual(settings.keychain, keychain)
        self.assertFalse(proxy.path.exists())

    def test_existing_credentials_survive_without_being_read_or_persisted(self):
        credentials = {"account": "private-test-account", "password": "private-test-password"}
        settings = MacNetworkSetup(credentials, enabled=True)
        before = copy.deepcopy(settings.proxies)
        keychain = copy.deepcopy(settings.keychain)
        proxy = self.proxy(settings)

        proxy.enable(7890, [])
        lease = proxy.path.read_text()
        for secret in credentials.values():
            self.assertNotIn(secret, lease)
        self.assertEqual(set(json.loads(lease)["services"]), {"Wi-Fi"})
        self.assertEqual(settings.keychain, keychain)

        # Recover after a restart using the on-disk lease.
        self.proxy(settings).disable()
        self.assertEqual(settings.proxies, before)
        self.assertEqual(settings.keychain, keychain)
        self.assertFalse(proxy.path.exists())

    def test_partial_apply_rolls_back_without_losing_authentication_records(self):
        settings = MacNetworkSetup({"account": "", "password": ""})
        before = copy.deepcopy(settings.proxies)
        keychain = copy.deepcopy(settings.keychain)
        settings.fail_command = "-setsecurewebproxy"
        proxy = self.proxy(settings)

        with self.assertRaises(RuntimeErrorMessage):
            proxy.enable(7890, ["localhost"])
        self.assertIsNone(settings.fail_command)
        self.assertEqual(settings.proxies, before)
        self.assertEqual(settings.bypass, ["old.example"])
        self.assertEqual(settings.keychain, keychain)
        self.assertFalse(proxy.path.exists())

    def test_restore_preserves_another_apps_proxy_and_bypass_changes(self):
        settings = MacNetworkSetup({"account": "", "password": ""})
        before = copy.deepcopy(settings.proxies)
        proxy = self.proxy(settings)
        proxy.enable(7890, ["localhost"])
        changed = {"enabled": True, "server": "127.0.0.1", "port": "9999"}
        settings.proxies["web"] = changed.copy()
        settings.bypass = ["other-app.example"]

        proxy.disable()
        self.assertEqual(settings.proxies["web"], changed)
        for kind in KINDS[1:]:
            self.assertEqual(settings.proxies[kind], before[kind])
        self.assertEqual(settings.bypass, ["other-app.example"])
        self.assertFalse(proxy.path.exists())

    def test_corrupt_lease_is_reported_without_running_restore_commands(self):
        settings = MacNetworkSetup({}, enabled=True)
        proxy = self.proxy(settings)
        proxy.path.write_text("not-json")
        with self.assertRaisesRegex(RuntimeErrorMessage, "恢复记录损坏"):
            proxy.disable()
        self.assertEqual(settings.proxies["web"]["server"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
