import os

from utils.mcp_util import create_mcp_stdio_client
from pathlib import Path
import sys

# 模块级全局变量
# （旧代码此处误写为 _powershell_client，且函数内赋值 _browser_client 未声明 global，
#   会导致客户端引用在函数返回后被回收，已修正）
_browser_client = None

async def get_stdio_browser_tools():
    global _browser_client
    # 当前目录结构：<项目根>/tools/browser_tools.py，parents[1]=<项目根>
    project_root = Path(__file__).resolve().parents[1]

    # 如果结构和这个假设不一致，就先 print 出来确认一下：
    print("project_root =", project_root, file=sys.stderr)

    server_script = project_root / "mcp_tools" / "browser_tools.py"

    params = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(server_script)],
        "cwd": str(project_root),  # ⭐ 3. 关键：统一 cwd
        "env": {
            **os.environ,
            "PYTHONPATH": str(project_root),  # 保证 MCP 服务端子进程能 import 项目模块
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    }

    client, tools = await create_mcp_stdio_client("browser_tools", params)
    _browser_client = client  # ← 保留引用，防止被 GC
    return tools