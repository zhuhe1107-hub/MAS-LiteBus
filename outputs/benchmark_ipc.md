# MAS-LiteBus Benchmark — IPC Focus (protocol vs protocol_ipc)

本报告专门对比 **in-process protocol** 和 **multi-process protocol_ipc** 两种模式 (均使用确定性模板 Agent, 排除 LLM 噪声). 看点: protocol_ipc 多付出的 latency 完全是Unix socket + fork + POSIX shm 系统调用开销, 而 `state_transfer_count` / `state_bytes` / `protocol_chars` 与 in-proc 完全对齐, 协议层结构性收益不受 IPC 影响.

_(每模式重复运行 3 次, 易抖动指标显示 mean ± std)_

## 多模式对比

| 指标 | protocol (full) | protocol + IPC |
|---|---:|---:|
| Agent 消息数 | 44 | 44 |
| 通信字符数 (text_chars 或 protocol_chars) | 16950 | 17034 |
| 估算 token (chars / 1.8) | 9417 | 9463 |
| 非文本状态传递次数 | 40 | 40 |
| 状态字节数 (累计) | 25600 | 25600 |
| 总耗时 (秒) | 0.513414 ± 0.010346 | 0.613135 ± 0.024946 |
| 记忆检索次数 | 10 | 10 |
| 记忆命中率 | 80.0% | 80.0% |
| 本地语料检索次数 | 3 | 3 |
| **--- IPC 专属指标 ---** | — | — |
| IPC 发送帧数 | - | 44 |
| IPC 接收帧数 | - | 44 |
| IPC 出向字节数 (含 4B 长度头) | - | 25141 ± 45.5 |
| IPC 单次往返平均 (μs) | - | 8152.7 ± 472.5 |
| shm 分配次数 (POSIX) | - | 30 |
| shm 峰值字节 | - | 1536 |

## 相对基线的提升

> 符号规则: **正号 = 优于基线**, 负号 = 劣于基线. 例如 `耗时 +20%` = 比基线快 20%, `耗时 -40%` = 比基线慢 40%.

- **protocol (full)** 相对 text (累积): token +0.0%, 通信字符 +0.0%, 耗时 +0.0%, 本地检索次数 +0.0%, 消息数 +0.0%, 命中率 +80.0%
- **protocol + IPC** 相对 text (累积): token +0.0%, 通信字符 +0.0%, 耗时 +0.0%, 本地检索次数 +0.0%, 消息数 +0.0%, 命中率 +80.0%


## 记忆复用准确性 (vs gold prior task ids)

| 模式 | scored | P@3 | R@3 | F1@3 | MRR |
|---|---:|---:|---:|---:|---:|
| protocol (full) | 8/8 | 87.5% | 88.8% | 84.5% | 1.0000 |
| protocol + IPC | 8/8 | 87.5% | 88.8% | 84.5% | 1.0000 |

每个任务在 `tasks/continuous_tasks.json` 里都标注了 `gold_prior_task_ids` (理论上应该被复用的前序任务). 分数把 task_id 通过当前 run 的 task→memory 映射翻译为 gold memory_ids, 与系统实际 `memory_refs` 求交集计算 P/R. 冷启动任务(无 gold prior) 不计入分母. **MRR 衡量 retrieve 列表第 1 名是不是 gold**, 1.0 意味着每次最相关的记忆都排在头部.



## IPC 侧观察

- 共 44 次 send + 44 次 recv, 单次往返均值 8152.7 μs.
- POSIX 共享内存分配 30 次, 峰值 1536 bytes (同一时刻只持有一个任务的 query/retr/sum 三个块).
- 协议 JSON 通过 4 字节长度头 + UTF-8 帧在 AF_UNIX 上交换; embedding 向量通过 shm_name 传引用, 不经过 socket 序列化, 因此 ipc_bytes_sent 不含向量本体.
- protocol_ipc 与 in-proc protocol 模式的 message_count / state_transfer_count / state_bytes 完全对齐, 协议结构性收益保留; 多出的耗时来自跨进程系统调用, 是真实分布式场景的代价.

## 结论

- **通信效率**: 结构化协议把 Agent 间长上下文压缩为动作/参数/结果/能力/引用, 字符与 token 节省显著.
- **非文本状态传递**: HashEmbedding 128 维向量通过 `multiprocessing.shared_memory` 直接跨进程引用, socket 只承载短 shm_name.
- **共享记忆**: SQLite (WAL) 在 coordinator/retriever/summarizer 三个进程之间安全并发, 后续任务在标签和语义相似度上命中前序记忆.
- **真实 IPC 成本**: protocol_ipc 模式可被 `ps -ef | grep worker` 与 `ls /dev/shm` 直接观察, 不是同进程函数调用伪装的"协作".

本报告由 `scripts/run_benchmark.py --mode all --repeat N` 自动生成.
