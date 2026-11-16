"""持久化表模型；migrations/env.py 导入本包，让 Alembic 看到所有表。

当前有长期记忆的五张表（memory.py）；预测记录（P8）以后再加。
"""

from app.models.memory import MemoryChild, MemoryEvent, MemoryExtraction, MemoryParent, MemoryUsage

__all__ = ["MemoryChild", "MemoryEvent", "MemoryExtraction", "MemoryParent", "MemoryUsage"]
