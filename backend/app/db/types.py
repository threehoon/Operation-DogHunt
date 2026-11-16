"""统一的 UTC 时间列：SQLite 读回的 DateTime 不带时区，Postgres 带；两边都转成带 UTC 时区的值。"""

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """写入时要求带时区并转 UTC；读出时补上 UTC。不接受不带时区的时间，避免把本地时间当成 UTC 存。"""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"UTCDateTime 只接受 datetime，收到 {type(value).__name__}")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime 需要带时区的时间（例如 datetime.now(UTC)）")
        value = value.astimezone(UTC)
        # SQLite 没有时区列，存成不带时区的 UTC 文本；读回时在 process_result_value 补上。
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
