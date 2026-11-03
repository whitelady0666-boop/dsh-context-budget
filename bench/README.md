# 测量脚本

全部脚本只用标准库 + `zstandard`（只有 `simulate.py` 需要）。不写死绝对路径，靠环境变量：

| 变量 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` | headless 会话的授权，必填 |
| `DSH_BIN` | dsh 可执行文件路径；不设就从 PATH 找 `dsh` |

Windows 上 `.cmd` 会自动套 `cmd /c`（见 `common.py`）。

## 怎么跑

```bash
cd bench

python analyze.py        # 只读 data/，把 CSV 汇总成表（不花钱，先跑这个）
python benchmark.py --memory ../MEMORY.md
python benchmark_long.py
python benchmark_long2.py
python simulate.py <session.v4.jsonl.zstd> --csv out/sim.csv
```

| 脚本 | 干什么 | 运行次数 | 产物 |
|---|---|---:|---|
| `benchmark.py` | 8 道题 × 3 组配置 × 2 次 | 48 | `data/benchmark.csv` |
| `benchmark_long.py` | 长任务 × 3 种记忆精度 × 6 次 | 18 | `data/benchmark-long.csv` |
| `benchmark_long2.py` | 长任务 × 2 种措辞 × 6 次 | 12 | `data/benchmark-long2.csv` |
| `simulate.py` | 真实会话日志 → 预算曲线 | 0（只读） | 终端表 / `--csv` |

`benchmark.py` 里那 8 道**示例题描述的是作者那台机器**（AstrBot 端口、NapCat 通道、我的目录），
换机器请改 `QS` 和 `ARTICLES`。判分是正则匹配最终回答，不匹配记 0 分。

## CSV 列

| 列 | 含义 |
|---|---|
| `arm` / `rep` / `q` | 组名 / 第几次重复 / 第几题 |
| `steps` | 模型调用步数（每个 `status:step_end` 事件一步） |
| `tools` | 工具调用次数（`tool_call` 事件数） |
| `miss` / `cache` / `out` | 未命中输入 / 缓存命中输入 / 输出 token |
| `equiv` | 等价成本 = `miss + cache*0.1 + out`，取整 |
| `correct` | 按正则判分，1 / 0 |
| `final` | 最终回答前 80 字（长任务里是 `step3` 的实际内容） |

## 一个已知的测量瑕疵

**BOM。** v1（`benchmark_long.py`）判分时不去 BOM，模型有两次把 `3` 写成带 BOM 的 `3`，被判失败，
于是 `benchmark-long.csv` 里 C 组是 4/6 而不是 6/6。v2 去掉了 BOM 再比，所以 `benchmark-long2.csv` 全是 6/6。
这个差别只影响"完成率"，不影响成本数字。

## 原始脚本

去密钥、去绝对路径之前的版本备份在仓库外（作者机器上的 `_bench_orig/`）。

**这个仓库里的脚本没有 API key**：所有授权都走 `DEEPSEEK_API_KEY` 环境变量。如果你是从别处 fork 来的，
先 `grep -r "sk-" .` 确认一下。
