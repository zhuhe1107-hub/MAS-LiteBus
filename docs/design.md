# MAS-LiteBus 系统设计文档

## 1. 目标

MAS-LiteBus 面向多智能体协作中的系统层机制，验证三件事：

1. 使用结构化协议降低 Agent 间自然语言通信开销。
2. 使用 embedding 向量和状态引用实现非文本中间状态传递。
3. 使用共享记忆模块沉淀并复用跨任务经验。

系统同时支持纯文本协作模式和结构化协议协作模式，可在相同 10 轮连续任务上进行对比实验。

## 2. 架构

```text
MultiAgentRuntime
  |
  +-- PlannerAgent
  +-- RetrieverAgent
  +-- ExecutorAgent
  +-- SummarizerAgent
  |
  +-- ProtocolBus
  +-- StateStore
  +-- SharedMemoryStore
  +-- Evaluation Metrics
```

核心源码：

- `mas_litebus/runtime/engine.py`：多 Agent 运行时。
- `mas_litebus/runtime/protocol.py`：结构化通信协议。
- `mas_litebus/runtime/bus.py`：协议总线和通信统计。
- `mas_litebus/state/embedding.py`：哈希语义向量与状态池。
- `mas_litebus/memory/store.py`：SQLite 共享记忆库。
- `mas_litebus/eval/benchmark.py`：双模式评测。

## 3. Agent 设计

系统包含 4 个 Agent：

| Agent | 职责 |
|---|---|
| PlannerAgent | 任务拆解、调度计划、记忆复用决策 |
| RetrieverAgent | 本地证据检索、共享记忆检索、生成检索状态向量 |
| ExecutorAgent | 工具执行、CodeAct 风格 Python 片段、产物生成 |
| SummarizerAgent | 汇总结论、提取策略、写入共享记忆 |

每个 Agent 都暴露能力描述，协议模式下通过 `handshake` 消息上报。

## 4. 结构化通信协议

协议消息包含：

- `action`：动作类型。
- `params`：输入参数。
- `result`：返回结果。
- `capability_required`：能力需求或能力描述。
- `state_refs`：非文本状态引用。
- `memory_refs`：共享记忆引用。

示例：

```json
{
  "msg_id": "msg-xxx",
  "task_id": "A1",
  "from": "retriever",
  "to": "executor",
  "action": "retrieve.result",
  "params": {
    "query": "部署 Python Web 服务",
    "top_k": 3
  },
  "result": {
    "items": [
      {
        "title": "openEuler Python service deployment",
        "tags": ["openEuler", "Python", "deployment"]
      }
    ]
  },
  "state_refs": ["state-xxx"],
  "memory_refs": ["mem-xxx"]
}
```

## 5. 非文本状态传递

系统使用 `HashEmbedding` 将任务、证据和总结转为固定维度向量。向量不是通过自然语言长文本透传，而是存入 `StateStore`，Agent 消息只传递 `state_id`。

状态格式：

```json
{
  "state_id": "state-xxx",
  "producer": "retriever",
  "type": "embedding",
  "dim": 128,
  "dtype": "float32",
  "size_bytes": 512
}
```

生成方式：

1. 使用正则切分英文词、数字和中文单字。
2. 使用 BLAKE2b 哈希映射到固定维度桶。
3. 对向量做 L2 归一化。

后续使用：

- 与共享记忆向量计算余弦相似度。
- 作为下游 Agent 选择上下文的非文本状态引用。
- 统计状态传递次数和字节规模。

## 6. 共享记忆

共享记忆存储在 SQLite 中，每条记忆包含：

- `memory_id`
- `source_agent`
- `created_at`
- `task_topic`
- `summary`
- `tags`
- `evidence`
- `vector`
- `reuse_count`

支持：

- 关键词检索
- 标签检索
- 语义相似度检索

协议模式会在任务开始时生成任务 embedding，并执行共享记忆检索。命中记忆后，Planner 和 Retriever 会通过 `memory_refs` 复用历史经验。当命中不少于 2 条记忆时，系统会跳过本地语料重复检索，体现跨任务复用带来的计算节省。

## 7. 四模式对比

系统支持四种协作模式, 共用同一套 Agent 实现和同一套 10 轮任务, 通过运行时层差异制造可对比条件:

| 模式 | 通信媒介 | 状态传递 | 记忆复用 | 进程拓扑 |
|---|---|---|---|---|
| `text` | NL, **累积全部上下文** | 无 | 无 | 单进程函数调用 |
| `text_v2` | NL, 每步只传上一步产出 | 无 | 无 | 单进程函数调用 |
| `protocol` | 紧凑 JSON `to_json(separators=(",", ":"))` | `state_id` 引用 in-proc dict | 启用 | 单进程函数调用 |
| `protocol_ipc` | JSON over AF_UNIX socket (4B 长度头) | `shm_name` 引用 POSIX 共享内存 | 启用 | 1 coordinator + 4 worker 独立子进程 |

报告把 `text_v2` 作为基线 (而不是 `text` 累积基线), 以便答辩时能解释"协议本身的结构化收益"而非"刻意做差的文本基线带来的虚高"。

## 8. 跨进程协议 + 共享内存状态 (protocol_ipc)

`protocol_ipc` 模式把协议总线落到真实的多进程系统机制上:

### 8.1 进程拓扑

```text
                 ┌────────────────────────────────────┐
                 │ Coordinator Process (runtime)      │
                 │ - 任务编排, Metrics, 共享记忆写入 │
                 └──┬─────┬─────┬─────┬──────────────┘
                    │     │     │     │   AF_UNIX SOCK_STREAM
              ┌─────▼─┐ ┌─▼──┐ ┌▼───┐ ┌▼────────┐
              │planner│ │retr│ │exec│ │summarizer│
              └───────┘ └────┘ └────┘ └─────────┘
                              │     │
                              └──┬──┘
                                 │  POSIX shm (/dev/shm)
                          ┌──────▼──────┐
                          │ state pool  │
                          │ float32 ×128│
                          └─────────────┘
```

- Worker 启动方式: `multiprocessing.get_context("fork").Process(target=agent_worker_main, ...)`. Coordinator 通过 ready 文件等待 worker 完成 `bind+listen`, 再 connect.
- 每个 worker 独占一个 socket 路径 `/tmp/mas_litebus_ipc_<rand>/<agent>.sock`, 与 coordinator 1:1 长连接.
- Coordinator 中心化调度, 没有 agent-to-agent P2P, 简化 metrics 收集与故障定位.

### 8.2 协议帧格式

```text
[ 4 字节 big-endian uint32 长度 N ][ N 字节 UTF-8 JSON ]
```

JSON 内容字段与 in-process 协议模式一致 (`action / params / result / capability_required / state_refs / memory_refs / ...`), 由 `mas_litebus/ipc/socket_bus.py` 的 `encode_frame` / `recv_framed` 实现.

### 8.3 非文本状态传递

embedding 向量 (128 维 float32, 512 字节) 不经过 socket, 而是:

1. 生产方 (coordinator 或 retriever/summarizer worker) 在 `multiprocessing.shared_memory` 创建一个命名块 `mas_state_<uuid16>`, 写入 `array.array('f', vec).tobytes()`.
2. 协议消息只携带短字符串 `shm_name` (~20 字节) 作为引用.
3. 消费方按名 `SharedMemory(name=...)` attach, 直接读 float32 字节, 不需要 JSON 反序列化.
4. Coordinator 维护 `task_id → [shm_names]`, 任务结束时统一 `unlink`.
5. Worker 创建的 shm 用 `resource_tracker.unregister` 标记"由 coordinator 接管", 避免 worker 退出时误删尚未消费的块.

这就是赛题要求的"非文本中间状态在 Agent 间直接交换": socket 上只跑 ~20 字节的引用, 实际 512 字节向量本体留在 `/dev/shm`, 评审现场可用 `ls /dev/shm` 验证.

### 8.4 共享记忆并发

`SharedMemoryStore.__init__` 启用 SQLite WAL 模式. coordinator (memory.search) 与 summarizer worker (memory.write) 在不同进程对同一 .sqlite3 并发读写; WAL 保证读不阻塞写, 写串行化.

### 8.5 评测口径

`protocol_ipc` 模式与 `protocol` 模式的 `message_count` / `state_transfer_count` / `state_bytes` / `protocol_chars` 全部对齐, 证明协议层结构化收益没有因 IPC 重构而改变. 多出的指标:

- `ipc_send_count`, `ipc_recv_count`, `ipc_bytes_sent` — 包含 4 字节长度头的实际 socket 流量.
- `ipc_round_trip_us_sum`, `ipc_round_trip_avg_us` — coordinator 视角的请求-响应往返延迟.
- `shm_alloc_count`, `shm_peak_bytes` — POSIX 共享内存的分配次数和峰值占用.

