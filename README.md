# AI_Agent

基于 LangGraph Supervisor 模式的多智能体协作系统，支持文件操作、数据库、Linux 沙盒、浏览器、RAG 知识库等工具。

## 架构

```
Supervisor (主管)
    ├── research_expert  信息检索：浏览器 + RAG 知识库
    ├── code_expert      代码工程：文件管理 + PowerShell 终端
    └── infra_expert     运维环境：MySQL 数据库 + Ubuntu SSH 沙盒
```

特性：

- **三层结构闸**：L1 重复/连错检测、L2 派遣次数上限（3 次/专家/轮）、L3 单派遣 20 次调用兜底，防空转不防 productive 长链
- **软警告窗口**：第 16 次调用时注入一次性 SystemMessage 给模型收尾机会，不污染历史
- **产物智能判定**：L3 硬截断时按 tool_call_id 配对识别成功写操作——有产物报 `[完成-待验证]`（主管派只读验证单），无产物报 `[待办]`
- **零工具空转守卫**：专家不调任何工具就想交回/报成功时，第一次强制其真正执行，二次空转统一转 `[待办]`——"假成功"在结构上无法向上传递
- **失败轮次自动回滚**：任务失败/超限/异常时删除本轮过程消息，保留用户提问与失败说明，历史不被污染
- **结构化汇报**：最终回复强制包含 `【状态】/【已完成】/【问题】/【建议】` 四段，成功失败一目了然
- **上下文防护**：单条超长消息截断、按 token 预算裁剪历史（80k token）、每轮持久化消息裁剪到 30 条
- **递归上限推导绑定**：`GRAPH_RECURSION_LIMIT`（560）由闸阈值推导（3 超步/工具调用 × 3 专家 × 3 派遣 × 20 调用），保证闸永远先于 GraphRecursionError 收尾
- **命名空间隔离 checkpoint**：多线程对话历史互不干扰，支持 `new` 命令开新会话
- **心跳机制**：长任务（>15s）期间定期提示执行中，流式输出不被超时中断（`asyncio.wait` 持久 future，绝不取消流）
- **优雅异常兜底**：API 配额/网络/工具异常只终止本轮，进程不崩溃，历史保留可续

## 专家与工具

| 专家 | 工具 |
|---|---|
| research_expert | `get_page_title`（取网页标题）、`open_webpage`（打开网页取标题+正文）、`search_in_baidu_with_html`（百度搜索）、`query_rag` 等知识库工具 |
| code_expert | 文件管理工具（根目录 D:\，`AGENT_FILE_ROOT` 可覆盖）、project_* 只读工具（根=项目目录）、PowerShell 终端 |
| infra_expert | 9 个 `mysql_*` 工具、7 个 `*_in_vm` SSH 沙盒工具（健康检查/目录/文件/权限/上传） |

## 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- MySQL 数据库（infra_expert 使用）
- Ubuntu 虚拟机 + SSH 免密登录（win_vm 沙盒使用）
- 浏览器驱动（research_expert 浏览器工具使用，首次运行自动下载）

## 安装

```bash
git clone <your-repo-url>
cd AI_Agent
uv sync          # 或：pip install -e .
```

## 配置

复制环境变量模板并填入真实密钥：

```bash
cp .env.example .env
```

`.env` 需要的密钥：

| 变量 | 用途 |
|---|---|
| `AMAP_KEY` | 高德地图 API |
| `GITHUB_TOKEN` | GitHub 相关工具 |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` | 阿里云百炼 LLM |
| `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 阿里云百炼 LLM |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` | MySQL 连接参数（infra_expert，有默认值） |
| `MYSQL_PASSWORD` | MySQL 密码（**必填**，infra_expert） |

**`.env` 绝不提交 Git**，已在 `.gitignore` 中忽略；源码中不得出现任何明文密钥。

## 启动

```bash
python main.py                    # 默认 unified 模式（推荐）
python main.py --mode unified     # 三专家多智能体
python main.py --mode multi       # 双专家多智能体
python main.py --mode single      # 单智能体（全部工具）
python main.py --thread-id 10     # 指定会话线程 ID
```

运行中输入：

- `new` — 开启全新会话线程（旧记录可按原 thread-id 恢复）
- `exit` / `quit` — 退出

## 项目结构

```
agent/                  智能体定义与编排
  unified_multi_agent.py    统一三专家系统（主）
  langgraph_code_agent.py   双专家（历史）
  code_agent.py             单智能体（历史）
model/                  LLM 实例（qwen）
mcp_tools/              MCP stdio 工具服务
tools/                  LangChain 工具适配层
rag/                    RAG 知识库
prompts/                提示词模板（unified 角色提示词集中在 unified_prompts.py；闸/守卫动态消息留在 agent 文件）
utils/                  公共工具
scripts/                运维脚本
.temp/                  运行时状态（checkpoint 等，gitignored）
backups/                自动备份（gitignored）
```

## 常见问题

**Q: 想调整专家的行为、职责边界或汇报话术？**
改 `prompts/unified_prompts.py`（三专家/主管/交接规则的角色提示词集中在此）。注意：提示词里列了各专家的工具清单，在 `mcp_tools/`/`tools/` 增删工具时须同步更新；闸与守卫动态生成的消息（如 `[完成-待验证]`、L2 拒收文本）不在此文件，在 `agent/unified_multi_agent.py` 的 middleware 中。

**Q: 运行时显示"工具调用已达 20 次硬上限"？**
这是三层结构闸的正常保护——专家在某子任务上重复调用（同参重试或连续报错）时被强制收尾。若本次派遣已有成功写操作，会报 `[完成-待验证]` 并由主管派只读验证单确认。可在 `agent/unified_multi_agent.py` 顶部调整 `MAX_TOOL_CALLS_PER_EXPERT` 等常量（注意 `GRAPH_RECURSION_LIMIT` 由这些常量推导，改闸值会联动）。

**Q: 专家回复"任务未执行：连续两轮未调用任何工具"？**
零工具空转守卫触发——专家被派遣后没调用任何工具就想交回结果。系统已自动拦截并把结果标记为 `[待办]`，主管会改派或拆小任务重试，无需人工处理；若频繁出现，通常是任务描述与专家职责不匹配（如让 code_expert 查数据库）。

**Q: 浏览器打开百度返回"百度安全验证"？**
百度对自动化浏览器的反爬拦截，属站点现象而非工具故障。浏览器工具（`open_webpage`/`get_page_title`）对必应等多数站点正常；验证浏览器能力时建议换用其他站点。

**Q: 提示"Free quota exhausted"？**
阿里云百炼免费额度耗尽。在百炼控制台充值，或改 `model/qwen.py` 换其他模型/端点。

**Q: 换电脑/换新线程后之前的对话没了？**
检查 `.temp/checkpoint/` 是否随项目一起迁移了；对话历史默认按 thread-id 隔离，启动时用 `--thread-id <原ID>` 恢复。
