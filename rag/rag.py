import hashlib
import os
import sys
from typing import Annotated

import requests
from dotenv import load_dotenv
from pydantic import Field

load_dotenv()  # 自动读取项目根目录下的 .env

from alibabacloud_bailian20231229.client import Client as bailian20231229Client
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_bailian20231229 import models as bailian_20231229_models
from alibabacloud_tea_util import models as util_models

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(log_level="WARNING")


# 获取阿里云百炼客户端
def create_client() -> bailian20231229Client:
    config = open_api_models.Config(
        access_key_id=os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID'),
        access_key_secret=os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET'),
    )

    config.endpoint = 'bailian.cn-beijing.aliyuncs.com'
    return bailian20231229Client(config)

# 查询阿里云百炼知识库
def retrieve_index(client, workspace_id, index_id, query):
    retrieve_request = bailian_20231229_models.RetrieveRequest(
        index_id=index_id,
        query=query
    )
    runtime = util_models.RuntimeOptions()
    return client.retrieve_with_options(
        workspace_id,
        retrieve_request,
        {},
        runtime
    )

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
         # MCP stdio 的 stdout 只能走 JSON-RPC，调试信息必须写 stderr，否则客户端会卡死。
         print("-" * 60, file=sys.stderr)
         print("[query_rag_from_bailian]", query, file=sys.stderr)
         print(result, file=sys.stderr)
         print("-" * 60, file=sys.stderr)
         return result or "知识库没有返回相关内容"
     except Exception as e:
         print(f"[query_rag_from_bailian] error: {e}", file=sys.stderr)
         return f"查询知识库失败: {e}"


# 上传文件申请租约
def apply_lease(client, category_id, file_name, file_md5, file_size, workspace_id):
    headers = {}
    runtime = util_models.RuntimeOptions()
    request = bailian_20231229_models.ApplyFileUploadLeaseRequest(
        file_name=file_name,
        md_5=file_md5,
        size_in_bytes=file_size,
    )
    return client.apply_file_upload_lease_with_options(
        category_id,
        workspace_id,
        request,
        headers,
        runtime
    )

# 计算MD5方法
def calculate_md5(file_path: str) -> str:
    """
    计算文件的MD5值。

    参数:
        file_path (str): 文件本地路径。

    返回:
        str: 文件的MD5值。
    """
    md5_hash = hashlib.md5()

    # 以二进制形式读取文件
    with open(file_path, "rb") as f:
        # 按块读取文件，避免大文件占用过多内存
        for chunk in iter(lambda: f.read(4096), b""):
            md5_hash.update(chunk)

    return md5_hash.hexdigest()

# 文件获取
def get_file_info(file_path):
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    file_md5 = calculate_md5(file_path)

    return file_name,  file_size, file_md5

# 参数传入封装
def apply_lease_by_file_path(client, category_id, file_path, workspace_id):
    client = create_client()  # 创建客户端
    file_name, file_size, file_md5 = get_file_info(file_path)

    return apply_lease(client, category_id, file_name, file_md5, file_size, workspace_id)

# 文件上传
def upload_file_to_bailian(upload_url, headers, file_path):
    with open(file_path, 'rb') as f:
        file_content = f.read()

    upload_headers = {
        "Content-Type": headers["Content-Type"],
        "X-bailian-extra": headers["X-bailian-extra"]
    }

    response = requests.put(upload_url, data=file_content, headers=upload_headers)
    # print(response.status_code)
    response.raise_for_status()

# 将文件添加到指定分类中
def add_file_to_bailian_category(client, lease_id, parser, category_id, workspace_id):
    headers = {}
    runtime = util_models.RuntimeOptions()

    request = bailian_20231229_models.AddFileRequest(
        lease_id=lease_id,
        parser=parser,
        category_id=category_id
    )

    return client.add_file_with_options(
        workspace_id,
        request,
        headers,
        runtime
    )

# 查询文件上传状态
def describe_file(client, file_id, workspace_id):
    headers = {}
    runtime = util_models.RuntimeOptions()

    return client.describe_file_with_options(
        file_id,
        workspace_id,
        headers,
        runtime
    )

# 智能体自动创建知识库
def create_index(client, name, file_id, workspace_id, structure_type="unstructured", source_type="DATA_CENTER_FILE", sink_type="BUILT_IN"):
    headers = {}
    runtime = util_models.RuntimeOptions()

    request = bailian_20231229_models.CreateIndexRequest(
        structure_type=structure_type,
        source_type=source_type,
        sink_type=sink_type,
        name=name,
        document_ids=[file_id]
    )

    return client.create_index_with_options(
        workspace_id,
        request,
        headers,
        runtime
    )

# 提交向量化任务
def submit_index(client, index_id, workspace_id):
    headers ={}
    runtime = util_models.RuntimeOptions()
    submit_index_job_request = bailian_20231229_models.SubmitIndexJobRequest(index_id=index_id)

    return client.submit_index_job_with_options(
        workspace_id,
        submit_index_job_request,
        headers,
        runtime
    )

# 查询向量化任务状态
def get_index_job_status(client, workspace_id, index_id, job_id):
    headers = {}
    runtime = util_models.RuntimeOptions()

    get_index_job_status_request = bailian_20231229_models.GetIndexJobStatusRequest(
        index_id=index_id,
        job_id=job_id
    )

    return client.get_index_job_status_with_options(workspace_id, get_index_job_status_request, headers, runtime)

# 查询知识库中文件
def list_indices(client, workspace_id):
    """
    获取文件的基本信息。

    参数:
        client (bailian20231229Client): 客户端（Client）。
        workspace_id (str): 业务空间ID。
        file_id (str): 文件ID。

    返回:
        阿里云百炼服务的响应。
    """
    headers = {}
    runtime = util_models.RuntimeOptions()
    list_indices_request = bailian_20231229_models.ListIndicesRequest()

    return client.list_indices_with_options(workspace_id, list_indices_request, headers, runtime)

# 追加文件进入知识库
def submit_index_add_documents_job(client, workspace_id, index_id, file_id, source_type="DATA_CENTER_FILE"):
    """
    向一个文档搜索类知识库追加导入已解析的文件。

    参数:
        client (bailian20231229Client): 客户端（Client）。
        workspace_id (str): 业务空间ID。
        index_id (str): 知识库ID。
        file_id (str): 文件ID。
        source_type(str): 数据类型。

    返回:
        阿里云百炼服务的响应。
    """
    headers = {}
    submit_index_add_documents_job_request = bailian_20231229_models.SubmitIndexAddDocumentsJobRequest(
        index_id=index_id,
        document_ids=[file_id],
        source_type=source_type
    )
    runtime = util_models.RuntimeOptions()
    return client.submit_index_add_documents_job_with_options(workspace_id, submit_index_add_documents_job_request, headers, runtime)

# 封装上传文件到百炼数据中心，并添加到指定分类
def upload_rag_file_to_bailian(client, workspace_id, category_id, file_path):
    """
    上传文件到百炼数据中心，并添加到指定分类

    参数：
        client: 百炼客户端
        workspace_id: 工作空间ID
        category_id: 分类ID
        file_path: 文件路径
    :return: 文件上传状态
    """
    # 1.申请文件租约
    lease = apply_lease_by_file_path(client, category_id, file_path, workspace_id)
    headers = lease.body.data.param.headers
    lease_id = lease.body.data.file_upload_lease_id
    upload_url = lease.body.data.param.url
    print("文件租约申请成功")
    print("headers:", headers)
    print("lease_id:", lease_id)
    print("upload_url:", upload_url)
    print()

    # 2.上传文件至百炼数据中心
    upload_file_to_bailian(upload_url, headers, file_path)

    # 3.将文件添加到指定分类
    add_file_response = add_file_to_bailian_category(client, lease_id, "DASHSCOPE_DOCMIND", category_id,
                                                     workspace_id)
    file_id = add_file_response.body.data.file_id
    print("文件添加到指定分类成功")
    print("file_id:", file_id)
    print()

    # 4.获取文件上传状态
    describe_file_response = describe_file(client, file_id, workspace_id)
    print("文件上传状态:")
    print("body:", describe_file_response.body)
    print()

    return file_id


# 封装 把文档上传添加到知识库中
def add_document_to_index(client, workspace_id, index_id, file_id):
    # 1. 获取job_id
    job_response = submit_index_add_documents_job(client, workspace_id, index_id, file_id)
    job_id = job_response.body.data.id

    # 2. 获取对应job_id的状态
    job_status = get_index_job_status(client, workspace_id, index_id, job_id)
    return job_status.body.data


if __name__ == '__main__':
    mcp.run(transport="stdio")
    # query_rag_from_bailian("查询终端的操作规范")

    # rag_file_path = ""
    # rag_category_id = "cate_bb4469b654ee4e8ea94b8022b81b0f83_15488790"
    # rag_workspace_id = "ws-9xp4ifbwnveexrw2"
    # bailian_client = create_client()
    #
    # upload_rag_file_to_bailian(bailian_client, rag_workspace_id, rag_category_id, rag_file_path)
    #
    # response = create_index(bailian_client, "智能体知识库", "",rag_workspace_id)
    # print(response)
    #
    # rag_index_id=""
    # job_response = submit_index(bailian_client, rag_index_id , rag_workspace_id)
    # job_id = job_response.body.data.id
    # print(job_id)
    # job_id = ""
    #
    # rag_job_id = ""
    # job_status = get_index_job_status(bailian_client, rag_workspace_id, rag_index_id, rag_job_id)
    # print(job_status.body.data)
    #
    # list_indices_response = list_indices(bailian_client, rag_workspace_id)
    # print(list_indices_response)
    #
    # rag_file_id = ""
    # response = submit_index_add_documents_job(bailian_client, rag_workspace_id, rag_index_id, rag_file_id)
    # print(response)