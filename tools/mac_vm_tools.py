import os

from utils.mcp_util import create_mcp_stdio_client
from pathlib import Path
import sys

# 模块级全局变量
mac_vm_client = None

async def get_stdio_mac_vm_tools():
    global mac_vm_client
    # 当前目录结构：<项目根>/tools/mac_vm_tools.py，parents[1]=<项目根>
    project_root = Path(__file__).resolve().parents[1]

    # 如果结构和这个假设不一致，就先 print 出来确认一下：
    print("project_root =", project_root, file=sys.stderr)

    # ⭐ 2. MCP server 脚本路径，也用 Path 拼，别硬编码
    #    mcp_tools 目录下的实际文件名是 mac_vm.py（旧代码误写为 mac_vm_tools.py，已修正）
    server_script = project_root / "mcp_tools" / "mac_vm.py"

    params = {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(server_script)],
        "cwd": str(project_root),  # ⭐ 3. 关键：统一 cwd
        "env": {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    }

    client, tools = await create_mcp_stdio_client("mac_vm_tools", params)
    mac_vm_client = client  # ← 保留引用，防止被 GC
    return tools