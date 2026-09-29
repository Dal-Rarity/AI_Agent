import os
import sys
from pathlib import Path

from utils.mcp_util import create_mcp_stdio_client


async def get_stdio_terminal_tools():
    # 当前目录结构：<项目根>/tools/terminal_tools.py，parents[1]=<项目根>
    project_root = Path(__file__).resolve().parents[1]

    # MCP server 脚本路径：mcp_tools/terminal_tools.py
    # （旧代码硬编码为 D:\Python\ai-agent-test\app\code_agent\mcp\... 的历史路径，已改为动态解析）
    server_script = project_root / "mcp_tools" / "terminal_tools.py"

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

    client, tools = await create_mcp_stdio_client("terminal_tools", params)

    return tools
