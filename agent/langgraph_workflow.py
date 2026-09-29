import os.path

from langchain_core.messages import AIMessage
from langchain_core.prompt_values import StringPromptValue
from langgraph.constants import START, END
from langgraph.graph import StateGraph, MessagesState

from mcp_tools.browser_tools import search_in_baidu_with_html
from model.qwen import llm_qwen


# 输出图
def output_graph_image(graph, filename):
    try:
        png_data = graph.get_graph().draw_mermaid_png()

        output_file_dir = os.path.dirname(__file__)
        output_file_path = os.path.join(output_file_dir, filename + ".png")

        with open(output_file_path, "wb") as f:
            f.write(png_data)
            print(f"图已保存到: {output_file_path}")
    except Exception as e:
        print(f"图生成失败: {e}")

# MessagesState类继承和能力扩展
class BaiduSearchMessagesState(MessagesState):
    search_keyword: str
    search_question: str
    search_results: str


key_extract_query_keyword = "key_extract_query_keyword"
key_search_baidu = "key_search_baidu"
key_reply_user = "key_reply_user"


# 定义节点
def node_extract_query_keyword(state: BaiduSearchMessagesState):
    last_message = state["messages"][-1]
    question = last_message.content
    state["search_question"] = question
    # print(f"用户问题: {content}")
    prompt = StringPromptValue(text=f"请从如下信息中提取需要在百度中搜索的关键词，直接返回最终结果：\n{question}")
    # print(f"提示: {prompt.text}")
    message = llm_qwen.invoke(input=prompt)
    state["messages"].append(message)
    state["search_keyword"] = message.content
    return state

def node_search_baidu(state: BaiduSearchMessagesState):
    # last_message = state['messages'][-1]
    # keyword = last_message.content
    keyword = state["search_keyword"]
    html = search_in_baidu_with_html(keyword)
    # print(f"百度搜索结果: {html}")
    state["messages"].append(AIMessage(content=f"百度搜索结果: {html}"))
    state["search_results"] = html
    return state

def node_reply_user(state: BaiduSearchMessagesState):
    # question = state["messages"][0].content
    # baidu_search_result = state["messages"][-1].content
    result = llm_qwen.invoke(input=f"""
# 要求
请结合百度搜索的结果，回答用户问题：{state["search_question"]}

# 百度搜索结果
{state["search_results"]}

""")
    state["messages"].append(result)
    return state

state_graph = StateGraph(BaiduSearchMessagesState)
state_graph.add_node(key_extract_query_keyword, node_extract_query_keyword)
state_graph.add_node(key_search_baidu, node_search_baidu)
state_graph.add_node(key_reply_user, node_reply_user)
# 节点串联
state_graph.add_edge(START, key_extract_query_keyword)
state_graph.add_edge(key_extract_query_keyword, key_search_baidu)
state_graph.add_edge(key_search_baidu, key_reply_user)
state_graph.add_edge(key_reply_user, END)

# 输出图
compiled_graph = state_graph.compile()
# output_graph_image(compiled_graph, "graph")


# 入口守卫：仅直接运行本脚本时才执行工作流，被 import 时不自动触发 LLM/搜索调用
if __name__ == "__main__":
    # 串联工作流程
    results = compiled_graph.stream({
        "messages": [("user", "昆明今天的天气如何")]
    })

    for s in results:
        key = list(s)[0]
        print(s[key]["messages"][-1].content)

