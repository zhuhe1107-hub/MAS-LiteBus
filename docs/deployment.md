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

```bash
python scripts/run_benchmark.py --mode both --rounds 10
```

输出文件：

```text
outputs/benchmark_summary.json
outputs/benchmark_report.md
outputs/memory_text.sqlite3
outputs/memory_protocol.sqlite3
```

## 4. 单独运行某个模式

```bash
python scripts/run_benchmark.py --mode text --rounds 10
python scripts/run_benchmark.py --mode protocol --rounds 10
```

## 5. 运行测试

```bash
python -m unittest discover -s tests
```

## 6. 常见问题

如果运行目录不是项目根目录，请进入项目根目录再执行脚本。脚本会自动将项目根目录加入 `sys.path`。

如果想接入真实 embedding 模型，可在 `mas_litebus/state/embedding.py` 中替换 `HashEmbedding.encode` 实现，保持返回 `list[float]` 即可。

