import uuid


from langchain_core.output_parsers import StrOutputParser
from langchain_community.chat_message_histories import ChatMessageHistory, FileChatMessageHistory
from langchain_core.runnables import RunnableWithMessageHistory         # 旧版内存保存
from langgraph.checkpoint.memory import MemorySaver  # 新版内存保存
from langchain_community.agent_toolkits.file_management import FileManagementToolkit
from langchain.agents import create_agent


from prompts.multi_chat_prompts import multi_chat_prompt
from model.qwen import llm_qwen
from tools.common import file_toolkit


# store = {}      # 存储session_id和会话历史

# 会话历史工厂函数
def get_session_history(session_id: str):
    # if session_id not in store:
    #     store[session_id] = ChatMessageHistory()
    # # print(store)
    # return store[session_id]

    # print(session_id)
    return FileChatMessageHistory(f"{session_id}.json")

def get_tools():

    # 文件工具
    file_tools = file_toolkit.get_tools()



    # 大模型绑定工具
    llm_with_tools = llm_qwen.bind_tools(tools=file_tools)
    # AI_Agent = create_agent(model=llm_qwen, tools=file_tools)

    chain = multi_chat_prompt | llm_with_tools | StrOutputParser()



    # 会话存储实例
    # chat_history = ChatMessageHistory(messages=[HumanMessage(content="我叫阿满")])        # 方式一
    # # 方式二
    # chat_history = ChatMessageHistory()
    # chat_history.add_user_message(HumanMessage(content="我叫阿满"))

    chain_with_history = RunnableWithMessageHistory(
        runnable=chain,
        get_session_history=get_session_history,
        input_messages_key="question",
        history_messages_key="chat_history"
    )

    chat_session_id = uuid.uuid4()


    while True:
        user_input = input("用户：")
        if user_input.lower() == "exit" or user_input.lower() == "quit":
            break

        print("助手：", end="")
        for chunk in chain_with_history.stream(
            {"question": user_input},
            config = {"configurable": {"session_id": chat_session_id}}
        ):
            print(chunk, end="")

        print("\n")

if __name__ == "__main__":
    get_tools()

# for chunk in chain.stream({"question": "我是谁？", "chat_history":[]}):
#     print(chunk, end="")