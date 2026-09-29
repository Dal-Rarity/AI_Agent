import asyncio

from langchain.agents import create_agent
from langchain_core.messages import convert_to_messages
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph_supervisor import create_supervisor

from model.qwen import llm_qwen
from tools.shell_tools import get_stdio_shell_tools
from tools.file_tools import file_tools


# 优化打印结果
def pretty_print_messages(update, last_message=False):
    # print(update.items())
    for node_name, node_update in update.items():
        update_label = f"Update from node {node_name}:"
        print(update_label)

        messages = convert_to_messages(node_update["messages"])
        # print(messages)
        if last_message:
            messages = messages[-1:]

        for message in messages:
            pretty_message = message.pretty_repr(html=True)
            print(pretty_message)
        print("\n")

# 创建并运行智能体
async def run_agent():
    memory = MemorySaver()

    shell_tools = await get_stdio_shell_tools()
    # 创建研究智能体
    research_agent = create_agent(
        model=llm_qwen,
        tools=shell_tools + file_tools,
        name="research_expert",
        system_prompt="你是一个技术方案设计的专家，专门负责研究和设计技术方案，不做代码生成，请指导 code_generator 来生成代码"
    )
    # 创建代码生成智能体
    code_agent = create_agent(
        model=llm_qwen,
        tools=shell_tools + file_tools,
        name="code_generator",
        system_prompt="你是一个编程专家，专门负责生成代码，请根据 research_expert 的要求实现代码或进行代码相关文件的操作"
    )
    # 串联智能体的智能体
    supervisor_agent = create_supervisor(
        agents=[research_agent, code_agent],
        model=llm_qwen,
        prompt=(
            "你是一个团队的主管，负责管理 research_expert 和 code_generator 两个智能体。"
            "对于任务的调研和规划，请使用 research_expert 智能体。"
            "对于编码任务，请使用 code_generator 智能体。"
        )
    )

    app = supervisor_agent.compile(checkpointer=memory)

    # 接受用户输入以及输出
    while True:
        user_input = input("用户：")

        if user_input.lower() == "exit":
            break


        config = RunnableConfig(configurable={"thread_id": "3"}, recursion_limit=100)
        async for chunk in app.astream(input={"messages": user_input}, config=config):
            pretty_print_messages(chunk, last_message=True)


# 入口守卫：仅直接运行本脚本时执行，被主入口 main.py 等模块 import 时不自动启动
if __name__ == "__main__":
    asyncio.run(run_agent())
