"""离线测试的隔离，必须在任何 app 模块导入前 import（每个测试文件第一行）。

- TestClient 会触发 lifespan 开始写日志文件和聊天存档，这里先把目录指到临时文件夹，不写进项目 data/。
- Settings 会读 backend/.env，而环境变量优先于 .env：这里强制把数据库换成临时 SQLite，关掉启动连库检查，
  嵌入和向量库用假实现，记忆召回、写入、维护全关（开发文档第 13.5 节）。这样本机 .env 里写了真实库和
  DOGHUNT_MEMORY_MODE=on，离线测试也不会连真实库或加载模型。召回相关测试在用例里单独打开。
"""

import os
import sys
import tempfile
from pathlib import Path

TEMP_ROOT = tempfile.mkdtemp(prefix="doghunt-test-logs-")
TEST_DATABASE_PATH = Path(TEMP_ROOT) / "test.db"
TEST_DATABASE_URL = f"sqlite:///{TEST_DATABASE_PATH}"

FORCED_ENV = {
    "DOGHUNT_LOG_DIR": os.path.join(TEMP_ROOT, "logs"),
    "DOGHUNT_TRACE_DIR": os.path.join(TEMP_ROOT, "traces"),
    "DOGHUNT_DATABASE_URL": TEST_DATABASE_URL,
    "DOGHUNT_DB_STARTUP_CHECK": "false",
    "DOGHUNT_EMBEDDING_PROVIDER": "fake",
    "DOGHUNT_VECTORSTORE_PROVIDER": "fake",
    "DOGHUNT_MEMORY_MODE": "off",
    "DOGHUNT_MEMORY_WRITE": "false",
    "DOGHUNT_MEMORY_MAINTENANCE_ENABLED": "false",
}
os.environ.update(FORCED_ENV)

# 万一有别的测试先导入了 app 并缓存了配置或 engine，清掉，让后面读到上面的测试值。
if "app.core.config" in sys.modules:
    sys.modules["app.core.config"].get_settings.cache_clear()
if "app.db.session" in sys.modules:
    sys.modules["app.db.session"].dispose_engine()
