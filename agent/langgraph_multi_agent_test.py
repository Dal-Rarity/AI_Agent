from langgraph_supervisor import create_supervisor
from langchain.agents import create_agent
from langchain_core.messages import convert_to_messages

from model.qwen import llm_qwen


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

# Create specialized agents
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b

def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    return a * b

def web_search(query: str) -> str:
    """Search the web for information."""
    return (
        "Here are the headcounts for each of the FAANG companies in 2024:\n"
        "1. **Facebook (Meta)**: 67,317 employees.\n"
        "2. **Apple**: 164,000 employees.\n"
        "3. **Amazon**: 1,551,000 employees.\n"
        "4. **Netflix**: 14,000 employees.\n"
        "5. **Google (Alphabet)**: 181,269 employees."
    )

math_agent = create_agent(
    model=llm_qwen,
    tools=[add, multiply],
    name="math_expert",
    system_prompt="You are a math expert. Always use one tool at a time.",  # 原 prompt → system_prompt
)

research_agent = create_agent(
    model=llm_qwen,
    tools=[web_search],
    name="research_expert",
    system_prompt="You are a world class researcher with access to web search. Do not do any math.",  # 原 prompt → system_prompt
)

# Create supervisor workflow
workflow = create_supervisor(
    [research_agent, math_agent],
    model=llm_qwen,
    prompt=(
        "You are a team supervisor managing a research expert and a math expert. "
        "For current events, use research_agent. "
        "For math problems, use math_agent."
    ),
)

# Compile and run
app = workflow.compile()


# 入口守卫：本文件是多智能体演示脚本，仅直接运行时才发起 LLM 调用
if __name__ == "__main__":
    for chunk in app.stream({
        "messages": [
            {
                "role": "user",
                "content": "what's the combined headcount of the FAANG companies in 2025?"
            }
        ]
    }):
        pretty_print_messages(chunk)
        # pretty_print_messages(chunk, last_message=True)
