from langchain_openai import ChatOpenAI

llm_qwen = ChatOpenAI(
    model="qwen3.8-flash",
    base_url="https://ws-9xp4ifbwnveexrw2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    streaming = True,
    # 流式空闲超时：默认 120s 偶尔误杀长回复（114 chunk 后网络抖动静默即被掐断），
    # 放宽到 300s；仍保留兜底——真死流会在 5 分钟后转为可捕获的异常而非永久卡住
    stream_chunk_timeout=300,
)