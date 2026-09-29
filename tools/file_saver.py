import base64
import json
import os
import pickle
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Sequence, Any
from urllib.parse import quote

from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, Checkpoint, CheckpointMetadata, \
    ChannelVersions

# from agent.model_chat import user_input
from model.qwen import llm_qwen
from tools.file_tools import file_tools


class FileSaver(BaseCheckpointSaver[str]):
    def __init__(self, base_path: str = r"D:\Python\AI_Agent\.temp\checkpoint"):
        super().__init__()
        self.base_path = base_path

        os.makedirs(base_path, exist_ok=True)


    # ⭐ 命名空间感知：langgraph 的 saver 契约要求按 (thread_id, checkpoint_ns, checkpoint_id)
    #    三元组存储与检索。子图（supervisor 下的各专家 agent）会以自己的 checkpoint_ns
    #    （如 "code_expert:abc123"）写 checkpoint；若像旧版一样全部平铺在 thread 目录下，
    #    根图 get_tuple 按 mtime 取"最新文件"会拿到子图 checkpoint：
    #      1) astream 启动恢复时读到子图 channel 状态 → 根图无任何节点被触发（"本轮 0 步"）；
    #      2) aget_state 时 patch_checkpoint_map 需要 saved.config 里的 checkpoint_ns
    #         （子图 metadata.parents 非空时必读），缺失即 KeyError: 'checkpoint_ns'。
    #    ns 含 ":" / "|" 等 Windows 文件名非法字符，用 URL 编码转义；根图 ns 为空用 _root。
    @staticmethod
    def _ns_dirname(checkpoint_ns: str) -> str:
        return "_root" if not checkpoint_ns else quote(checkpoint_ns, safe="")

    # 获取存储的 JSON 文件路径 方法
    def _get_checkpoint_path(self, thread_id, checkpoint_ns, checkpoint_id):
        dir_path = os.path.join(self.base_path, str(thread_id), self._ns_dirname(checkpoint_ns))
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, f"{checkpoint_id}.json")
        return file_path

    # 序列化 Checkpoint 方法
    def _serialize_checkpoint(self, data) -> str:
        pickled = pickle.dumps(data)
        return base64.b64encode(pickled).decode("utf-8")

    # 反序列化方法
    def _deserialize_data(self, data):
        decoded = base64.b64decode(data)
        return pickle.loads(decoded)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
         # 1. 找到正确的 checkpoint 文件路径（按调用方指定的命名空间隔离）
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        requested_id = config["configurable"].get("checkpoint_id")
        # 2. 读取 checkpoint 文件内容
        dir_path = os.path.join(self.base_path, thread_id, self._ns_dirname(checkpoint_ns))
        if not os.path.isdir(dir_path):
            return None

        if requested_id:
            checkpoint_file_path = self._get_checkpoint_path(thread_id, checkpoint_ns, requested_id)
            if not os.path.exists(checkpoint_file_path):
                return None
            checkpoint_id = requested_id
        else:
            checkpoint_files = list(Path(dir_path).glob("*.json"))
            if not checkpoint_files:
                return None
            checkpoint_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            latest_checkpoint = checkpoint_files[0]
            checkpoint_id = latest_checkpoint.stem
            checkpoint_file_path = str(latest_checkpoint)

        # 3. 反序列化 checkpoint 数据
        with open(checkpoint_file_path, "r", encoding="utf-8") as checkpoint_file:
            data = json.load(checkpoint_file)

        checkpoint = self._deserialize_data(data["checkpoint"])
        metadata = self._deserialize_data(data["metadata"])
        parent_checkpoint_id = data.get("parent_checkpoint_id")

        # 4. 返回 CheckpointTuple
        #    ⭐ config / parent_config 必须回带 checkpoint_ns：langgraph 的
        #       patch_checkpoint_map 在 metadata.parents 非空（子图 checkpoint）时会
        #       读取 conf["checkpoint_ns"] 组装 checkpoint_map，缺键即 KeyError。
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_checkpoint_id,
                    }
                }
                if parent_checkpoint_id
                else None
            ),
        )



    def put(
            self,
            config: RunnableConfig,
            checkpoint: Checkpoint,
            metadata: CheckpointMetadata,
            new_versions: ChannelVersions,
    ) -> RunnableConfig:

        # 1.生成存储的 JSON 文件路径（按命名空间分目录，根图在 _root/）
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]

        checkpoint_path = self._get_checkpoint_path(thread_id, checkpoint_ns, checkpoint_id)


        # 2. 将 Checkpoint 进行序列化
        checkpoint_data = {
            "checkpoint": self._serialize_checkpoint(checkpoint),
            "metadata": self._serialize_checkpoint(metadata),
            "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
        }

        # 3. 将 Checkpoint 存储到文件系统
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(checkpoint_data, f, indent=2, ensure_ascii=False)

        # 4. 生成返回值（回带 checkpoint_ns，供上层继续在同一命名空间链式写入）
        return RunnableConfig(
            configurable={
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        )



    def put_writes(
            self,
            config: RunnableConfig,
            writes: Sequence[tuple[str, Any]],
            task_id: str,
            task_path: str = "",
    ) -> None:
        pass  # pending writes 不需要落盘：本 saver 只用于断点恢复对话状态

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        return self.put_writes(config, writes, task_id, task_path)


# memory = FileSaver()
#
# agent = create_agent(
#     model = llm_qwen,
#     tools=file_tools,
#     checkpointer=memory,
#     debug=False,
# )
#
# config = RunnableConfig(configurable={"thread_id": 1})
#
# while True:
#     user_input = input("用户：")
#
#     if user_input.lower() == "exit":
#         break
#
#     res = agent.invoke(input={"messages": "我是苏，你是谁？"}, config=config)
#     print("助手：", res["messages"][-1].content)