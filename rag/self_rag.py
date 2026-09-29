import sys
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

# 当前目录结构：<项目根>/rag/self_rag.py，parents[0]=rag，parents[1]=<项目根>
# （旧代码取 parents[3] 并注释为旧机器路径 D:\Python\ai-agent-test，在当前结构下指向错误，已修正）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.rag import create_client, retrieve_index, upload_rag_file_to_bailian, add_document_to_index, \
    get_index_job_status


mcp = FastMCP(log_level="WARNING")




@mcp.tool(name="query_rag", description="从阿里云百炼平台查询知识库信息")
def query_rag_from_bailian(query: Annotated[str, Field(description="访问知识库查询的内容", json_schema_extra={"example": "终端的操作规范"})]) -> str:
     try:
         bailian_client = create_client()
         workspace_id = 'ws-9xp4ifbwnveexrw2'
         index_id = 'rbmju91kt6'
         rag = retrieve_index(bailian_client, workspace_id, index_id, query)

         result = ""
         nodes = getattr(getattr(rag.body, "data", None), "nodes", None) or []
         for data in nodes:
             result += f"""{data.text}
"""

         return result or "知识库没有返回相关内容"
     except Exception as e:
         return f"查询知识库失败: {e}"

@mcp.tool(name="upload_local_file_to_bailian_rag", description="将本地的文件上传到阿里云百炼平台")
def upload_rag_to_bailian(file_path: Annotated[str,
    Field(description="本地知识文件的路径，需要传入绝对路径",
          json_schema_extra={"example": r"D:\Python\AI_Agent\rag\powershell.txt"})]):
    bailian_client = create_client()
    workspace_id = "ws-9xp4ifbwnveexrw2"
    category_id = "cate_bb4469b654ee4e8ea94b8022b81b0f83_15488790"
    file_id = upload_rag_file_to_bailian(bailian_client, workspace_id, category_id, file_path)

    index_id = "rbmju91kt6"
    return add_document_to_index(bailian_client, workspace_id, index_id, file_id)

@mcp.tool(name="query_bailian_rag_job_status", description="查询上传到阿里云百炼平台知识库的知识文件处理状态")
def query_bailain_rag_job_status(job_id: str):
    bailian_client = create_client()
    workspace_id = "ws-9xp4ifbwnveexrw2"
    index_id = "rbmju91kt6"
    job_status = get_index_job_status(bailian_client, workspace_id, index_id, job_id)

    return job_status.body.data

if __name__ == '__main__':
    mcp.run(transport="stdio")

    # rag_file_path = ""
    # response = upload_rag_to_bailian(rag_file_path)
    # print(response)
