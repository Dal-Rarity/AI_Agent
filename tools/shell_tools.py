import os
import sys
from pathlib import Path

from utils.mcp_util import create_mcp_stdio_client


async def get_stdio_shell_tools():
    # 当前目录结构：<项目根>/tools/shell_tools.py，parents[1]=<项目根>
    project_root = Path(__file__).resolve().parents[1]

    # MCP server 脚本路径：mcp_tools/shell_tools.py（动态解析，避免硬编码绝对路径）
    server_script = project_root / "mcp_tools" / "shell_tools.py"

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

    client, tools = await create_mcp_stdio_client("shell_tools", params)

    return tools
