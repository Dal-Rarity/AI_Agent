"""
把 win_vm.py 作为 stdio MCP 服务启动，供 AI_Agent.py 调用
"""
import os
from pathlib import Path
import sys

from utils.mcp_util import create_mcp_stdio_client

_win_vm_client = None


async def get_stdio_win_vm_tools():
    global _win_vm_client
    # 当前目录结构：<项目根>/tools/win_vm_tools.py，parents[1]=<项目根>
    project_root = Path(__file__).resolve().parents[1]

    print("project_root =", project_root, file=sys.stderr)

    # 服务端脚本路径：win_vm.py
    server_script = project_root / "mcp_tools" / "win_vm.py"

    params = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(server_script)],
        "cwd": str(project_root),
        "env": {
            **os.environ,
            "PYTHONPATH": str(project_root),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    }

    client, tools = await create_mcp_stdio_client("win_vm_tools", params)
    _win_vm_client = client
    return tools