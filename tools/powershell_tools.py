import os

from utils.mcp_util import create_mcp_stdio_client
from pathlib import Path
import sys

# 模块级全局变量
_powershell_client = None

async def get_stdio_powershell_tools():
    global _powershell_client
    # ⭐ 1. 计算一个稳定的项目根，作为 MCP server 的 cwd
    #    文件用 __file__ 反推，避免硬编码
    #    当前目录结构：<项目根>/tools/powershell_tools.py
    #    parents[0]=tools，parents[1]=<项目根>
    #    （旧代码按 app/AI_Agent/tools 层级取 parents[3]，在当前结构下会错误指向 D:\，已修正）
    project_root = Path(__file__).resolve().parents[1]

    # 如果结构和这个假设不一致，就先 print 出来确认一下：
    print("project_root =", project_root, file=sys.stderr)

    # ⭐ 2. MCP server 脚本路径，也用 Path 拼，别硬编码
    server_script = project_root / "mcp_tools" / "powershell_tools.py"

    params = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(server_script)],
        "cwd": str(project_root),  # ⭐ 3. 关键：统一 cwd
        # 可选：确保子进程无缓冲，日志能及时出来
        "env": {
            **os.environ,
            "PYTHONPATH": str(project_root),  # 保证 MCP 服务端子进程能 import 项目模块
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    }
    # params = {
    #     "transport": "stdio",
    #     "command": sys.executable,
    #     "args": [r"D:\Python\ai-agent-test\app\AI_Agent\mcp_tools\powershell_tools.py"]
    # }

    client, tools = await create_mcp_stdio_client("powershell_tools", params)
    _powershell_client = client  # ← 保留引用，防止被 GC
    return tools