"""从环境变量或项目根目录 .env.local 读取供应商 Key。Key 不进入日志和接口响应。"""

import os

from app.integrations.odds import PROJECT_ROOT

PLACEHOLDERS = {"YOUR_API_KEY", "YOUR_KEY"}


def read_key(name: str) -> str | None:
    """没配置返回 None；配置了但含空白字符抛 ValueError，由调用方转成自己的错误。"""
    key = os.environ.get(name, "").strip()
    config = PROJECT_ROOT / ".env.local"
    if not key and config.exists():
        for line in config.read_text(encoding="utf-8").splitlines():
            field, sep, value = line.strip().partition("=")
            if sep and field.strip() == name:
                key = value.strip()
                if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
                    key = key[1:-1]
                break
    if not key or key in PLACEHOLDERS:
        return None
    if any(c.isspace() for c in key):
        raise ValueError(f".env.local 中的 {name} 含空白字符，请检查。")
    return key
