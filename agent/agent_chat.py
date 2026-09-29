from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langchain.agents import create_agent
from langgraph.checkpoint.redis import RedisSaver
from langgraph.checkpoint.mongodb import MongoDBSaver

from model.qwen import llm_qwen
from tools.file_tools import file_tools


def created_agent():
    # 存储到内存
    # memory = MemorySaver()

    # 存储到Redis中
    with RedisSaver.from_conn_string("redis://192.168.56.200:6379/0") as memory:
        memory.setup()

    # 存储到MongoDB中
    # MONGODB_URI = "mongodb://192.168.56.200:27017/"
    # MONGODB_DB = "chat"
    #
    # with MongoDBSaver.from_conn_string(MONGODB_URI, MONGODB_DB) as memory:

        agent = create_agent(
            model=llm_qwen,
            tools=file_tools,
            checkpointer=memory,
            debug=True
        )
        #  MongoDB 必须在创建代理之后进行配置，
        # config = RunnableConfig(configurable={"thread_id": 1})
        #
        # agent = created_agent()
        #
        # res = agent.invoke(input={"messages": [("user", "你好，我是苏")]}, config=config)
        # print("==" * 20)
        # print(res)
        # print("==" * 20)
        # # 执行完之后需要关闭，释放资源
        # memory.close()

        return agent

# created_agent()

def run_agent():
    config = RunnableConfig(configurable={"thread_id": 1})

    agent = created_agent()

    res = agent.invoke(input={"messages": [("user", "你好，我是苏")]}, config=config)
    print("==" * 20)
    print(res)
    print("==" * 20)


# 入口守卫：仅直接运行本脚本时执行，被其他模块 import 时不自动启动
if __name__ == "__main__":
    run_agent()