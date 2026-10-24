from importlib.resources import files
import copy
import ipaddress
import http.client
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import uuid

from zfly_runtime.schema import CONFIG_KEYS, validate_core_config
from .i18n import tr
from zfly_runtime.storage import RuntimeErrorMessage


class ControllerError(Exception):
    pass


class TransportError(ControllerError):
    """The connection failed without confirming the outcome of a request."""


class ResponseError(ControllerError):
    """A response could not confirm the result of the requested operation."""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_endpoint(endpoint):
    try:
        parts = urllib.parse.urlsplit(endpoint.strip())
        host = parts.hostname
        loopback = host == "localhost" or host in ("127.0.0.1", "::1")
        if host and host != "localhost":
            loopback = ipaddress.ip_address(host).is_loopback and host in ("127.0.0.1", "::1")
        if (
            parts.scheme not in ("http", "https")
            or not loopback
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or "?" in endpoint
            or "#" in endpoint
            or (parts.port is not None and not 1 <= parts.port <= 65535)
        ):
            raise ValueError
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    except ValueError:
        raise ControllerError(tr("ZFly 本地运行时地址无效，请重试启动。")) from None


class Controller:
    def __init__(self, endpoint, secret, opener=None):
        self.endpoint = validate_endpoint(endpoint)
        if any(ord(char) < 32 or ord(char) == 127 for char in secret):
            raise ControllerError(tr("控制器密钥不能包含换行或控制字符。"))
        self._secret = secret
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirects()
        )

    def _request(self, method, *segments, body=None, query=None, timeout=8, management_errors=True):
        path = "/".join(urllib.parse.quote(segment, safe="") for segment in segments)
        url = self.endpoint + "/" + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json"}
        if self._secret:
            headers["Authorization"] = "Bearer " + self._secret
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                if not 200 <= response.status < 300:
                    raise ControllerError(tr("控制器请求失败（HTTP {status}）。", status=response.status))
                if response.status == 204:
                    return None
                result = response.read()
                return json.loads(result) if result else None
        except urllib.error.HTTPError as error:
            detail = None
            if management_errors and error.code not in (401, 403) and not 300 <= error.code < 400:
                try:
                    payload = json.loads(error.read(16384))
                    if isinstance(payload.get("message"), str):
                        detail = payload["message"]
                except (ValueError, OSError, AttributeError):
                    pass
            error.close()
            if error.code in (401, 403):
                message = tr("控制器拒绝访问，请检查控制器密钥。")
            elif 300 <= error.code < 400:
                message = tr("控制器发生重定向，已停止请求。")
            elif error.code in (503, 504):
                message = tr("核心暂时无法完成请求，请稍后重试。")
            else:
                message = tr("控制器请求失败（HTTP {status}）。", status=error.code)
            raise ControllerError(detail or message) from None
        except (urllib.error.URLError, OSError, http.client.IncompleteRead):
            raise TransportError(tr("ZFly 内置核心没有响应，请重试启动。")) from None
        except ValueError:
            raise ResponseError(tr("控制器返回了无法识别的数据。")) from None

    def snapshot(self):
        result = {name: self._request("GET", name) for name in ("version", "configs", "proxies", "connections")}
        try:
            if not isinstance(result["version"]["version"], str):
                raise ValueError
            if not isinstance(result["configs"], dict):
                raise ValueError
            if not isinstance(result["proxies"]["proxies"], dict):
                raise ValueError
            connections = result["connections"].get("connections") or []
            if not isinstance(connections, list):
                raise ValueError
            result["connections"]["connections"] = connections
            return result
        except (KeyError, TypeError, AttributeError, ValueError):
            raise ControllerError(tr("控制器返回了无法识别的数据。")) from None

    def configuration(self):
        config = self._request("GET", "configs")
        if not isinstance(config, dict):
            raise ControllerError(tr("核心设置格式无效。"))
        return config

    def configure(self, draft, baseline):
        patch = config_changes(draft, baseline)
        if not patch:
            return
        validate_config({**baseline, **patch})
        command = {
            "action": "core.configure", "patch": patch,
            "expected": {key: baseline.get(key) for key in patch},
            "requestID": uuid.uuid4().hex,
        }
        # Reuse the receipt ID if a committed response was lost in transport.
        for attempt in range(2):
            try:
                result = self._request("POST", "action", body=command, timeout=240)
                if not isinstance(result, dict) or not isinstance(result.get("state"), dict):
                    raise ResponseError(tr("保存结果尚未确认，请刷新查看后再操作。"))
                return
            except (TransportError, ResponseError):
                if attempt:
                    raise ControllerError(tr("连接中断，保存结果尚未确认；请刷新查看，避免重复提交。")) from None

    def select(self, group, proxy):
        self._request("PUT", "proxies", group, body={"name": proxy})

    def unfix(self, group):
        self._request("DELETE", "proxies", group)

    def delay(self, proxy):
        result = self._request(
            "GET", "proxies", proxy, "delay",
            query={"url": "https://www.gstatic.com/generate_204", "timeout": 5000},
        )
        if not isinstance(result, dict) or type(result.get("delay")) is not int:
            raise ControllerError(tr("控制器返回了无法识别的测速数据。"))
        return result["delay"]

    def close(self, connection):
        self._request("DELETE", "connections", connection)

    def inspect(self, tool, name="", record_type="A"):
        if tool == "rules":
            result = self._request("GET", "rules")
        elif tool == "dnsQuery":
            if not name.strip() or record_type not in ("A", "AAAA", "CNAME", "MX", "TXT", "NS", "HTTPS"):
                raise ControllerError(tr("请输入域名并选择查询类型。"))
            result = self._request("GET", "dns", "query", query={"name": name.strip(), "type": record_type})
        elif tool in ("flushDNS", "flushFakeIP"):
            result = self._request("POST", "cache", "dns" if tool == "flushDNS" else "fakeip", "flush")
        elif tool == "closeConnections":
            result = self._request("DELETE", "connections")
        else:
            raise ControllerError(tr("不支持的核心工具。"))
        return json.dumps(result, ensure_ascii=False, indent=2) if result is not None else tr("操作已完成。")


class PreviewController:
    def __init__(self):
        self.endpoint = tr("演示模式 · 不连接核心")
        self._data = json.loads(files("zfly_runtime").joinpath("preview.json").read_text(encoding="utf-8"))

    def snapshot(self):
        return copy.deepcopy(self._data)

    def configuration(self):
        return copy.deepcopy(self._data["configs"])

    def configure(self, draft, baseline):
        patch = config_changes(draft, baseline)
        if any(self._data["configs"].get(key) != baseline.get(key) for key in patch):
            raise ControllerError(tr("正在编辑的设置已被其他界面修改，请重新载入。"))
        validate_config({**self._data["configs"], **patch})
        self._data["configs"].update(patch)

    def select(self, group, proxy):
        item = self._data["proxies"]["proxies"][group]
        item["now"] = proxy
        if item["type"] in ("URLTest", "Fallback"):
            item["fixed"] = proxy

    def unfix(self, group):
        self._data["proxies"]["proxies"][group]["fixed"] = ""

    def delay(self, proxy):
        history = self._data["proxies"]["proxies"][proxy].get("history") or []
        return history[-1]["delay"] if history else 42

    def close(self, connection):
        items = self._data["connections"]["connections"]
        self._data["connections"]["connections"] = [item for item in items if item["id"] != connection]

    def inspect(self, tool, name="", record_type="A"):
        if tool not in ("rules", "dnsQuery", "flushDNS", "flushFakeIP", "closeConnections"):
            raise ControllerError(tr("不支持的核心工具。"))
        if tool == "dnsQuery" and (not name.strip() or record_type not in ("A", "AAAA", "CNAME", "MX", "TXT", "NS", "HTTPS")):
            raise ControllerError(tr("请输入域名并选择查询类型。"))
        if tool == "rules":
            return json.dumps({"rules": [{"type": "Match", "payload": "", "proxy": "DIRECT"}]}, indent=2)
        if tool == "dnsQuery":
            return json.dumps({"Answer": [{"name": name, "type": record_type, "data": "192.0.2.1"}]}, indent=2)
        if tool == "closeConnections":
            self._data["connections"]["connections"] = []
        return tr("演示操作已完成。")


def config_changes(draft, baseline):
    return {key: value for key, value in draft.items()
            if key in CONFIG_KEYS and value is not None and value != baseline.get(key)}


def validate_config(config):
    try:
        validate_core_config(config)
    except RuntimeErrorMessage as error:
        raise ControllerError(tr(str(error))) from None
