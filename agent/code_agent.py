import asyncio
import time
import traceback

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from model.qwen import llm_qwen
from rag.rag import query_rag_from_bailian
from tools.browser_tools import get_stdio_browser_tools
from tools.file_saver import FileSaver
from tools.file_tools import file_tools
from tools.mysql_tools import get_stdio_mysql_tools
from tools.powershell_tools import get_stdio_powershell_tools
from tools.rag_self_tools import get_stdio_rag_self_tools
from tools.rag_tools import get_stdio_rag_tools
from tools.shell_tools import get_stdio_shell_tools
from tools.terminal_tools import get_stdio_terminal_tools
from tools.mac_vm_tools import get_stdio_mac_vm_tools
from tools.win_vm_tools import get_stdio_win_vm_tools


def format_debug_output(step_name: str, content: str, is_tool_call = False) -> None :
    if is_tool_call:
        print(f"🔄 【工具调用】 {step_name}")
        print("-" * 40)
        print(content.strip())
        print("-" * 40)
    print(f".💭 【{step_name}】")
    print("-" * 40)
    print(content.strip())
    print("-" * 40)



async def run_agent():
    # memory = MemorySaver()      # 存储到内存
    memory = FileSaver()        # 存储到文件
    # 基础终端工具
    # shell_tools = await get_stdio_shell_tools()
    # MacOS终端工具
    # terminal_tools = await get_stdio_terminal_tools()
    # Windows终端工具
    powershell_tools = await get_stdio_powershell_tools()
    # rag_tools = await get_stdio_rag_tools()                       # RAG工具
    rag_self_tools = await get_stdio_rag_self_tools()               # RAG自查询工具
    browser_tools = await get_stdio_browser_tools()                 # 浏览器工具
    # vm_tools = await get_stdio_mac_vm_tools()                      # 在Mac控制虚拟机工具
    vm_tools = await get_stdio_win_vm_tools()                        # 在Windows控制虚拟机工具
    mysql_tools = await get_stdio_mysql_tools()
    tools = file_tools + powershell_tools + rag_self_tools + browser_tools + vm_tools + mysql_tools
    # tools = file_tools + powershell_tools

    # 方案二：提供一个RAG工具，让智能体通过工具查询知识


    # print("=" * 50)
    # print(f"工具数量: {len(tools)}")
    # for t in tools:
    #     print(f"  - {t.name}")
    # print("=" * 50)

    # 系统提示词设计
    prompt = PromptTemplate.from_template(template="""
# 角色
你是一名优秀的工程师，你的名字叫做{name}

"""

)

    agent = create_agent(
        model = llm_qwen,
        tools=tools,
        system_prompt=SystemMessage(content=prompt.format(name="Bot")),
        checkpointer=memory,
        debug=False,
    )


    config = RunnableConfig(configurable={"thread_id": 3}, recursion_limit=100)

    while True:
        user_input = await asyncio.to_thread(input, "用户：")

        if user_input.strip().lower() in ("exit", "quit"):
            break

        print("\n🤖 助手正在思考处理...")
        print("=="*30)

        # 思考过程迭代处理
        iteration_count = 0
        start_time = time.time()
        last_tool_time = start_time

        # 方案一：从RAG阿里百炼知识库中读取知识，并拼接到提示词中
        prompt = f"""
# 相关知识
执行任务之前先使用 query_rag 工具查询知识库，根据知识库中的知识执行任务

# 用户问题

# 要求
执行任务之前先使用 query_rag 工具查询知识库，根据知识库中的知识执行任务。
创建前端脚手架时必须非交互：npm.cmd create vue@latest <name> --yes -- --default --force。
禁止用会提问的命令去 execute_powershell_command，否则会卡住。
{user_input}"""

        async for chunk in agent.astream(input={"messages": prompt}, config=config):
            iteration_count += 1
            print(f"\n📊 第 {iteration_count} 步执行:")
            print("-"*30)

            items = chunk.items()
            for node_name, node_output in items:
                if "messages" in node_output:
                    for msg in node_output["messages"]:
                        if isinstance(msg, AIMessage):
                            if msg.content:
                                format_debug_output("AI思考", msg.content)
                            else:
                                for tool in msg.tool_calls:
                                    format_debug_output("工具调用", f"{tool['name']}: {tool['args']}")
                        elif isinstance(msg, ToolMessage):
                            tool_name = getattr(msg, "name", "unknown")
                            tool_content = msg.content

                            current_time = time.time()
                            tool_duration = current_time - last_tool_time
                            last_tool_time = current_time

                            tool_result = f"""🔧 工具： {tool_name}
                            📃 结果：
                            {tool_content}
                            ✅️ 状态：执行完成，可以开始下一个任务
                            🕒 执行时间: {tool_duration:.2f} 秒"""
                            format_debug_output("工具执行结果" , tool_result, is_tool_call = True)
                        else:
                            format_debug_output("未实现", f"暂未实现的打印内容：{chunk}")
                            # print(f"{tool_name}: {tool_content}")

            print()


if __name__ == "__main__":
    asyncio.run(run_agent())
