"""Transport and transaction regressions; no sockets or running cores."""
import io
import json
import unittest
import urllib.error

from zfly.controller import Controller, ControllerError, PreviewController, validate_config
from zfly.i18n import get_language, set_language


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode())
        self.status = 200


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.previous_language = get_language()
        set_language("zh-Hans")

    def tearDown(self):
        set_language(self.previous_language)

    def test_automatic_pin_preview_and_unfix_encoded_transport(self):
        preview = PreviewController()
        preview.select("自动选择", "新加坡")
        self.assertEqual(preview.snapshot()["proxies"]["proxies"]["自动选择"]["fixed"], "新加坡")
        preview.unfix("自动选择")
        self.assertFalse(preview.snapshot()["proxies"]["proxies"]["自动选择"]["fixed"])
        calls = []
        def serve(request, _timeout):
            calls.append((request.method, request.selector))
            return Response({})
        self.controller(serve).unfix("Auto/% 🧪")
        self.assertEqual(calls, [("DELETE", "/proxies/Auto%2F%25%20%F0%9F%A7%AA")])

    def controller(self, callback):
        class Opener:
            def open(self, request, timeout):
                return callback(request, timeout)
        return Controller("http://127.0.0.1:12345", "test-only", opener=Opener())

    def test_save_sends_expected_values_and_confirms_a_lost_response(self):
        requests, receipts, applied = [], {}, []
        def serve(request, timeout):
            self.assertEqual(request.selector, "/action")
            self.assertGreater(timeout, 15)
            command = json.loads(request.data)
            requests.append(command)
            key = command["requestID"]
            if key not in receipts:
                applied.append(command["patch"])
                receipts[key] = {"state": {"running": False}}
                raise urllib.error.URLError(TimeoutError())
            return Response(receipts[key])
        self.controller(serve).configure({"mode": "global", "mixed-port": 7890},
                                        {"mode": "rule", "mixed-port": 7890})
        self.assertEqual(applied, [{"mode": "global"}])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[0]["expected"], {"mode": "rule"})

    def test_unconfirmed_save_does_not_claim_a_definite_failure(self):
        def offline(_request, _timeout):
            raise urllib.error.URLError(TimeoutError())
        with self.assertRaisesRegex(ControllerError, "保存结果尚未确认"):
            self.controller(offline).configure({"mode": "global"}, {"mode": "rule"})

    def test_malformed_save_response_is_retried_and_reported_as_unconfirmed(self):
        calls = []
        def malformed(request, _timeout):
            calls.append(json.loads(request.data))
            return Response({"wrong": "receipt"})
        with self.assertRaisesRegex(ControllerError, "保存结果尚未确认"):
            self.controller(malformed).configure({"mode": "global"}, {"mode": "rule"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

    def test_runtime_rejection_keeps_actionable_detail_without_retry(self):
        calls = []
        def reject(request, _timeout):
            calls.append(request)
            payload = json.dumps({"message": "设置已被其他界面修改，请重新载入。"}).encode()
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(payload))
        with self.assertRaisesRegex(ControllerError, "其他界面修改"):
            self.controller(reject).configure({"mode": "global"}, {"mode": "rule"})
        self.assertEqual(len(calls), 1)

    def test_port_validation_matches_persistent_listener_constraints(self):
        with self.assertRaisesRegex(ControllerError, "混合端口"):
            validate_config({"mixed-port": 0})
        validate_config({"mixed-port": 7890, "port": 0, "socks-port": 0})
        with self.assertRaisesRegex(ControllerError, "不能重复"):
            validate_config({"mixed-port": 7890, "port": 7890})


if __name__ == "__main__":
    unittest.main()
