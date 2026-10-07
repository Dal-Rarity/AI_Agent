import os
from functools import wraps

from langchain_community.agent_toolkits.file_management import FileManagementToolkit

# 文件工具根目录：默认 D 盘根目录，可用环境变量 AGENT_FILE_ROOT 覆盖。
# 用户本来就持有无限制的 PowerShell 终端工具，再把文件工具锁死在 .temp 只是形式主义。
_FILE_ROOT = os.environ.get("AGENT_FILE_ROOT", r"D:\\")
file_tools = FileManagementToolkit(root_dir=_FILE_ROOT).get_tools()


# 配置文件保护（与 mcp_tools/powershell_tools.py 的命令行守卫同口径）：
# 回归场景 33 实证模型会自行改 .env 的 MYSQL_PORT 造成数据源漂移，
# 写类文件工具命中受保护配置时直接拒绝。
_PROTECTED_CONFIG_NAMES = {".env", ".env.example"}
_PROTECTED_WRITE_TOOLS = {"write_file", "copy_file", "move_file"}


def _is_protected_config(path: str) -> bool:
    if not isinstance(path, str):
        return False
    base = os.path.basename(path.strip().rstrip("/\\"))
    return base.lower() in _PROTECTED_CONFIG_NAMES


for _t in file_tools:
    if _t.name in _PROTECTED_WRITE_TOOLS:
        _orig_run = _t._run

        @wraps(_orig_run)
        def _guarded_run(*args, __orig=_orig_run, **kwargs):
            for _v in (*args, *kwargs.values()):
                if _is_protected_config(_v):
                    return (
                        "🚫 配置文件保护：.env/.env.example 是运行环境配置，"
                        "禁止专家通过文件工具修改或删除（防止数据源漂移、凭据失效）。"
                        "如需变更连接配置，请由人工处理。"
                    )
            return __orig(*args, **kwargs)

        _t._run = _guarded_run


# 项目根目录：只读工具集（read_file / list_directory / file_search）。
# 供 code_expert 直接读取 .code 等项目源码——这是"分析前端格式"类任务的正规通道，
# 没有它时专家只能借 PowerShell 逐层试探目录，几十次调用就烧光步数预算。
# 工具名统一加 project_ 前缀，避免与 file_tools 的同名工具冲突。
project_read_tools = FileManagementToolkit(
    root_dir=r"D:\Python\AI_Agent",
    selected_tools=["read_file", "list_directory", "file_search"],
).get_tools()
for _t in project_read_tools:
    _t.name = f"project_{_t.name}"
    _t.description = f"【项目只读】{_t.description}"
