# 实验报告

## 1. 实验目的

比较 6 种 Agent 协作模式在相同 10 轮连续任务下的通信开销、状态传递成本、记忆复用率和实际耗时. 通过 2×2+2 设计 (协议/NL × memory/no-memory + IPC 真实跨进程变体) 把不同机制的贡献拆开归因:

| 模式 | 通信 | memory | 进程 |
|---|---|---|---|
| `text` | NL 累积上下文 | ✗ | in-proc |
| `text_v2` | NL 每步独立 | ✗ | in-proc |
| `text_with_memory` | NL 每步独立 | ✓ | in-proc |
| `protocol_no_memory` | 结构化 JSON | ✗ | in-proc |
| `protocol` | 结构化 JSON | ✓ | in-proc |
| `protocol_ipc` | 结构化 JSON over AF_UNIX | ✓ | 1 coord + 4 worker 真实进程 |

`text_v2` 是主基线 (合理 NL). `text` 用作"如果系统不做任何上下文收敛"的最坏对照. 中间四种以 `text_v2` 为零点, 用消融把协议结构性收益和记忆复用收益拆开. `protocol_ipc` 用来证明把 in-proc 协议落地到真实多进程 + 共享内存后, 协议结构性收益没有衰减, 多出的耗时是跨进程系统调用代价.

## 2. 实验任务

实验使用 `tasks/continuous_tasks.json` 中的 10 轮任务：

- A1-A5：openEuler Python Web 服务部署链。
- B1-B5：CSV 数据分析链。

两组任务均包含规划、检索、执行、总结和记忆写入过程，且后续任务可复用前序任务中的执行策略、报告模板和排障经验。

## 3. 指标

主指标 (4 种模式都有):

| 指标 | 含义 |
|---|---|
| Agent 消息数 | 业务消息 + 握手消息总数 |
| 通信字符数 | 文本字符数 或 协议 JSON 字符数 |
| 估算 token | 按字符数 / 1.8 粗估 |
| 非文本状态传递次数 | 协议消息携带 `state_refs` 的总次数 |
| 状态字节数 | 被引用状态的累计字节规模 |
| 总耗时 | 完成全部任务的端到端耗时 |
| 本地语料检索次数 | 实际触发了 `_simulate_local_index_scan` 的任务数 |
| 记忆命中率 | 共享记忆检索命中 ≥1 条的任务比例 |

`protocol_ipc` 专属指标:

| 指标 | 含义 |
|---|---|
| `ipc_send_count` / `ipc_recv_count` | 跨进程帧数 |
| `ipc_bytes_sent` | AF_UNIX socket 实际出向字节 (含 4B 长度头) |
| `ipc_round_trip_avg_us` | coordinator 视角的单次请求-响应延迟 |
| `shm_alloc_count` | POSIX 共享内存块分配次数 |
| `shm_peak_bytes` | 同一时刻 /dev/shm 中本应用占用的字节峰值 |

## 4. 运行方式

跑全部 6 个模式, 每个重复 5 次取均值与标准差:

```bash
python scripts/run_benchmark.py --mode all --rounds 10 --repeat 5
```

`--repeat` 控制每个模式独立重跑次数, 每次跑都先清空对应 SQLite 文件以保证起点一致. 报告里耗时与 IPC 往返时间会展示 `mean ± std`.

## 4.1 记忆复用准确性

`tasks/continuous_tasks.json` 每个任务都标注了 `gold_prior_task_ids` — 即理论上应该被复用的前序任务集合. 跑完后, `mas_litebus/eval/accuracy.py` 把 task_id 通过 task→memory 映射翻译为 gold memory_ids, 与系统实际 `memory_refs` 求交集计算:

- **Precision@3**: 检索回来的记忆中, 有多少比例命中 gold
- **Recall@3**: gold 集合中, 有多少比例被检索到
- **F1@3**: P 和 R 的调和平均
- **MRR**: 检索结果列表中, 第一个 gold memory 命中的倒数排名 (1.0 = 永远第一位就是 gold)

这一指标只对启用 memory 的模式 (`text_with_memory` / `protocol` / `protocol_ipc`) 计算; 冷启动任务 (`A1`/`B1` 无前序) 不计入分母.

## 5. 结果

运行后查看：

```text
outputs/benchmark_report.md
outputs/benchmark_summary.json
```

报告与汇总 JSON 由 `mas_litebus/eval/report.py` 自动生成, 避免手填导致不可复现.

## 6. 分析口径

### 6.1 协议结构化收益 (protocol vs text_v2)

- 协议消息只传动作 / 参数 / 结果 / 能力 / state_refs / memory_refs, 字符与 token 明显低于 NL.
- embedding 走 `state_id` (in-proc) 或 `shm_name` (IPC), 不在消息体里铺开向量内容.

### 6.2 共享记忆复用收益 (protocol vs text_v2)

- protocol 与 protocol_ipc 都做了 `memory.search`. 当命中 ≥ 2 条记忆时 (`engine.py` / `ipc_engine.py` 中 `skip_local = len(memory_refs) >= 2`), 跳过本地语料 BLAKE2b 扫描.
- text / text_v2 模式不复用记忆 (`use_memory=False`), 因此每次都重跑本地扫描.

### 6.3 IPC 真实成本 (protocol_ipc vs protocol)

- `protocol_ipc` 与 in-proc `protocol` 模式的 `message_count` / `state_transfer_count` / `state_bytes` / `protocol_chars` 对齐, 证明协议层收益没有因 IPC 重构而衰减.
- 多出的耗时来自 socket 系统调用 + 共享内存 attach/detach + fork 子进程的固定开销, 是真实分布式系统该付的代价, 不能等同于 in-proc 模式的"理论延迟".

### 6.4 答辩可被质疑的点 (主动声明)

- 文本基线 (`text` 模式) 用累积上下文拼接, 通信开销被刻意放大. 真实多 Agent 系统通常会做某种程度的上下文收敛, 因此应主要参考 `text_v2` 与 `protocol` 的对比.
- 记忆命中是否"准确"未做 ground truth 评估; 当前 `memory_hit_rate` 只衡量"是否找到至少一条相似度 ≥ 阈值的历史记忆". `mas_litebus/eval/accuracy.py` 配合 `tasks/continuous_tasks.json` 里的 `gold_prior_task_ids` 计算 P/R/F1/MRR 作为额外信号.

## 7. LLM 接入 (真实 token 计数)

为了避免"系统层数据全是模板自圆其说"的质疑, 项目内置 LLM 接入开关:

```bash
python scripts/run_benchmark.py --mode all --llm ollama --llm-model llama3:8b --rounds 10
```

实现位于 `mas_litebus/llm/`:

- `base.py`: `LLMBackend` ABC + `LLMResponse` (含 `prompt_tokens` / `completion_tokens` / `latency_ms`)
- `ollama.py`: 调本地 Ollama HTTP `/api/chat`, 解析 `prompt_eval_count` / `eval_count` 作为真实 BPE token 数
- `prompts.py`: 每个 Agent 两套 prompt — `mode="text"` 嵌入累积 NL, `mode="protocol"` 嵌入紧凑 JSON
- `parse.py`: 容忍 markdown 围栏 / 尾逗号的 JSON 抽取, 失败时回退到模板

每个 Agent 构造时可选 `llm: LLMBackend | None`:

- `llm=None` (默认): 走原确定性模板, 兼容现有 11 个单元测试和无网评审环境.
- `llm=OllamaBackend(...)`: 每轮任务每个 Agent 发起一次 LLM 调用, 把真 BPE token 累加进 `Metrics.llm_prompt_tokens` / `llm_completion_tokens`.

### 7.1 真实 token 数据 (llama3:8b, 1 次 10 轮任务)

| 模式 | LLM prompt tokens (真 BPE) | LLM completion tokens | 单次平均延迟 ms | 总耗时 s |
|---|---:|---:|---:|---:|
| text (累积) | 15513 | 7343 | 3661 | 146.9 |
| text_v2 (合理基线) | 14096 | 7271 | 3624 | 145.3 |
| text + memory | 15474 | 6078 | 3719 | 123.3 |
| protocol (no memory) | 7711 | 4376 | 2218 | 88.9 |
| **protocol (full)** | **6376** | **3811** | **2300** | **76.5** |

**protocol 相对 text_v2 的真实 prompt token 节省: 14096 → 6376 = 54.8%**.

这是赛题 "通信效率: 相比纯文本协作的 **token 节省效果**" 的字面回答 — 用 Ollama 报回的真 tokenizer 计数, 不是 `chars/1.8` 估算.

### 7.2 Executor 沙箱

LLM 模式下 Executor 走 CodeAct: 让 LLM 生成 Python 片段, 投到 `mas_litebus/sandbox/runner.py` 实现的 subprocess 沙箱执行:

- 独立 Python 子进程, 工作目录在临时 tmpdir
- `resource.setrlimit` 限 CPU 秒 (4s) + 地址空间 (256 MB) + 文件描述符 (32)
- 父进程加 `subprocess.timeout=5s` 兜底
- 环境变量裁剪 (去掉 HTTP_PROXY), 防止生成代码出网

沙箱失败时 (语法错误 / 超时 / OOM) 自动回退到 `_template_execute`, 累加 `metrics.llm_parse_failures` 但不让流水线崩.

### 7.3 IPC + LLM 暂未整合

`protocol_ipc` 模式目前不接 LLM (报告里 LLM 行显示 "-"). 原因: worker 子进程通过 `fork()` 启动, 当时父进程的 `httpx.Client` 状态会被复制, 多 worker 同时往 Ollama 发请求容易触发连接池竞争. 让 worker 各自起新 client 是直接做法, 但需重写 worker 启动流程. 这是后续工作; 当前 IPC 模式跑确定性 Agent, 用来证明跨进程协议机制本身能跑.

