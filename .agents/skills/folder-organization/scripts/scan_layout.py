"""扫描仓库目录，找出平铺过多、版本系列混放、记录与内容混放的目录。

只读，不移动任何文件。用法（在仓库任意位置运行）：

    python3 .agents/skills/folder-organization/scripts/scan_layout.py [路径 ...]
        [--max-files 12] [--min-series 3] [--max-dated 50]

有 Git 时只看已跟踪和未忽略的文件；没有 Git 时遍历目录并跳过常见依赖与产物目录。
"""

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".next", ".nuxt", "coverage", "htmlcov",
}
# 目录根部的入口与索引文件，不计入“平铺过多”
ENTRY_FILES = {"readme.md", "index.md", "__init__.py", "index.ts", "index.js", ".gitkeep", ".gitignore"}
RECORD_STEMS = {"changelog", "issues", "history", "todo", "notes", "decisions"}
SERIES = re.compile(r"^(?P<stem>.+?)[_-]v(?P<n>\d+)(?P<ext>(\.[^./]+)*)$", re.IGNORECASE)
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}")


def list_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--full-name", "--cached", "--others", "--exclude-standard", "-z"],
            check=True, capture_output=True,
        ).stdout
        top = Path(subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip())
        files = [top / p for p in out.decode("utf-8").split("\0") if p]
        return [f for f in files if f.exists()]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            files.extend(Path(dirpath) / n for n in filenames)
        return files


def analyse(directory: Path, names: list[str], args) -> list[str]:
    findings = []
    counted = [n for n in names if n.lower() not in ENTRY_FILES]
    series: dict[str, list[int]] = defaultdict(list)
    for n in names:
        m = SERIES.match(n)
        if m:
            series[m["stem"] + m["ext"]].append(int(m["n"]))
    big_series = {k: sorted(v) for k, v in series.items() if len(v) >= args.min_series}
    records = [n for n in names if Path(n).stem.lower() in RECORD_STEMS]
    dated = [n for n in names if DATED.match(n)]

    mostly_dated = counted and len(dated) >= 0.8 * len(counted)
    if len(counted) > args.max_files and not mostly_dated:
        findings.append(f"一层平铺 {len(counted)} 个文件（阈值 {args.max_files}，不含 README/索引）")
    if big_series and (len(series) > 1 or len(counted) > len(next(iter(big_series.values())))):
        desc = "，".join(f"{k} v{v[0]}–v{v[-1]}（{len(v)} 个）" for k, v in sorted(big_series.items()))
        findings.append(f"版本系列与其他文件混放：{desc}")
    if records and len(counted) - len(records) >= args.max_files // 2:
        findings.append(f"记录类文件与内容混放：{'、'.join(sorted(records))}")
    if len(dated) > args.max_dated:
        findings.append(f"按日期命名的文件 {len(dated)} 个（阈值 {args.max_dated}），可考虑按月分组")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=["."], help="只报告这些路径下的目录")
    parser.add_argument("--max-files", type=int, default=12)
    parser.add_argument("--min-series", type=int, default=3)
    parser.add_argument("--max-dated", type=int, default=50)
    args = parser.parse_args()

    scopes = [Path(p).resolve() for p in args.paths]
    by_dir: dict[Path, list[str]] = defaultdict(list)
    for scope in scopes:
        for f in list_files(scope):
            f = f.resolve()
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            if any(f == s or s in f.parents for s in scopes):
                by_dir[f.parent].append(f.name)

    cwd = Path.cwd()
    hits = 0
    for directory in sorted(by_dir):
        findings = analyse(directory, sorted(set(by_dir[directory])), args)
        if not findings:
            continue
        hits += 1
        try:
            shown = directory.relative_to(cwd)
        except ValueError:
            shown = directory
        print(f"{shown}/")
        for line in findings:
            print(f"  - {line}")
    if hits == 0:
        print("没有发现超出阈值的目录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
