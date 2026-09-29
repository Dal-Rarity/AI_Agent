import os
from pathlib import Path
import sys

from utils.mcp_util import create_mcp_stdio_client

mysql_client = None


async def get_stdio_mysql_tools():
    global mysql_client
    # 当前目录结构：<项目根>/tools/mysql_tools.py，parents[1]=<项目根>
    project_root = Path(__file__).resolve().parents[1]

    print("project_root =", project_root, file=sys.stderr)

    # 服务端脚本路径：mysql_tools.py
    server_script = project_root / "mcp_tools" / "mysql_tools.py"

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

    client, tools = await create_mcp_stdio_client("mysql_tools", params)
    mysql_client = client
    return tools