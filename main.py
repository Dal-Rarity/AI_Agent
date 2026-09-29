"""
AI_Agent 项目 —— 统一程序主入口
================================

项目中的智能体形态：
    1. unified（默认）：统一三专家多智能体
       Supervisor + research_expert / code_expert / infra_expert
       详见 agent/unified_multi_agent.py
    2. multi：原始双专家多智能体
       Supervisor + research_expert / code_generator
       详见 agent/langgraph_code_agent.py
    3. single：原始单智能体（全部工具交给一个 agent）
       详见 agent/code_agent.py

常用命令：
    python main.py                          # 默认启动 unified 模式
    python main.py --mode unified           # 三专家多智能体
    python main.py --mode multi            # 双专家多智能体
    python main.py --mode single           # 单智能体
    python main.py --thread-id 10          # 指定会话线程 ID（用于恢复/隔离对话记忆）
    python main.py --help                  # 查看参数说明
"""

import argparse
import asyncio
import importlib


# ==================== 运行模式注册表 ====================
# 每条记录：(模式名, 模块路径, run_agent 是否接收 thread_id 参数, 模式说明)
# 后续新增智能体形态时，只需在此注册一行，无需改动启动逻辑
MODE_REGISTRY = {
    "unified": (
        "agent.unified_multi_agent",
        True,
        "统一三专家多智能体（research/code/infra + supervisor）【默认】",
    ),
    "multi": (
        "agent.langgraph_code_agent",
        False,
        "双专家多智能体（research_expert + code_generator）",
    ),
    "single": (
        "agent.code_agent",
        False,
        "单智能体（PowerShell/浏览器/RAG/虚拟机/MySQL 全工具）",
    ),
}


def parse_args():
    """解析命令行参数：运行模式 + 会话线程 ID。"""
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="AI_Agent 统一主入口：按模式启动不同形态的智能体系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=list(MODE_REGISTRY.keys()),
        default="unified",
        help="智能体运行模式（默认：unified）",
    )
    parser.add_argument(
        "--thread-id",
        default="4",
        help="会话线程 ID，相同 ID 可恢复历史对话上下文（默认：4，仅 unified 模式生效）",
    )
    return parser.parse_args()


def main():
    """主入口：解析参数 → 延迟加载对应智能体模块 → 启动异步事件循环。"""
    args = parse_args()

    module_path, accept_thread_id, desc = MODE_REGISTRY[args.mode]
    print(f"启动模式：{args.mode} —— {desc}")

    # 延迟导入：避免在仅查看 --help 时就初始化大模型、拉起 MCP 子进程
    module = importlib.import_module(module_path)
    run_agent = getattr(module, "run_agent", None)
    if run_agent is None:
        raise AttributeError(f"模块 {module_path} 中未找到 run_agent 函数")

    # unified 模式支持自定义 thread_id；历史脚本的 run_agent 不接收参数，按原签名调用
    if accept_thread_id:
        asyncio.run(run_agent(thread_id=args.thread_id))
    else:
        asyncio.run(run_agent())


if __name__ == "__main__":
    main()
