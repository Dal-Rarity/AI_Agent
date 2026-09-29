import os

from utils.mcp_util import create_mcp_stdio_client
from pathlib import Path
import sys

# 模块级全局变量
_rag_client = None

async def get_stdio_rag_tools():
    global _rag_client
    # 当前目录结构：<项目根>/tools/rag_tools.py，parents[1]=<项目根>
    project_root = Path(__file__).resolve().parents[1]

    # 如果结构和这个假设不一致，就先 print 出来确认一下：
    print("project_root =", project_root, file=sys.stderr)

    # ⭐ 2. MCP server 脚本路径，也用 Path 拼，别硬编码
    server_script = project_root / "rag" / "rag.py"

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

    client, tools = await create_mcp_stdio_client("rag_tools", params)
    _rag_client = client  # ← 保留引用，防止被 GC
    return tools