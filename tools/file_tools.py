import os

from langchain_community.agent_toolkits.file_management import FileManagementToolkit

# 文件工具根目录：默认 D 盘根目录，可用环境变量 AGENT_FILE_ROOT 覆盖。
# 用户本来就持有无限制的 PowerShell 终端工具，再把文件工具锁死在 .temp 只是形式主义。
_FILE_ROOT = os.environ.get("AGENT_FILE_ROOT", r"D:\\")
file_tools = FileManagementToolkit(root_dir=_FILE_ROOT).get_tools()

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
