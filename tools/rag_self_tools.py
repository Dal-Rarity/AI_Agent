import os

from utils.mcp_util import create_mcp_stdio_client
from pathlib import Path
import sys

# 模块级全局变量
rag_self_client = None

async def get_stdio_rag_self_tools():
    global rag_self_client
    # 当前目录结构：<项目根>/tools/rag_self_tools.py，parents[1]=<项目根>
    # （旧代码取 parents[3] 并拼接 "AI_Agent" 子目录，在当前结构下指向错误，已修正）
    project_root = Path(__file__).resolve().parents[1]

    # 如果结构和这个假设不一致，就先 print 出来确认一下：
    print("project_root =", project_root, file=sys.stderr)

    # 注意：必须以模块方式启动（python -m rag.self_rag），不能直接运行脚本。
    # 因为脚本目录 rag/ 内恰好存在 rag.py，直接运行时脚本目录会排在 sys.path 最前，
    # 导致 import rag 被解析成 rag.py（报 "'rag' is not a package"）；
    # 而 -m 方式下 sys.path[0] 是 cwd（项目根），能正确解析到 rag 包。
    params = {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "rag.self_rag"],
        "cwd": str(project_root),  # ⭐ 3. 关键：统一 cwd
        "env": {
            **os.environ,
            # 必需：self_rag.py 服务端内部有 from rag.rag import ...，没有 PYTHONPATH 会启动失败
            "PYTHONPATH": str(project_root),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    }

    client, tools = await create_mcp_stdio_client("rag_self_tools", params)
    rag_self_client = client  # ← 保留引用，防止被 GC
    return tools