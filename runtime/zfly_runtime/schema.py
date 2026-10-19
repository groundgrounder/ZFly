"""Core fields shared by the runtime and native clients."""
from .storage import RuntimeErrorMessage

NETWORK_CONFIG_FIELDS = {
    "mode": "mode", "mixedPort": "mixed-port", "ipv6": "ipv6",
    "allowLan": "allow-lan", "tcpConcurrent": "tcp-concurrent", "logLevel": "log-level",
}
CONFIG_CHOICES = {
    "mode": ["rule", "global", "direct"],
    "log-level": ["silent", "error", "warning", "info", "debug"],
    "find-process-mode": ["off", "strict", "always"],
}
CONFIG_FLAGS = ["ipv6", "allow-lan", "tcp-concurrent", "sniffing"]
CONFIG_PORTS = ["mixed-port", "port", "socks-port"]
CONFIG_TEXT = ["bind-address"]
CONFIG_KEYS = set(CONFIG_CHOICES) | set(CONFIG_FLAGS + CONFIG_PORTS + CONFIG_TEXT)


def validate_core_config(config):
    used = set()
    for key in CONFIG_PORTS:
        value = config.get(key)
        if value is None:
            continue
        minimum = 1 if key == "mixed-port" else 0
        if type(value) is not int or not minimum <= value <= 65535:
            message = "混合端口应为 1–65535 的整数。" if minimum else "端口应为 0–65535 的整数。"
            raise RuntimeErrorMessage(message)
        if value and value in used:
            raise RuntimeErrorMessage("监听端口不能重复。")
        used.add(value)
    for key, choices in CONFIG_CHOICES.items():
        if config.get(key) is not None and config[key] not in choices:
            raise RuntimeErrorMessage("不支持的核心设置：" + key)
    for key in CONFIG_FLAGS:
        if config.get(key) is not None and type(config[key]) is not bool:
            raise RuntimeErrorMessage("开关值无效。")
    for key in CONFIG_TEXT:
        value = config.get(key)
        if value is not None and (not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise RuntimeErrorMessage("接口和监听地址不能包含控制字符。")
    bind = config.get("bind-address")
    if bind is not None and (not bind or any(c.isspace() for c in bind)):
        raise RuntimeErrorMessage("监听地址不能为空或包含空白。")
