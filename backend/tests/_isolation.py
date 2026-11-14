"""离线测试不写进项目 data/：TestClient 会触发 lifespan 开始写日志文件和聊天存档，这里先把目录指到临时文件夹。"""

import os
import tempfile

TEMP_ROOT = tempfile.mkdtemp(prefix="doghunt-test-logs-")
os.environ["DOGHUNT_LOG_DIR"] = os.path.join(TEMP_ROOT, "logs")
os.environ["DOGHUNT_TRACE_DIR"] = os.path.join(TEMP_ROOT, "traces")
