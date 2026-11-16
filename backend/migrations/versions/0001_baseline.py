"""基线：空结构，只确立迁移链起点；第一版记忆表在 P3 的下一个版本里加。

Revision ID: 0001
Revises:
Create Date: 2026-11-16
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
