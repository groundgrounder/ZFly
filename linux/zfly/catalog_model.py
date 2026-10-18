import copy
import json
import uuid

from .controller import ControllerError

CATALOG_KINDS = {"proxies": "自定义节点", "groups": "自定义代理组"}
TEMPLATES = {
    "proxies": {"name": "新节点", "type": "socks5", "server": "127.0.0.1", "port": 1080},
    "groups": {"name": "新代理组", "type": "select", "proxies": ["DIRECT"]},
}


class PreviewCatalog:
    def __init__(self):
        self.records = {}
        self.versions = {}

    def perform(self, command, state):
        kind, identity = command.get("section"), command.get("id")
        if kind not in CATALOG_KINDS:
            raise ControllerError("不支持的资源分类。")
        if kind == "groups" and not any(p["id"] == command.get("name") and p["type"] == "custom" for p in state["profiles"]):
            raise ControllerError("请先选择自定义配置。")
        scope = kind + ":" + (command.get("name", "") if kind == "groups" else "")
        records = self.records.setdefault(scope, {})
        if identity is not None and identity not in records:
            raise ControllerError("资源已被删除。")
        document = records[identity] if identity is not None else {
            "section": kind, "title": CATALOG_KINDS[kind], "text": json.dumps(TEMPLATES[kind], ensure_ascii=False, indent=2),
            "revision": f"new:{scope}:{self.versions.get(scope, 0)}", "writable": True, "format": "json",
        }
        action = command["action"]
        if action == "catalog.read":
            return {"state": copy.deepcopy(state), "document": copy.deepcopy(document)}
        if action in ("catalog.save", "catalog.delete"):
            if document["revision"] != command.get("revision"):
                raise ControllerError("资源已修改，请重新载入。")
            if action == "catalog.delete":
                if identity is None:
                    raise ControllerError("请先选择资源。")
                del records[identity]
            else:
                try:
                    value = json.loads(command["text"])
                except (ValueError, KeyError, TypeError):
                    raise ControllerError("JSON 格式无效。") from None
                name_key = "name"
                if not isinstance(value, dict) or not isinstance(value.get(name_key), str) or not value[name_key].strip():
                    raise ControllerError("资源需要有效的 JSON 对象和名称。")
                if any(json.loads(doc["text"])[name_key] == value[name_key] for key, doc in records.items() if key != identity):
                    raise ControllerError("名称已存在。")
                records[identity or str(uuid.uuid4())] = {**document, "text": command["text"], "revision": str(uuid.uuid4())}
            self.versions[scope] = self.versions.get(scope, 0) + 1
        elif action != "catalog.list":
            raise ControllerError("不支持的资源操作。")
        items = []
        for key, doc in records.items():
            value = json.loads(doc["text"])
            items.append({"id": key, "label": value["name"], "detail": value.get("type", "本地资源")})
        return {"state": copy.deepcopy(state), "items": sorted(items, key=lambda item: item["label"])}
