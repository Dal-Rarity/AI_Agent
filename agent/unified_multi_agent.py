"""
统一多智能体协作系统（Supervisor-Worker 模式）
================================================

架构说明：
    将原单智能体（code_agent.py）手中的 6 类工具，按职责拆分给 3 个核心专家，
    再由 1 个 Supervisor（主管）根据用户意图进行语义路由与多轮协作调度。

    ┌──────────────────────────────────────────────┐
    │                 Supervisor                   │
    │        负责理解任务、路由专家、汇总结果        │
    └───────────────────────┬──────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌────────────────┐ ┌────────────────────┐ ┌────────────────────┐
│research_expert │ │   code_expert      │ │   infra_expert     │
│  信息检索专家  │ │   代码工程专家      │ │   运维环境专家      │
│                │ │                    │ │                    │
│ browser 浏览器 │ │ file 文件管理       │ │ mysql 数据库(9个)  │
│ rag_self 知识库│ │ powershell 终端(1个)│ │ win_vm Linux沙盒(7个)│
└────────────────┘ └────────────────────┘ └────────────────────┘

协作机制：
    1. 用户消息先到 Supervisor；
    2. Supervisor 语义判断任务类型，调用 transfer_to_<专家名> 转接；
    3. 专家执行完毕后自动回到 Supervisor；
    4. Supervisor 决定继续派遣其他专家（多轮接力）还是直接汇总答复用户。

记忆机制：
    所有专家共享同一份消息状态（由 create_supervisor 内部管理），
    编译图时统一注入 FileSaver 文件检查点，按 thread_id 持久化多轮对话上下文。

运行方式：
    python main.py --mode unified                # 推荐：通过统一主入口启动
    python agent/unified_multi_agent.py          # 也可以直接运行本文件
"""

import asyncio
import dataclasses
import json
import time

from langchain.agents import create_agent
from langchain.agents.middleware import before_model, wrap_model_call
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph_supervisor import create_supervisor

# ==================== 项目内部依赖 ====================

# 大模型实例（阿里云百炼兼容接口，全系统共用同一个模型配置）
from model.qwen import llm_qwen
# 文件检查点：把多轮对话状态持久化到 .temp/checkpoint 目录
from tools.file_saver import FileSaver
# 本地文件管理工具（FileManagementToolkit）
from tools.file_tools import file_tools, project_read_tools
# 以下均为 MCP stdio 工具的异步加载函数（启动时一次性拉起对应的 MCP 子进程）
from tools.powershell_tools import get_stdio_powershell_tools
from tools.browser_tools import get_stdio_browser_tools
from tools.rag_self_tools import get_stdio_rag_self_tools
from tools.win_vm_tools import get_stdio_win_vm_tools
from tools.mysql_tools import get_stdio_mysql_tools


# ==================== 角色提示词（已迁移至 prompts/unified_prompts.py 统一管理） ====================
from prompts.unified_prompts import (
    RESEARCH_SYSTEM_PROMPT,
    CODE_SYSTEM_PROMPT,
    INFRA_SYSTEM_PROMPT,
    EXPERT_HANDOFF_RULE,
    SUPERVISOR_PROMPT,
)

# ==================== 调试输出（极简模式：一行一条，核心信息优先） ====================

# 输出截断长度：防止单条消息刷屏，只保留最关键的信息
MAX_THINK_CHARS = 80        # AI 思考内容最多显示字符数
MAX_ARGS_CHARS = 60         # 工具参数最多显示字符数
MAX_RESULT_CHARS = 100      # 工具结果最多显示字符数


def _truncate(text: str, max_len: int) -> str:
    """截断文本到 max_len，超出部分用 ... 替代，同时折叠空白字符。"""
    if not isinstance(text, str):
        text = str(text)
    text = " ".join(text.split())  # 折叠换行和多余空格
    return text if len(text) <= max_len else text[:max_len] + "..."


def format_debug_output(step_name: str, content: str, is_tool_call: bool = False) -> None:
    """统一格式化每一步的思考/工具调用/工具结果输出（极简一行模式）。"""
    if is_tool_call:
        print(f"  ↳ {step_name}: {_truncate(content, MAX_RESULT_CHARS)}")
    else:
        print(f"  💭 {step_name}: {_truncate(content, MAX_THINK_CHARS)}")


def _parse_final_report(text: str) -> dict:
    """解析 supervisor 的固定格式最终汇报。

    返回 {status, done[], problems[], advice[]}。
    如果模型没遵守格式（解析不到标记），返回 None，调用方走降级显示。
    """
    import re

    if not text or "【状态】" not in text:
        return None

    # 按标记切分文本
    markers = ["【状态】", "【已完成】", "【问题】", "【建议】"]
    positions = []
    for m in markers:
        idx = text.find(m)
        if idx == -1:
            return None
        positions.append((idx, m))
    positions.sort()

    sections = {}
    for i, (idx, m) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        sections[m] = text[idx + len(m): end].strip()

    # 状态：取第一行，只保留三种合法值之一
    raw_status = sections.get("【状态】", "").split("\n")[0].strip()
    if "成功" in raw_status and "部分" not in raw_status:
        status = "成功"
    elif "部分" in raw_status:
        status = "部分完成"
    elif "失败" in raw_status:
        status = "失败"
    else:
        status = raw_status[:10]  # 未知状态，原样显示

    def _extract_items(s: str) -> list:
        """提取列表项（- 开头的行），每行截断到 60 字符。"""
        items = []
        for line in s.split("\n"):
            line = line.strip().lstrip("-•·").strip()
            if line:
                items.append(_truncate(line, 60))
        return items

    return {
        "status": status,
        "done": _extract_items(sections.get("【已完成】", "")),
        "problems": _extract_items(sections.get("【问题】", "")),
        "advice": _extract_items(sections.get("【建议】", "")),
    }


def _print_final_summary(report: dict, steps: int, elapsed: float) -> None:
    """按统一模板输出最终汇总（成功/失败一目了然）。"""
    icon = {"成功": "✅", "部分完成": "⚠️", "失败": "❌"}.get(report["status"], "📋")

    print("\n" + "=" * 60)
    print(f"{icon} 任务状态：{report['status']}（{steps} 步，{elapsed:.0f}s）")
    print("=" * 60)

    if report["done"]:
        print("\n📌 已完成：")
        for item in report["done"]:
            print(f"   • {item}")

    problems = [p for p in report["problems"] if p and p != "无"]
    if problems:
        print("\n❗ 存在问题：")
        for item in problems:
            print(f"   • {item}")

    if report["advice"]:
        print("\n👉 后续建议：")
        for item in report["advice"]:
            print(f"   • {item}")

    print("\n" + "=" * 60 + "\n")


# ==================== 上下文管理（防止死循环撑爆 LLM 上下文） ====================
# qwen3 兼容模式报错上限：openai.BadRequestError: Range of input length should be [1, 983616]
# 死循环时多轮 handoff pair + 工具结果累积会触顶；这里给 supervisor / 专家各保留 80k
# token 的输入预算，远低于上限又足够保留最近若干轮对话。
MAX_CONTEXT_TOKENS = 80000

# 单条消息字符硬上限：trim_messages 只能整条丢弃消息、不能拆分单条消息，
# 实测一次目录树读取/页面抓取/长命令输出可返回约 30 万字符，单条即可击穿
# 80k token 预算（对应第 3 次工具调用后撑爆 983616 上限的场景），必须先截断再裁剪。
MAX_TOOL_MSG_CHARS = 20000


# ==================== 专家预算三层结构闸（非 prompt 约束，不依赖模型自觉） ====================
# 设计目标：把"无进展的空转"与"productive 的长调用链"区分开——
#   L1 重复闸：同(工具,参数)重复 / 连续报错是空转特征，5~8 次即掐；
#              每次调用都有新参数或新结果的正常链路完全不受影响。
#   L2 派遣闸：硬预算按派遣重置，若主管反复派遣同一专家，总调用量实际无上界
#              （实测正是 50 步递归烧穿的路径）——同专家同轮限派 3 次。
#   L3 总数闸：单次派遣兜底上限。原 15 次把真实长任务也掐死（专家"显得没用"的
#              主因），放宽到 25 次后由 L1/L2 负责精确止损。
MAX_DUPLICATE_TOOL_CALLS = 3            # L1a：一次派遣内同(工具,参数)最大允许出现次数
MAX_CONSECUTIVE_TOOL_ERRORS = 3         # L1b：连续失败工具结果数上限
MAX_DISPATCHES_PER_EXPERT_PER_ROUND = 3  # L2：同一专家单轮最多被成功派遣次数
MAX_TOOL_CALLS_PER_EXPERT = 20          # L3：单次派遣兜底上限（实测 productive 最长链 16）
# L3 软警告线：达到后不立即掐断，而是注入警告给模型一次"收尾窗口"——
# 活已干完的可在此窗口发 [完成]，避免"产物已存在却被误报未完成"；
# 仍无收敛则到 L3 硬线截断。
SOFT_WARN_TOOL_CALLS = MAX_TOOL_CALLS_PER_EXPERT - 4

# 明确会"产出产物"的写操作工具（用于硬截断时判定专家可能已完成任务）。
# 命中且对应工具结果成功 → 合成 [完成-待验证]，由主管派验证单确认，
# 而不是一刀切报 [待办]。
ARTIFACT_TOOL_NAMES = {
    "write_file", "create_file", "write_file_to_vm",
    "mysql_insert_data", "mysql_create_table", "mysql_execute_command",
    "mysql_update_data", "mysql_delete_data",
}

# 专家总数（推导递归上限用；新增专家时同步修改）
N_CORE_EXPERTS = 3

# 单次工具调用在 langgraph-supervisor 子图内实际消耗的超步成本（经验实测值）：
# model 节点 + tools 节点 + 子图状态回同步 = 3，而非直觉上的 2。
# 离线扫描实测 recursion_limit=50 时专家最多 15 次调用，第 16 次必崩。
SUPERSTEPS_PER_TOOL_CALL = 3

# 图递归上限：由闸的阈值【推导】而非拍脑袋——
# 保证在最极端组合（3 专家 × 每专家 3 派遣 × 每派遣 20 调用）下，
# 三层闸的优雅收尾永远先于 GraphRecursionError 触发；递归错误退化为
# "连闸本身都坏了"时才可能触及的远方兜底。
# 额外 +20：主管派遣往返、专家合成消息后的 handoff、主管最终汇总等固定开销。
GRAPH_RECURSION_LIMIT = (
    20
    + SUPERSTEPS_PER_TOOL_CALL * N_CORE_EXPERTS
    * MAX_DISPATCHES_PER_EXPERT_PER_ROUND * MAX_TOOL_CALLS_PER_EXPERT
)


def _is_successful_dispatch(m) -> bool:
    """判断一条 ToolMessage 是否为『主管成功派遣』的回执。

    专家幻觉调用 transfer_to_* 时会得到 Error 回执，其 name 同样以 transfer_to
    开头——若把错误回执也当派遣边界，预算会被清零重计。实测专家因此额外获得
    15 次调用额度，在子图内烧穿 recursion_limit=50 导致整轮 GraphRecursionError。
    成功回执内容固定为 "Successfully transferred to ..."（langgraph_supervisor 生成），
    错误回执以 "Error:" 开头，用内容区分两者。
    """
    if not (isinstance(m, ToolMessage) and (getattr(m, "name", "") or "").startswith("transfer_to")):
        return False
    return "successfully" in str(getattr(m, "content", "")).lower()


def _dispatch_start(messages) -> int:
    """本次派遣的起点：最后一条【成功】派遣回执 ToolMessage 的下一条位置。"""
    for i in range(len(messages) - 1, -1, -1):
        if _is_successful_dispatch(messages[i]):
            return i + 1
    return 0


def _calls_since_last_dispatch(messages) -> int:
    """只统计【本次派遣】之后发生的工具调用数，而不是整段会话历史的累计。

    专家被 supervisor 派遣时，状态里会出现一对 transfer_to_* 消息
    （AIMessage 发起 + ToolMessage 确认），最后一条【成功】的派遣回执
    即本次派遣的起点；起点之前的工具调用属于历史轮次，不计入本次预算。
    这样每次派遣都获得全新的 MAX_TOOL_CALLS_PER_EXPERT 次额度，
    复杂任务可靠多次派遣推进，不会因历史累计而永久跳闸。
    """
    start = _dispatch_start(messages)
    return sum(
        len(getattr(m, "tool_calls", None) or [])
        for m in messages[start:]
        if isinstance(m, AIMessage)
    )


def _duplicate_tool_call_count(messages) -> int:
    """一次派遣内同（工具, 参数）重复出现的最大次数——空转重试的结构特征。

    正常链路每次调用参数都不同（读不同文件、查不同目录）；
    空转则是同一调用原样重试（幻觉工具、路径试错、命令盲重）。
    """
    start = _dispatch_start(messages)
    seen: dict = {}
    worst = 0
    for m in messages[start:]:
        if not isinstance(m, AIMessage):
            continue
        for tc in getattr(m, "tool_calls", None) or []:
            key = (
                tc.get("name", ""),
                json.dumps(tc.get("args", {}), sort_keys=True, ensure_ascii=False),
            )
            seen[key] = seen.get(key, 0) + 1
            worst = max(worst, seen[key])
    return worst


def _is_tool_error(content) -> bool:
    """识别失败的工具结果：MCP 错误、终端命令失败、无效工具名等。"""
    text = str(content).strip()
    return (
        text.startswith("Error")
        or text.startswith("错误")
        or "执行失败" in text[:60]
        or "is not a valid tool" in text[:100]
    )


def _consecutive_tool_error_streak(messages) -> int:
    """从最新往前数连续失败的工具结果数（中间夹 AI 消息不打断，成功即清零）。"""
    streak = 0
    for m in reversed(messages[_dispatch_start(messages):]):
        if isinstance(m, ToolMessage):
            if _is_tool_error(getattr(m, "content", "")):
                streak += 1
            else:
                break
    return streak


def _artifact_preview(name: str, args: dict) -> str:
    """从写操作的参数里提取人类可读的产物标识（文件路径 / SQL 摘要）。"""
    if not isinstance(args, dict):
        return name
    for key in ("file_path", "file", "path", "vm_dest_dir"):
        if args.get(key):
            return str(args[key])
    for key in ("command", "sql"):
        if args.get(key):
            return _truncate(str(args[key]), 50)
    return name


def _produced_artifacts(messages, start: int) -> list:
    """找出本次派遣内【执行成功的写操作】产物列表。

    通过 ToolMessage.tool_call_id 与 AIMessage.tool_calls 配对，
    只统计结果非错误的写操作。返回 [{"name","artifact"}]，最多 8 条。
    """
    calls = {}
    order = []
    for m in messages[start:]:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                tcid = tc.get("id")
                if tcid:
                    calls[tcid] = {
                        "name": tc.get("name", ""),
                        "args": tc.get("args", {}),
                        "ok": None,
                    }
                    order.append(tcid)
        elif isinstance(m, ToolMessage):
            tcid = getattr(m, "tool_call_id", None)
            if tcid in calls:
                calls[tcid]["ok"] = not _is_tool_error(getattr(m, "content", ""))

    produced = []
    for tcid in order:
        c = calls[tcid]
        if c["ok"] and c["name"] in ARTIFACT_TOOL_NAMES:
            produced.append({
                "name": c["name"],
                "artifact": _artifact_preview(c["name"], c["args"]),
            })
    return produced[-8:]


def _dispatches_this_round(messages, expert_name: str) -> int:
    """本轮（最近一条用户消息之后）该专家被成功派遣的次数。"""
    last_user = 0
    for i, m in enumerate(messages):
        if isinstance(m, HumanMessage):
            last_user = i
    target = f"transfer_to_{expert_name}"
    return sum(
        1
        for m in messages[last_user:]
        if _is_successful_dispatch(m) and (getattr(m, "name", "") or "") == target
    )


@wrap_model_call
async def hard_tool_budget(request, handler):
    """专家三层结构闸：L2 派遣闸 → L1 重复/错误闸 → L3 总数闸。

    任一闸触发即**不调用模型**，直接返回一条不带 tool_calls 的 AIMessage——
    ReAct 循环见到无工具调用的 AI 消息即自然终止，专家带着说明交回主管。
    三层分工：L1 精确识别空转（同参重试/连续报错），productive 长链不受影响；
    L2 堵住"预算随派遣重置"导致的总量无上界；L3 兜底。全部从消息结构判定，
    不依赖 prompt 规则与模型自觉。
    """
    messages = request.state.get("messages", [])
    start = _dispatch_start(messages)

    # L2 派遣闸：本轮已被成功派遣超限 → 零成本拒收（不调模型、不执行任何工具）
    last_dispatch = next(
        (m for m in reversed(messages[:start]) if _is_successful_dispatch(m)), None
    )
    if last_dispatch is not None:
        expert_name = (getattr(last_dispatch, "name", "") or "")[len("transfer_to_"):]
        if expert_name:
            n_dispatch = _dispatches_this_round(messages, expert_name)
            if n_dispatch > MAX_DISPATCHES_PER_EXPERT_PER_ROUND:
                return AIMessage(content=(
                    f"[完成-阻断] 本专家（{expert_name}）本轮已被派遣 {n_dispatch} 次，"
                    f"达到防循环上限（{MAX_DISPATCHES_PER_EXPERT_PER_ROUND}）。"
                    "请主管基于已有信息直接答复用户或改派其他专家，不要再派遣本专家。"
                ))

    # L1a 同参重复闸：同一调用原样重试说明模型在空转
    dup = _duplicate_tool_call_count(messages)
    if dup >= MAX_DUPLICATE_TOOL_CALLS:
        return AIMessage(content=(
            f"[待办] 同一工具调用（含参数）已重复 {dup} 次，继续重试无意义。"
            "请基于已有结果收尾：汇总已确认的信息与当前障碍，交回主管。"
        ))

    # L1b 连续错误闸：环境/参数类失败盲试只会烧预算
    err = _consecutive_tool_error_streak(messages)
    if err >= MAX_CONSECUTIVE_TOOL_ERRORS:
        return AIMessage(content=(
            f"[待办] 连续 {err} 次工具调用失败，疑似环境或参数问题。"
            "请汇总失败原因与已验证事实交回主管，由主管决定换工具、换专家或直接答复用户。"
        ))

    # L3 软警告窗口：已接近上限但未到硬线——注入警告再给模型一次收尾机会，
    # 不修改图 state（仅本次模型调用可见），因此不会污染历史或重复累积。
    n_calls = _calls_since_last_dispatch(messages)
    if SOFT_WARN_TOOL_CALLS <= n_calls < MAX_TOOL_CALLS_PER_EXPERT:
        warn = SystemMessage(content=(
            f"⚠️ 你本次派遣已执行 {n_calls} 次工具调用，距硬上限仅剩 "
            f"{MAX_TOOL_CALLS_PER_EXPERT - n_calls} 次。请立即二选一："
            f"① 若任务产物已完成，直接输出 [完成] <子任务>: <结果>，不再调用工具；"
            f"② 若未完成，只执行最关键的一步，然后用一句话汇总现状交回主管。"
            "禁止继续开放式探索。"
        ))
        warned_state = {**request.state, "messages": [*messages, warn]}
        # ModelRequest 是 dataclass（不是 pydantic model，没有 model_copy）
        return await handler(dataclasses.replace(request, state=warned_state))

    # L3 总数闸（硬截断）
    if n_calls >= MAX_TOOL_CALLS_PER_EXPERT:
        start = _dispatch_start(messages)
        # 智能判定：本次派遣若有成功的写操作，说明产物可能已落地——
        # 合成 [完成-待验证] 让主管派验证单确认，而非误报"未完成"。
        produced = _produced_artifacts(messages, start)
        if produced:
            artifacts_text = "\n".join(
                f"- [{p['name']}] {p['artifact']}" for p in produced
            )
            return AIMessage(content=(
                f"[完成-待验证] 工具调用达 {MAX_TOOL_CALLS_PER_EXPERT} 次硬上限被强制收尾，"
                f"但本次派遣存在 {len(produced)} 个成功的写操作，产物可能已完成：\n"
                f"{artifacts_text}\n"
                "请主管派 ≤2 次调用的只读验证单确认产物完整性："
                "验证通过即按 [完成] 推进；验证不通过再按 [待办] 拆小重派。"
            ))
        # 无成功写操作：仍是纯探索未完成
        tool_summary = []
        for m in messages[start:]:
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    tool_summary.append(f"- {tc['name']}")
        summary_text = "\n".join(tool_summary[-10:])
        return AIMessage(content=(
            f"[待办] 本次派遣工具调用已达 {MAX_TOOL_CALLS_PER_EXPERT} 次硬上限，"
            f"本专家被强制收尾，未产生写操作产物。\n\n已执行的操作：\n{summary_text}\n\n"
            "请主管把剩余工作拆成 ≤5 次调用、直奔目标路径的小任务重新派遣。"
        ))

    return await handler(request)


# ==================== 零工具空转守卫（防"假成功"） ====================
# 背景：E2E 回归中观察到专家被派遣后【不调用任何工具】，
#   - 场景 A（T3）：直接输出 "Transferring back to supervisor" 空转返回；
#   - 场景 B（T7）：输出"已派遣…稍候即出结果"等文字，把【没做】包装成【成功】，
#     主管未核验即向用户报成功——比明确失败更危险。
# 结构判定（不靠 prompt 自觉）：模型准备终止（无 tool_calls）
#   + 本次派遣零 ToolMessage + 任务文本含动作性动词 →
#   先强制给一次"真去执行"的机会；仍空转则把假成功转成明确的 [待办] 信号。

NOOP_GUARD_MARK = "【空转守卫·必须先执行】"

# 任务文本中出现这些动词，说明该任务期望"动手"而非空谈
ACTION_VERBS = (
    "查询", "执行", "检索", "打开", "读取", "查看", "检查", "运行", "搜索",
    "统计", "写入", "写文件", "创建", "新建", "获取", "修改", "删除",
    "列出", "计算", "调研", "采集", "上传", "安装",
)


def _has_tool_execution(messages, start: int) -> bool:
    """本次派遣之后是否真实执行过至少一个工具（存在 ToolMessage）。"""
    return any(isinstance(m, ToolMessage) for m in messages[start:])


def _task_text_since_dispatch(messages, start: int) -> str:
    """取本次派遣后最近一条 HumanMessage 的文本（即主管下达的任务）。"""
    for m in reversed(messages[start:]):
        if isinstance(m, HumanMessage):
            content = getattr(m, "content", "")
            return content if isinstance(content, str) else str(content)
    # 子图未注入 Human 的兜底：取全文最后一条 Human
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            content = getattr(m, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


@wrap_model_call
async def no_op_guard(request, handler):
    """拦截"零工具调用却要交回/报成功"的专家回复。"""
    response = await handler(request)

    # 只处理标准 ModelResponse；ExtendedModelResponse 等形态原样放行
    result = getattr(response, "result", None)
    if not result:
        return response

    ai_messages = [m for m in result if isinstance(m, AIMessage)]
    if not ai_messages:
        return response
    last_ai = ai_messages[-1]

    # 模型发起了工具调用 → 正常执行链，放行
    if getattr(last_ai, "tool_calls", None):
        return response

    messages = request.state.get("messages", [])
    start = _dispatch_start(messages)

    # 本次派遣已真实执行过工具 → 属于"做完后总结"，放行
    if _has_tool_execution(messages, start):
        return response

    task = _task_text_since_dispatch(messages, start)

    # 任务不含动作性要求（如纯讨论/解释）→ 允许直接文字回复
    if not any(verb in task for verb in ACTION_VERBS):
        return response

    already_guarded = any(
        isinstance(m, SystemMessage)
        and NOOP_GUARD_MARK in str(getattr(m, "content", ""))
        for m in messages[start:]
    )

    if not already_guarded:
        # 第一次空转：不把假结果向上交，追加纠正消息后再给模型一次真正执行的机会
        warn = SystemMessage(content=(
            f"{NOOP_GUARD_MARK}你还没有调用任何工具，任务实际尚未执行。"
            "请立即选择与任务匹配的【本岗位工具】真正执行一次，再按真实结果汇报；"
            "只有在确认自己确实没有该能力时，才允许输出 "
            "[待办] <子任务>: <缺少的能力> 并交回主管。"
            "禁止不调工具就声称完成，也禁止只说'交回主管'。"
        ))
        guided_state = {
            **request.state,
            "messages": [*messages, last_ai, warn],
        }
        return await handler(dataclasses.replace(request, state=guided_state))

    # 第二次仍零工具空转：丢弃其原文，统一转成主管不会误判为成功的 [待办] 信号
    return AIMessage(content=(
        "[待办] 任务未执行：连续两轮未调用任何工具即试图交回，"
        "本结果不代表成功。\n"
        f"原始任务：{_truncate(task, 120)}\n"
        "请主管改派其他专家、把任务拆小后重新派遣，或人工介入处理。"
    ))


def _truncate_oversized(messages):
    """把超长消息内容截断为"头 3/4 + 尾 1/4"，中间标注省略长度。
    主要针对 ToolMessage（目录树/页面 HTML/长命令输出），对所有消息类型统一生效。"""
    sanitized = []
    for m in messages:
        content = getattr(m, "content", None)
        if isinstance(content, str) and len(content) > MAX_TOOL_MSG_CHARS:
            head = content[: MAX_TOOL_MSG_CHARS * 3 // 4]
            tail = content[-MAX_TOOL_MSG_CHARS // 4 :]
            omitted = len(content) - len(head) - len(tail)
            m = m.model_copy(update={
                "content": f"{head}\n...[中间 {omitted} 字符已截断，仅保留首尾]...\n{tail}"
            })
        sanitized.append(m)
    return sanitized


def _approx_token_counter(messages) -> int:
    """粗略 token 估算：1 token ≈ 3 个字符 + 每条消息固定开销 10。
    不依赖 tiktoken，避免 qwen 兼容模式下 ChatOpenAI 拿不到正确 tokenizer。"""
    total = 0
    for m in messages:
        content = getattr(m, "content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # 多模态消息：拼接所有 text part
            text = "".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        else:
            text = str(content)
        total += len(text) // 3 + 10
    return total


@before_model
def trim_history(state, runtime):
    """before_model 中间件：把 state["messages"] 按"保留最近"策略裁剪到 MAX_CONTEXT_TOKENS 以内。
    始终保留首条 SystemMessage，保证角色 prompt 不丢失。

    ⚠️ 与旧 pre_model_hook 的语义差异（迁移自 v0 的 pre_model_hook）：
    - 旧 pre_model_hook 返回 {"llm_input_messages": trimmed}，只覆盖**本次 LLM 输入**，
      不修改持久化的 state["messages"]（checkpoint 完整保留全部历史）；
    - 新 @before_model 返回 {"messages": trimmed}，是**覆盖** state["messages"]
      （LangChain v1 middleware 的标准语义），后续节点与下次 checkpoint 看到的就是裁剪后的列表。
      对于"防止死循环撑爆 context"这个目的，两者效果等价（都让 LLM 看到的 token 数受控）；
      差异只在 checkpoint 里是否还能看到被裁掉的旧消息——本项目用 FileSaver 落盘历史
      只是为了断点恢复，不需要保留全部原始消息，所以接受这个语义变化。

    ⚠️ token_counter 选择：
      用户模板给的是 token_counter=model（让 langchain 用模型自带 tokenizer），
      但 qwen3 通过阿里云百炼兼容接口接入，ChatOpenAI 拿不到对应 tokenizer，
      会退化到默认 tiktoken 估算（与实际 token 数偏差大，可能低估导致仍撑爆）；
      这里继续用上一轮验证过的 _approx_token_counter（1 token ≈ 3 字符粗估），
      保守偏高估，确保不触 983616 上限。"""
    messages = state.get("messages", [])
    if not messages:
        return {"messages": []}
    # 先截断单条超长消息（trim_messages 无法拆分单条），再按 token 预算裁剪
    messages = _truncate_oversized(messages)
    trimmed = trim_messages(
        messages,
        strategy="last",
        max_tokens=MAX_CONTEXT_TOKENS,
        token_counter=_approx_token_counter,
        include_system=True,                # 始终保留 system 消息（角色 prompt）
        start_on=("system", "human"),      # 裁剪后必须以 system 或 human 开头
        end_on=("human", "tool"),           # 裁剪后以 human 或 tool 结尾，方便 LLM 推理
    )
    return {"messages": trimmed}


# ==================== 图的构建 ====================

async def build_unified_app():
    """
    加载全部 MCP 工具 → 创建 3 个专家 → 创建 Supervisor → 编译为可执行图。

    :return: 编译后的 LangGraph 应用（已注入 FileSaver 检查点）
    """
    # 1. 启动 MCP stdio 子进程并加载各专家的专属工具（必须在创建 agent 前一次性加载完）
    #    ⭐ asyncio.gather 并行拉起 5 个服务：各 loader 相互独立（各自的子进程与全局 client），
    #       串行启动实测约 12s，并行后约 3~4s
    (
        powershell_tools,   # Windows 终端
        browser_tools,      # 浏览器
        rag_self_tools,     # 知识库自查询
        win_vm_tools,       # Linux 沙盒虚拟机
        mysql_tools,        # MySQL 数据库
    ) = await asyncio.gather(
        get_stdio_powershell_tools(),
        get_stdio_browser_tools(),
        get_stdio_rag_self_tools(),
        get_stdio_win_vm_tools(),
        get_stdio_mysql_tools(),
    )

    # 2. 按职责创建 3 个核心专家（工具集互相隔离，避免单 agent 工具过多导致选择混乱）
    #    ⭐ middleware=[trim_history]：三个专家内部 ReAct 循环也会累积消息，
    #       实测死循环时 code_expert 报 openai.BadRequestError: Range of input length should be [1, 983616]，
    #       所以专家内部也要 trim messages。
    #       （LangChain v1 的 create_agent 已移除 pre_model_hook，改用 middleware 体系）
    research_agent = create_agent(
        model=llm_qwen,
        tools=browser_tools + rag_self_tools,
        name="research_expert",
        system_prompt=RESEARCH_SYSTEM_PROMPT + EXPERT_HANDOFF_RULE,
        middleware=[trim_history, hard_tool_budget, no_op_guard],
    )
    code_agent = create_agent(
        model=llm_qwen,
        tools=file_tools + project_read_tools + powershell_tools,
        name="code_expert",
        system_prompt=CODE_SYSTEM_PROMPT + EXPERT_HANDOFF_RULE,
        middleware=[trim_history, hard_tool_budget, no_op_guard],
    )
    infra_agent = create_agent(
        model=llm_qwen,
        tools=mysql_tools + win_vm_tools,
        name="infra_expert",
        system_prompt=INFRA_SYSTEM_PROMPT + EXPERT_HANDOFF_RULE,
        middleware=[trim_history, hard_tool_budget, no_op_guard],
    )

    # 3. 创建主管：自动为其生成 transfer_to_<专家名> 转接工具
    #    ⭐ output_mode="last_message"：专家返回时只把最后一条消息加回主消息流，
    #       避免专家 ReAct 内部多步历史在 supervisor 状态里累积；
    #    ⭐ parallel_tool_calls=False：禁止 supervisor 一次并行派多个专家
    #       （否则它会"同时"派 infra+code，又各自返回导致来回路由）。
    #    ⚠️ 不给 supervisor 挂裁剪中间件：
    #       - supervisor 用的是 langgraph_supervisor.create_supervisor，内部转给
    #         langgraph.prebuilt.create_react_agent（与上面 langchain.agents.create_agent
    #         不是同一套 API），它仍支持旧 pre_model_hook 但**不接受 middleware 参数**；
    #       - 按设计 supervisor 只做转发/汇总，单任务内多轮 handoff 累积量远低于
    #         983616 token 上限（已用 recursion_limit=50 + SUPERVISOR_PROMPT 终止规则双保险）。
    supervisor = create_supervisor(
        agents=[research_agent, code_agent, infra_agent],
        model=llm_qwen,
        prompt=SUPERVISOR_PROMPT,
        output_mode="last_message",
        parallel_tool_calls=False,
    )

    # 4. 编译图并注入文件检查点（三个专家共享同一份对话状态与记忆）
    app = supervisor.compile(checkpointer=FileSaver())
    return app


# ==================== 交互辅助：心跳 / 历史裁剪 ====================

async def _stream_with_heartbeat(stream, start_time, interval: int = 15):
    """包装 app.astream：超过 interval 秒没有新 chunk 时打印心跳并 yield None。

    单个 MCP 工具调用可能耗时 20~90s（浏览器搜索、ssh、批量 SQL），期间无任何输出
    会让用户以为程序卡死。心跳只提示"仍在执行"，不改变事件流本身。

    ⚠️ 关键实现约束：禁止用 asyncio.wait_for(ait.__anext__(), timeout=...) 实现——
    wait_for 超时会 cancel 等待中的 __anext__ 协程，而 langgraph 的 astream 由该协程
    驱动整张图的执行，一旦被 cancel，图运行随之中断、剩余 chunk 全部丢失，
    流还会"干净"关闭（实测每次运行都精确死在第一次心跳之后）。
    正确做法：__anext__ 只启动一次并持久持有，超时仅打印心跳，永不取消。
    """
    ait = stream.__aiter__()
    pending = None
    while True:
        if pending is None:
            pending = asyncio.ensure_future(ait.__anext__())
        done, _ = await asyncio.wait({pending}, timeout=interval)
        if not done:
            print(f"   ⏳ 仍在执行中（本轮已运行 {int(time.time() - start_time)}s）…", flush=True)
            continue
        task, pending = pending, None
        try:
            chunk = task.result()
        except StopAsyncIteration:
            return
        yield chunk


# 每轮结束后持久化保留的最近消息条数：
# 既保留近期对话上下文（下轮能接续），又防止旧失败循环/超长历史在 checkpoint 里
# 越积越多——这就是"固定 thread_id 跑几轮后要么撑爆要么被旧记录带偏"的根因。
MAX_KEEP_MESSAGES = 30

# 单轮用户输入的最大协作步数（根图层）：
# 防止 supervisor 在多专家间来回派遣导致总轮次爆炸（实测可达 60+ 轮 / 5+ 小时）。
# 每层闸只管单个专家/单次派遣，这里管"整轮总步数"——超过即优雅终止本轮，
# 提示用户拆小任务或手动介入，而不是无限烧时间。
# 一步 ≈ 一次 transfer 往返，15 步足以完成"调研→编码→部署"的正常链路。
MAX_STEPS_PER_ROUND = 15


async def _prune_history(app, config) -> None:
    """把 checkpoint 中持久化的消息裁到最近 MAX_KEEP_MESSAGES 条（确定性结构操作）。
    本版本 langgraph 无 RemoveAllMessages，逐条 RemoveMessage 等效实现。"""
    state = await app.aget_state(config)
    msgs = state.values.get("messages", [])
    excess = len(msgs) - MAX_KEEP_MESSAGES
    if excess <= 0:
        return
    removals = [RemoveMessage(id=m.id) for m in msgs[:excess] if getattr(m, "id", None)]
    if removals:
        await app.aupdate_state(config, {"messages": removals})


async def _rollback_failed_round(app, config, pre_ids: set, reason: str) -> None:
    """失败轮次自动回滚：删除本轮新增的过程消息，只保留用户原始问题。

    背景：异常/递归烧穿的轮次会在 checkpoint 里残留混乱的失败循环，
    固定 thread-id 下轮启动就被这些消息带偏（普通用户不会手动换 id）。
    这里在失败后立即清理，让下轮"无感恢复"：
      - 本轮新增的 AI/Tool 消息全部 RemoveMessage；
      - 保留本轮 HumanMessage（用户知道自己问过什么）；
      - 追加一条简短说明，标注未完成原因。
    成功轮次不触发，跨轮上下文正常保留。
    """
    state = await app.aget_state(config)
    msgs = state.values.get("messages", [])

    removals = []
    for m in msgs:
        mid = getattr(m, "id", None)
        if mid and mid not in pre_ids and not isinstance(m, HumanMessage):
            removals.append(RemoveMessage(id=mid))

    if not removals:
        return

    note = AIMessage(content=(
        f"（上一轮任务因「{reason}」未完成，过程记录已自动清理，不影响继续使用；"
        "可直接重新描述需求）"
    ))
    await app.aupdate_state(config, {"messages": [*removals, note]})


# ==================== 交互式运行入口 ====================

async def run_agent(thread_id: str = "4"):
    """
    启动统一多智能体的交互式命令行循环。

    :param thread_id: 会话线程 ID，相同 ID 可通过 FileSaver 恢复历史上下文
    """
    # 构建多智能体图（内部会拉起 5 个 MCP stdio 服务子进程）
    app = await build_unified_app()

    # 会话配置：thread_id 决定检查点归属；recursion_limit 是纯兜底——
    # 值由三层闸阈值推导（GRAPH_RECURSION_LIMIT），保证正常操作下永远是闸先收尾，
    # GraphRecursionError 只在闸本身失效时才可能触发。
    config = RunnableConfig(
        configurable={"thread_id": thread_id}, recursion_limit=GRAPH_RECURSION_LIMIT
    )

    print("=" * 60)
    print("统一多智能体系统已启动（research / code / infra + supervisor）")
    print("输入 exit 或 quit 退出；输入 new 开启全新会话线程（旧记录仍可按原 thread-id 恢复）")
    print("=" * 60)

    # 已打印消息 id 集合（跨轮次生效）：
    # checkpointer 会让每个 chunk 重放全量历史消息，按消息 id 去重后只打印增量，
    # 避免屏幕输出随对话长度线性膨胀（实测第 3 个任务时重复输出已达数百行）
    printed_msg_ids = set()

    while True:
        # input 是阻塞调用，放到线程中执行，避免卡住事件循环（与 code_agent.py 一致）
        try:
            user_input = await asyncio.to_thread(input, "用户：")
        except EOFError:
            # 管道输入结束（如自动化测试/脚本重定向）时优雅退出
            print("\n检测到输入结束，退出统一多智能体系统。")
            break

        if user_input.strip().lower() in ("exit", "quit"):
            break

        # 内置命令 new：不重启进程直接开新会话线程，避免旧 checkpoint 干扰新一轮对话
        if user_input.strip().lower() == "new":
            thread_id = f"s{int(time.time())}"
            config = RunnableConfig(
                configurable={"thread_id": thread_id}, recursion_limit=GRAPH_RECURSION_LIMIT
            )
            printed_msg_ids.clear()
            print(f"🆕 已开启全新会话线程（thread_id={thread_id}）。\n")
            continue

        print("\n🤖 多智能体团队正在协作处理...")
        print("==" * 30)

        # 过程统计：迭代步数与工具耗时
        iteration_count = 0
        start_time = time.time()
        last_tool_time = start_time

        # 本轮关键操作日志：用于最终汇总时告诉用户"做了什么"
        # 格式：(专家名, 动作描述)
        actions_log = []
        # 记录最后一条 supervisor 的 AI 消息（用于最终汇总提取结论）
        last_supervisor_text = ""

        # 本轮开始前已存在的消息 id 集合：失败回滚时据此识别"本轮新增消息"
        pre_snapshot = await app.aget_state(config)
        pre_ids = {
            getattr(m, "id", None)
            for m in pre_snapshot.values.get("messages", [])
        }
        pre_ids.discard(None)

        # 本轮失败原因标志：非 None 时在轮末自动回滚本轮过程消息
        fail_reason = None

        # 流式消费图的执行过程，逐节点打印专家的思考与工具调用
        # GraphRecursionError 兜底：即使专家空转触顶，也只终止本轮、不杀死整个进程
        try:
            async for chunk in _stream_with_heartbeat(
                app.astream(input={"messages": user_input}, config=config), start_time
            ):
                if chunk is None:
                    continue  # 心跳（包装器已打印"仍在执行"）
                # 增量过滤：跳过 checkpoint 重放的历史消息，只留本 chunk 中的新消息
                new_items = []
                for node_name, node_output in chunk.items():
                    if "messages" not in node_output:
                        continue
                    for msg in node_output["messages"]:
                        msg_id = getattr(msg, "id", None)
                        if msg_id is not None:
                            if msg_id in printed_msg_ids:
                                continue
                            printed_msg_ids.add(msg_id)
                        new_items.append((node_name, msg))

                if not new_items:
                    continue

                iteration_count += 1

                # 单轮总步数上限：防止 supervisor 在多专家间来回派遣导致总轮次爆炸。
                if iteration_count >= MAX_STEPS_PER_ROUND:
                    print(
                        f"\n⚠️ 本轮协作步数已达上限（{MAX_STEPS_PER_ROUND} 步），强制终止。"
                    )
                    print(
                        "   通常是 supervisor 在多专家间反复派遣但无实质进展。"
                        "建议把任务拆成更小的独立子任务分步下达，或手动介入当前卡点。\n"
                    )
                    fail_reason = "协作步数超限、无实质进展"
                    break

                for node_name, msg in new_items:
                    if isinstance(msg, AIMessage):
                        # AI 消息：有正文打印思考内容；无正文则说明发起了工具调用
                        if msg.content:
                            format_debug_output(f"{node_name}", msg.content)
                            if node_name == "supervisor":
                                last_supervisor_text = msg.content
                        else:
                            for tool in msg.tool_calls:
                                args_str = _truncate(
                                    str(tool.get("args", {})), MAX_ARGS_CHARS
                                )
                                print(f"  🔧 {node_name} → {tool['name']}({args_str})")
                                actions_log.append((node_name, f"调用 {tool['name']}"))
                    elif isinstance(msg, ToolMessage):
                        # 工具消息：只打印工具名 + 简短结果 + 耗时
                        tool_name = getattr(msg, "name", "unknown")
                        tool_content = msg.content

                        current_time = time.time()
                        tool_duration = current_time - last_tool_time
                        last_tool_time = current_time

                        result_summary = _truncate(str(tool_content), MAX_RESULT_CHARS)
                        print(f"  ↳ {tool_name}: {result_summary} ({tool_duration:.1f}s)")

            # ============ 最终汇总（解析 supervisor 的固定格式汇报） ============
            elapsed = time.time() - start_time
            report = _parse_final_report(last_supervisor_text)

            if report is not None:
                # 正常路径：按统一模板输出，成功/失败一目了然
                _print_final_summary(report, iteration_count, elapsed)
                # 完全失败且无可用产物 → 本轮消息会干扰下轮，标记自动回滚；
                # "部分完成"保留（可能含有用产物，交由 prune 裁剪）
                if report["status"] == "失败" and not report["done"]:
                    fail_reason = "任务执行失败"
            else:
                # 降级路径：模型没遵守格式，用 actions_log + 简短原文兜底
                print("\n" + "=" * 60)
                print(f"📋 本轮结果（{iteration_count} 步，{elapsed:.0f}s）")
                print("=" * 60)
                if actions_log:
                    print("\n📌 已执行：")
                    for expert, action in actions_log[-10:]:
                        print(f"   • [{expert}] {action}")
                if last_supervisor_text:
                    print(f"\n💬 主管原话：{_truncate(last_supervisor_text, 200)}")
                print("\n" + "=" * 60 + "\n")
        except GraphRecursionError:
            print(f"\n⚠️ 本轮被强制终止（{iteration_count} 步，递归上限）")
            if actions_log:
                print("已执行的操作：")
                for expert, action in actions_log[-10:]:
                    print(f"   • [{expert}] {action}")
            print("   建议：把任务拆成更小的子任务分步下达\n")
            fail_reason = "递归上限（专家空转）"
        except Exception as e:
            # 单轮失败不杀死进程：API 配额/网络/工具异常只终止本轮，会话保留可直接重试
            print(f"\n❌ 本轮异常终止（{iteration_count} 步）：{type(e).__name__}")
            if actions_log:
                print("已执行的操作：")
                for expert, action in actions_log[-10:]:
                    print(f"   • [{expert}] {action}")
            print(f"   原因：{_truncate(str(e), 120)}")
            print("   修复问题后直接重新输入即可\n")
            fail_reason = f"{type(e).__name__}"

        # 失败轮次先自动回滚（删除本轮混乱过程、保留用户问题），
        # 固定 thread-id 下轮启动不再被失败循环带偏——普通用户无需手动换 id。
        if fail_reason is not None:
            await _rollback_failed_round(app, config, pre_ids, fail_reason)

        # 再裁剪持久化历史：保留下轮需要的近期上下文
        await _prune_history(app, config)


if __name__ == "__main__":
    asyncio.run(run_agent())
