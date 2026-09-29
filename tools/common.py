from langchain_core.prompts import ChatMessagePromptTemplate, ChatPromptTemplate
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from langchain_community.agent_toolkits import FileManagementToolkit

llm = ChatOpenAI(
    model="qwen3.8-max-0902",
    base_url="https://ws-9xp4ifbwnveexrw2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    streaming = True
)

system_message_prompt_template = ChatMessagePromptTemplate.from_template(
    template="你是一位{role}专家，擅长{domain}领域的问题回答",
    role = "system"
)

human_message_prompt_template = ChatMessagePromptTemplate.from_template(
    template="用户问题：{question}",
    role = "user"
)


# 创建提示词模版
chat_prompt_template = ChatPromptTemplate.from_messages(
    [
        system_message_prompt_template,
        human_message_prompt_template
    ]
)

class AddInputArgs(BaseModel):
    a: int = Field(description="The first number")
    b: int = Field(description="The second number")


@tool(description="Add two numbers",
      args_schema=AddInputArgs,
      return_direct=False
)
# 1.定义工具
def add(a, b):
    """Add two numbers """
    return a + b


def create_calc_tools():
    return [add]


calc_tools = create_calc_tools()

file_toolkit = FileManagementToolkit(root_dir="/Python/AI_Agent/.temp")
file_tools = file_toolkit.get_tools()