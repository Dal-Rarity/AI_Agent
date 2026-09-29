"""
代码备份脚本（项目级备份机制）
================================

作用：
    在对 AI_Agent 项目做任何代码修改之前，先执行本脚本，对项目源码做一次
    完整快照备份，便于在修改出现问题时快速回滚。

备份产物：
    backups/backup_<时间戳>_v<版本号>/
        ├── MANIFEST.txt   备份清单（备份时间、项目版本、文件列表、大小、SHA256）
        └── ...            与项目源码目录结构一致的副本

命名规则：
    时间戳：本地时间 %Y%m%d_%H%M%S（例如 20260927_153000）
    版本号：自动读取 pyproject.toml 中的 project.version（例如 0.1.0）

用法：
    python scripts/backup.py                # 执行一次完整备份
    python scripts/backup.py --note "重构多智能体前备份"   # 带备注的备份

注意：
    1. 备份只包含源码与配置，不含 .venv / __pycache__ / .temp / backups 自身；
    2. .env 含密钥信息，仅在本机 backups 目录内留存，请勿外传。
"""

import argparse
import hashlib
import shutil
import tomllib
from datetime import datetime
from pathlib import Path

# ==================== 备份配置 ====================

# 脚本位于 <项目根>/scripts/backup.py，因此 parents[1] 即项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 备份输出根目录
BACKUP_ROOT = PROJECT_ROOT / "backups"

# 需要备份的源码目录
BACKUP_DIRS = ["agent", "mcp_tools", "model", "prompts", "rag", "scripts", "tools", "utils"]

# 需要备份的根目录文件（main.py 等后续新增文件存在时自动纳入）
BACKUP_ROOT_FILES = ["pyproject.toml", "uv.lock", "main.py", ".env"]

# 复制时跳过的目录名 / 后缀名（虚拟环境、缓存、临时产物一律不备份）
SKIP_DIR_NAMES = {"__pycache__", ".venv", ".temp", ".idea", ".code", "backups", ".git"}
SKIP_SUFFIXES = {".pyc"}

# ==================================================


def get_project_version() -> str:
    """读取 pyproject.toml 中的 project.version 作为本次备份的版本号。"""
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def iter_backup_files():
    """
    遍历所有需要备份的文件，产出 (源文件绝对路径, 相对项目根的路径) 元组。
    自动跳过缓存目录、虚拟环境和备份目录自身，避免备份套备份。
    """
    # 1. 遍历配置的源码目录
    for dir_name in BACKUP_DIRS:
        src_dir = PROJECT_ROOT / dir_name
        if not src_dir.is_dir():
            continue
        for path in src_dir.rglob("*"):
            if path.is_dir():
                continue
            # 路径中任意一级命中跳过名单则跳过
            if any(part in SKIP_DIR_NAMES for part in path.relative_to(PROJECT_ROOT).parts):
                continue
            if path.suffix in SKIP_SUFFIXES:
                continue
            yield path, path.relative_to(PROJECT_ROOT)

    # 2. 遍历根目录文件（不存在的自动忽略）
    for file_name in BACKUP_ROOT_FILES:
        path = PROJECT_ROOT / file_name
        if path.is_file():
            yield path, Path(file_name)


def calc_sha256(file_path: Path) -> str:
    """计算文件的 SHA256，写入清单以便后续校验备份完整性。"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def create_backup(note: str = "") -> Path:
    """
    执行一次完整备份。

    :param note: 可选的备份备注（例如修改原因），会写入 MANIFEST.txt
    :return: 本次备份目录的 Path
    """
    # 1. 生成带时间戳与版本号的备份目录名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    version = get_project_version()
    backup_dir = BACKUP_ROOT / f"backup_{timestamp}_v{version}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    # 2. 逐文件复制，同时收集清单信息
    manifest_lines = [
        "AI_Agent 项目代码备份清单",
        "=" * 50,
        f"备份时间      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"项目版本      : v{version}",
        f"项目根目录    : {PROJECT_ROOT}",
        f"备份目录      : {backup_dir}",
        f"备注          : {note or '（无）'}",
        "=" * 50,
        "文件清单（相对路径 | 大小(字节) | SHA256）：",
        "",
    ]

    file_count = 0
    total_size = 0
    for src_path, rel_path in iter_backup_files():
        # 保持与源码一致的目录结构
        dst_path = backup_dir / rel_path
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)  # copy2 保留修改时间等元数据

        size = src_path.stat().st_size
        sha = calc_sha256(src_path)
        manifest_lines.append(f"{rel_path} | {size} | {sha}")

        file_count += 1
        total_size += size

    manifest_lines.extend([
        "",
        "=" * 50,
        f"文件总数      : {file_count}",
        f"总大小(字节)  : {total_size}",
    ])

    # 3. 写入清单文件
    manifest_path = backup_dir / "MANIFEST.txt"
    manifest_path.write_text("\n".join(manifest_lines), encoding="utf-8")

    # 4. 控制台输出备份结果
    print(f"备份完成：{backup_dir}")
    print(f"文件数量：{file_count}，总大小：{total_size / 1024:.1f} KB")
    print(f"清单文件：{manifest_path}")
    return backup_dir


def main():
    """命令行入口：解析备注参数并执行备份。"""
    parser = argparse.ArgumentParser(description="AI_Agent 项目代码备份工具（时间戳 + 版本号）")
    parser.add_argument("--note", default="", help="本次备份的备注说明，例如修改原因")
    args = parser.parse_args()

    create_backup(note=args.note)


if __name__ == "__main__":
    main()
