"""记忆命令行：在 backend/ 下执行 `uv run --locked python -m app.services.memory <子命令>`。

  seed --parents   写入固定父块目录（幂等，重复执行不重复插入；make memory-seed 调用这条）
  seed --samples   写入几条开发样例子块，只用于开发和测试；environment=production 时拒绝
  list             只读列出父块和子块（默认不含已忘记的，--all 包含）
  init [--force]   初始化 Milvus 集合与别名（实现见 vector_init.py，P4）

退出码：0 成功；1 数据库不可用、连接串无效或写入冲突；2 参数错误（argparse）。init 的退出码由 run_init 决定。
其他异常（程序错误）照常抛出带堆栈，不伪装成数据库不可用。
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import URL_ERRORS, _short_error, get_engine, register_database_secrets, safe_url, session_scope
from app.repositories import memory as repo

# 开发样例：(父块 key, 标题, 正文, kind, status)。来源统一标“开发样例”，方便识别和清理。
SAMPLES: tuple[tuple[str, str, str, str, str], ...] = (
    ("output_pref", "先给结论，再讲原因", "回答时先用一两句话给出两个玩法各自的结论，再展开依据和不确定因素。", "explicit", "active"),
    ("output_pref", "赔率用十进制显示", "赔率一律写成十进制，例如 1.90；不要换算成其他格式。", "explicit", "active"),
    ("method_pref", "只看全场让分和大小分", "默认只分析赛前全场让分与全场大小分，不展开半场、单节和球员个人数据。", "explicit", "active"),
    ("review_notes", "让分一分以内的比赛观望偏少", "旧策略在盘口 ±1 以内的比赛里给方向的次数偏多，待更多样本验证。", "ai", "candidate"),
)
SAMPLE_SOURCE = ("maintenance", "dev-sample", "开发样例")


def _fail(exc: BaseException) -> int:
    url = safe_url(get_settings().database_url.get_secret_value())
    print(f"数据库操作失败（{url}）：{_short_error(exc)}", file=sys.stderr)
    print("先确认 make storage-up、make db-migrate 已执行。", file=sys.stderr)
    return 1


def seed_parents_command() -> int:
    with session_scope() as session:
        result = repo.seed_parents(session)
    print(f"父块目录：新建 {len(result.created)}，更新标题 {len(result.updated)}，未变 {len(result.unchanged)}")
    for label, keys in (("新建", result.created), ("更新", result.updated)):
        if keys:
            print(f"  {label}：{', '.join(keys)}")
    return 0


def seed_samples_command() -> int:
    if get_settings().environment == "production":
        print("开发样例只用于开发和测试，environment=production 时不写入。", file=sys.stderr)
        return 1
    created = skipped = 0
    with session_scope() as session:
        repo.seed_parents(session)  # 样例要挂在父块下，先保证目录存在
        for key, title, body, kind, status in SAMPLES:
            parent = repo.get_parent_by_key(session, key)
            content_hash = repo.child_content_hash(title, body, "global", None)
            if any(row.status in ("active", "candidate") for row in repo.find_by_content_hash(session, parent.id, content_hash)):
                skipped += 1
                continue
            kind_source, ref, label = SAMPLE_SOURCE
            repo.insert_child(session, parent_id=parent.id, title=title, body=body, kind=kind, status=status,
                              source_kind=kind_source, source_ref=ref, source_label=label)
            created += 1
    print(f"开发样例：新建 {created}，已存在跳过 {skipped}（只用于开发/测试）")
    return 0


def list_command(include_forgotten: bool) -> int:
    with session_scope() as session:
        parents = repo.list_parents(session)
        children = repo.list_children(session, include_forgotten=include_forgotten, limit=repo.MAX_CHILD_LIMIT)
        by_parent: dict[str, list] = {}
        for child in children:
            by_parent.setdefault(child.parent_id, []).append(child)
        if not parents:
            print("还没有父块；先执行 seed --parents。")
        for parent in parents:
            rows = by_parent.get(parent.id, [])
            print(f"[{parent.key}] {parent.title}（{len(rows)} 条，向量 {parent.embed_state}）")
            for child in rows:
                forgotten = "，已忘记" if child.forgotten_at else ""
                print(f"  {child.id}  {child.status}/{child.kind}{forgotten}  {child.title}")
    return 0


def init_command(args: argparse.Namespace) -> int:
    # 懒加载：Milvus 依赖只在执行 init 时导入，seed/list 不需要 pymilvus。
    from app.services.memory.vector_init import run_init

    return run_init(force=args.force)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.services.memory", description="长期记忆的维护命令")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="写入固定父块目录或开发样例（幂等）")
    seed.add_argument("--parents", action="store_true", help="写入固定父块目录")
    seed.add_argument("--samples", action="store_true", help="写入开发样例子块（只用于开发/测试）")

    listing = sub.add_parser("list", help="只读列出父块和子块")
    listing.add_argument("--all", dest="include_forgotten", action="store_true", help="包含已忘记的条目")

    init = sub.add_parser("init", help="初始化 Milvus 集合与别名（P4）")
    init.add_argument("--force", action="store_true", help="见 vector_init.run_init 的说明")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    register_database_secrets()  # 先登记数据库密码，之后打印的驱动错误都经 applog.redact 脱敏
    if args.command == "init":
        return init_command(args)
    if args.command == "seed" and not (args.parents or args.samples):
        parser.error("seed 需要 --parents 或 --samples")
    try:
        get_engine()
    except URL_ERRORS as exc:
        return _fail(exc)
    try:
        if args.command == "seed":
            code = seed_parents_command() if args.parents else 0
            if code == 0 and args.samples:
                code = seed_samples_command()
            return code
        return list_command(args.include_forgotten)
    except (*repo.DATABASE_UNAVAILABLE_ERRORS, IntegrityError) as exc:
        return _fail(exc)


if __name__ == "__main__":
    sys.exit(main())
