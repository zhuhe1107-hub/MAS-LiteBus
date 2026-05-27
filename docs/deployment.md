# 部署文档

## 1. 环境要求

目标环境：

- openEuler 24.03-LTS-SP3
- Python 3.9 或更高版本，推荐 Python 3.11

核心版本仅使用 Python 标准库，无需联网下载模型或第三方依赖。

## 2. 安装

```bash
sudo dnf install -y python3 python3-pip sqlite git
cd mas-litebus
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` 当前为空依赖，保留该文件是为了便于后续扩展 FAISS、sentence-transformers 或 rich。

## 3. 运行基准实验

跑全部四种模式 (text / text_v2 / protocol / protocol_ipc):

```bash
python scripts/run_benchmark.py --mode all --rounds 10
```

输出文件：

```text
outputs/benchmark_summary.json
outputs/benchmark_report.md
outputs/memory_text.sqlite3
outputs/memory_text_v2.sqlite3
outputs/memory_protocol.sqlite3
outputs/memory_protocol_ipc.sqlite3
```

## 4. 单独运行某个模式

```bash
python scripts/run_benchmark.py --mode text --rounds 10
python scripts/run_benchmark.py --mode text_v2 --rounds 10
python scripts/run_benchmark.py --mode protocol --rounds 10
python scripts/run_benchmark.py --mode protocol_ipc --rounds 10
```

## 4.1 protocol_ipc 模式的系统要求

`protocol_ipc` 在 4 个独立子进程上跑, 需要:

- POSIX 共享内存挂载点 `/dev/shm`. openEuler 24.03-LTS-SP3 默认开启, 大小通常为 tmpfs 总内存的 50%, 远远满足 10 轮任务的 ~1.5 KB 峰值占用.
- Unix Domain Socket 支持 (`AF_UNIX`). 临时 socket 路径建在 `/tmp/mas_litebus_ipc_*/`, 程序结束时自动清理.
- 共享内存名前缀 `mas_state_`. 如果上次异常退出残留, 可执行 `rm -f /dev/shm/mas_state_*` 清理 (正常退出会自动 unlink, 不会残留).
- Python 3.9+ 的 `multiprocessing.shared_memory` (3.8+ 已具备). 在 openEuler 默认 Python 上无额外依赖.

### 4.1.1 平台限制 (硬性)

`protocol_ipc` 和 `tests/test_ipc.py` **仅在 Linux 上运行** (macOS 部分支持). Windows 不支持:

- Windows 没有 fork (`multiprocessing.get_context("fork")` 在 Windows 上会报错)
- Windows 没有 `/dev/shm` (POSIX 命名共享内存)
- 即使 Windows 10+ 支持 `AF_UNIX`, Python multiprocessing 的 worker 启动路径也不完整

代码在非 Linux/macOS 平台会在 `IPCMultiAgentRuntime.__init__` 立即 `RuntimeError`. 不会跑到一半再崩.

`--mode all` 和 `--mode ablation` 因为包含 `protocol_ipc`, 同样仅 Linux 可用. 其他模式 (`text` / `text_v2` / `text_with_memory` / `protocol_no_memory` / `protocol` / LLM 集成) 都是跨平台的.

赛题交付目标就是 openEuler 24.03-LTS-SP3 (Linux), 所以这个限制不影响评审.

## 5. 运行测试

```bash
python -m unittest discover -s tests
```

## 6. 常见问题

如果运行目录不是项目根目录，请进入项目根目录再执行脚本。脚本会自动将项目根目录加入 `sys.path`。

如果想接入真实 embedding 模型，可在 `mas_litebus/state/embedding.py` 中替换 `HashEmbedding.encode` 实现，保持返回 `list[float]` 即可。

