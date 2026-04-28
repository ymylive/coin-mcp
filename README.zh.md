# coin-mcp

[English](./README.md) · **中文**

> 一个全面的加密货币市场数据 MCP 服务器。6 个数据源、49 个工具、8 个 prompt 模板、3 个 resource —— 整合在一起，让 LLM 能用单次调用回答几乎任何"市场怎么样？"的问题。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.2%2B-green.svg)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](#开源协议)
[![Tests](https://img.shields.io/badge/tests-29%20passing-brightgreen.svg)](#测试)

---

## 目录

- [为什么是 coin-mcp](#为什么是-coin-mcp)
- [它能回答什么问题](#它能回答什么问题)
- [安装](#安装)
- [架构](#架构)
- [工具清单（49 个）](#工具清单49-个)
- [Prompt 模板（8 个）](#prompt-模板8-个)
- [Resources（3 个）](#resources3-个)
- [配置](#配置)
- [接入 MCP 客户端](#接入-mcp-客户端)
- [传输协议](#传输协议)
- [缓存层](#缓存层)
- [安全模型](#安全模型)
- [项目结构](#项目结构)
- [测试](#测试)
- [路线图](#路线图)
- [开源协议](#开源协议)
- [致谢](#致谢)

---

## 为什么是 coin-mcp

大多数加密 MCP 服务器只是单一 API 的薄封装，用户一旦问出需要"组合数据"的问题就立刻撞墙。`coin-mcp` 的出发点不同：一个真正能用的 AI 助手需要**互补**的数据源在统一契约下协作。

| 数据源 | 强项 | 在本项目中的覆盖范围 |
|---|---|---|
| **CoinGecko** | 聚合、按成交量加权 | 价格、市值、历史、NFT、分类、上市公司持仓、趋势、搜索 |
| **CCXT** | 实时、单交易所 | 跨 100+ 交易所的 ticker、订单簿、最近成交、OHLCV、资金费率 |
| **DefiLlama** | DeFi 原生、免费 | 协议/链 TVL、稳定币、Yield 池、DEX 量、费率/收入 |
| **DexScreener** | DEX 侧、长尾代币 | 新发行/小币种的交易对、流动性、价格 |
| **Alternative.me** | 情绪指标 | 加密货币恐慌贪婪指数 |
| **本地指标** | 离线计算 | RSI / MACD / Bollinger / EMA/SMA / ATR / ADX / Stochastic / OBV |

附带一个分层 HTTP 缓存（让你稳稳处于 CoinGecko 免费层的速率限制以下），以及多传输运行时（stdio / SSE / streamable-HTTP）。

## 它能回答什么问题

LLM 接入后能用 1–3 次工具调用完成的真实问题样例：

- "BTC 现在多少钱？" → `get_price`
- "对比 BTC 在 Coinbase / Kraken / OKX / DexScreener 的价格，告诉我价差。" → `compare_prices`
- "给我 30 天 K 线，跑 RSI / MACD / 布林。" → `get_market_chart` + `compute_indicators`
- "ETH/USDT 当前最佳买卖盘在哪个交易所？" → `get_consolidated_orderbook`
- "BTC 永续在 Binance / OKX / Bybit / Bitmex 的资金费率，谁付谁？" → `compare_funding_rates`
- "Solana 上 TVL 最大的协议是哪些？" → `list_protocols(chain="Solana")`
- "TVL > $5M、稳定币、APY > 10% 的 Yield 池有哪些？" → `list_yield_pools`
- "今天市场是恐慌还是贪婪？" → `get_fear_greed_index`
- "数据看起来怪怪的，所有源都正常吗？" → `health_check`
- "哪些上市公司持有 BTC？" → `get_companies_holdings`

## 安装

### 前置要求

- **Python 3.10+**（用 `python3 --version` 检查）
- **Git**
- 任选其一：
  - [`uv`](https://docs.astral.sh/uv/)（**推荐**，一个工具搞定 Python 工具链 + venv + 锁文件）
  - `pip` + 虚拟环境

`uv` 一行安装：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh        # macOS / Linux
# powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows PowerShell
```

### 方法 1 — uv（推荐）

```bash
git clone https://github.com/ymylive/coin-mcp.git
cd coin-mcp
uv sync                                # 自动建 .venv，按 uv.lock 装依赖
uv run coin-mcp                        # 用 stdio 启动服务
```

`uv sync` 会读取 `pyproject.toml` + `uv.lock` 把所有依赖（`mcp`、`httpx`、`ccxt`）装进项目本地的 `.venv/`，不污染系统环境。

### 方法 2 — pip + venv

```bash
git clone https://github.com/ymylive/coin-mcp.git
cd coin-mcp
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e .                       # 可编辑安装
coin-mcp                               # 启动
```

### 方法 3 — 直接从 GitHub 安装（不需要 clone）

```bash
uv tool install git+https://github.com/ymylive/coin-mcp.git
coin-mcp                               # 命令直接进 PATH
```

把 `coin-mcp` 装到全局 PATH，不留任何项目目录。最适合"我只想接到 Claude Desktop 用"的场景。

### 可选：装 dev / 测试依赖

如果想跑测试或改代码：

```bash
uv sync --extra dev                    # 加上 pytest + pytest-asyncio
uv run pytest                          # 29 个测试，应当全绿
```

### 验证安装

5 秒钟确认所有工具都注册了：

```bash
uv run python -c "
import asyncio, server
async def main():
    tools = await server.mcp.list_tools()
    prompts = await server.mcp.list_prompts()
    resources = await server.mcp.list_resources()
    print(f'{len(tools)} tools / {len(prompts)} prompts / {len(resources)} resources')
asyncio.run(main())
"
# 期望输出：49 tools / 8 prompts / 3 resources
```

CLI 帮助：

```bash
uv run coin-mcp --help
```

### 常见坑

| 报错 | 原因 / 解决方案 |
|---|---|
| `python3: command not found` | 没装 Python 3.10+。去 [python.org](https://www.python.org/downloads/) 装，或 `uv python install 3.12`。 |
| `ModuleNotFoundError: mcp` | 没在项目 venv 里运行。用 `uv run …` 或 `source .venv/bin/activate`。 |
| Binance 报 `Service unavailable from a restricted location` | 地理限制，不是 bug。改用 OKX / Kraken / Bybit 等（通过 `exchange_id` 参数）。 |
| CoinGecko 报 `HTTP 429` | 触发限流。缓存层会缓解；考虑设置 `COINGECKO_API_KEY` 提升限速。 |
| `uv: command not found` | 先装 uv（见前置要求），或者走方法 2 的 pip 路径。 |

## 架构

```
                        ┌──────────────────────────────┐
                        │       LLM / MCP 客户端       │
                        │   Claude · Cursor · 自定义   │
                        └──────────────┬───────────────┘
                                       │ JSON-RPC over stdio / SSE / HTTP
                        ┌──────────────▼───────────────┐
                        │       FastMCP 服务器         │
                        │   49 工具 · 8 prompts · 3   │
                        │   resources · 多传输支持     │
                        └──────────────┬───────────────┘
                                       │
                ┌──────────────────────┼──────────────────────┐
                │                      │                      │
       ┌────────▼─────────┐   ┌────────▼────────┐   ┌────────▼────────┐
       │ HTTP 层          │   │  CCXT 运行时    │   │  本地计算       │
       │ httpx + 分层     │   │  有界 LRU       │   │  指标库         │
       │ TTL LRU 缓存     │   │  + 单 id RLock  │   │  （无 I/O）     │
       │ + 鉴权感知 key   │   │  + 预热         │   │                 │
       └────────┬─────────┘   └────────┬────────┘   └─────────────────┘
                │                      │
   ┌────────────┼──────────────┐       │
   ▼            ▼              ▼       ▼
CoinGecko  DefiLlama  DexScreener   100+ 交易所
                                    (Binance, OKX, Coinbase,
                                     Kraken, Bybit, …)
```

关键设计选择：

- **一个 endpoint 一个工具，配丰富的 AI 友好 docstring。** 没用所谓的 endpoint 工厂——docstring 就是 LLM 用来选工具的 API 契约。49 个 docstring 是特性不是 bug。
- **`{"error": "..."}` 信封契约。** 每个 HTTP 工具要么返回上游 JSON，要么返回错误信封。缓存绝不缓存错误。LLM 那侧从不抛异常。
- **每个 URL 插值参数都有路径注入校验。** Coin ID、协议 slug、地址在发 HTTP 前必须通过严格正则。
- **单交易所 RLock + 预热 `load_markets()`。** 并发 CCXT 调用不会 race 懒加载的 markets 表。
- **鉴权感知缓存键。** 已知鉴权 header 的值参与缓存键 sha-256 哈希——多租户部署下不会串数据。

## 工具清单（49 个）

### 聚合市场数据 — CoinGecko（18）

| 工具 | 用途 |
|---|---|
| `get_price` | 单/多币当前价格，支持多种计价货币 |
| `get_coin_details` | 单币完整元数据：描述、链接、评分、市场数据、开发/社区指标 |
| `get_market_chart` | 历史价格 / 市值 / 成交量时间序列 |
| `get_aggregated_ohlc` | 跨所聚合 OHLC 蜡烛图 |
| `get_coin_tickers` | 单币在多个交易所的 ticker，按信任分/成交量排序 |
| `search` | 跨币种 / 交易所 / 分类 / NFT 通用搜索 |
| `list_top_coins` | 头部币种带完整市场数据，可排序+分页 |
| `get_trending` | 24 小时趋势币 / NFT / 分类 |
| `get_top_gainers_losers` | 24h 最大涨跌幅（Pro 端点；可回退到 `list_top_coins`） |
| `get_global_market` | 总市值 / BTC/ETH 市占率 / 活跃币数 |
| `get_global_defi` | 全球 DeFi 市值 + DeFi-to-ETH 比 |
| `list_categories` | 全部币种分类的聚合市场数据 |
| `list_exchanges_directory` | CEX 目录按信任分排名 |
| `get_exchange_info` | 单个交易所详情：描述、链接、ticker |
| `list_derivatives_exchanges` | 衍生品交易所按持仓量 / 成交量排名 |
| `list_nfts` | NFT collection 按地板价 / 市值 / 量排名 |
| `get_nft_collection` | 单个 NFT collection 详情 |
| `get_companies_holdings` | 持有 BTC / ETH 的上市公司 |

### 单交易所实时 — CCXT（7）

| 工具 | 用途 |
|---|---|
| `list_supported_exchanges` | 全部 111 个 CCXT 支持的交易所 ID |
| `get_exchange_markets` | 某交易所所有 markets / 交易对 |
| `get_exchange_ticker` | 单所 bid/ask/last/24h |
| `get_orderbook` | L2 订单簿快照 |
| `get_recent_trades` | 最近成交（盘口流） |
| `get_exchange_ohlcv` | 1m 起的 OHLCV，含单所成交量 |
| `get_funding_rate` | 永续当前资金费率 |

### 衍生品扩展 — CCXT（3）

| 工具 | 用途 |
|---|---|
| `get_funding_rate_history` | 永续资金费率时间序列 |
| `get_open_interest` | 当前未平仓量（带 history 回退） |
| `compare_funding_rates` | 跨所资金费率快照 + max/min/spread |

### DeFi 原生 — DefiLlama（9）

| 工具 | 用途 |
|---|---|
| `list_protocols` | 全部 DeFi 协议按 TVL 排名，可排序、可按链筛选 |
| `get_protocol_tvl` | 单协议详情 + 精简 TVL 历史 |
| `list_chains_tvl` | 各链 TVL |
| `get_chain_tvl_history` | 单链（或 DeFi 整体）TVL 历史 |
| `list_stablecoins` | 头部稳定币按市值 + 链分布 |
| `list_yield_pools` | 按 TVL / 项目 / 链 / 币种过滤的 Yield 池 |
| `list_dex_volumes` | DEX 24h 量排名 |
| `list_fees_revenue` | 按费率或收入排名的协议 |
| `get_token_dex_price` | DefiLlama 预言机价（用 `chain:address` 寻址） |

### DEX 侧 — DexScreener（5）

| 工具 | 用途 |
|---|---|
| `dex_search` | 跨链按代币名/符号/地址搜对 |
| `get_dex_token_pairs` | 某代币合约地址的全部交易对 |
| `get_dex_pair` | 按链 + 对地址查单对 |
| `list_latest_dex_tokens` | 新上 DexScreener profile 的代币 |
| `list_top_boosted_tokens` | 当前付费推广榜首代币 |

### 跨源聚合（3）

| 工具 | 用途 |
|---|---|
| `health_check` | 并发 ping 所有数据源 + 延迟 |
| `compare_prices` | 单币在 CG + 多 CEX + DEX 的价格对比 + 价差 |
| `get_consolidated_orderbook` | 跨所合并 L2，每价位标注来源 |

### 情绪（1）

| 工具 | 用途 |
|---|---|
| `get_fear_greed_index` | 加密恐慌贪婪指数（0 = 极度恐慌，100 = 极度贪婪） |

### 本地技术指标（1）

| 工具 | 用途 |
|---|---|
| `compute_indicators` | RSI / MACD / Bollinger / EMA / SMA / ATR / ADX / Stochastic / OBV — 纯 Python，作用于传入的 OHLCV |

### 缓存可观测（2）

| 工具 | 用途 |
|---|---|
| `cache_stats` | 缓存条数、命中、miss、命中率、按模式分组 |
| `clear_cache` | 清空进程内 HTTP 缓存 |

## Prompt 模板（8 个）

Prompt 是带参数的工作流模板，用户在 UI 里点选后会展开成多步指令让 LLM 用工具执行。

| Prompt | 用途 |
|---|---|
| `analyze_coin` | 单币完整简报：身份、价格、图表、可交易场所 |
| `compare_coins` | N 个币并列对比 |
| `technical_analysis` | 抓 OHLCV + 跑指标全套 + 总结趋势 / 动量 / 波动 |
| `scan_funding_arbitrage` | N 个交易所的资金费率 + 价差 |
| `market_overview` | 宏观简报：市值 / 市占率 / 情绪 / 趋势 / 头部涨跌 |
| `defi_health_check` | TVL by chain + 协议视图 + 稳定币概览 |
| `find_token_dex` | 先 search，找不到再回退 DexScreener |
| `yield_hunter` | 按 TVL / 链 / 币种过滤 Yield 池，按 30 日均 APY 排 |

## Resources（3 个）

客户端可挂载到 LLM 上下文的静态参考数据：

- `coin-mcp://exchanges/ccxt` — JSON 列表，所有 CCXT 支持的交易所 ID
- `coin-mcp://coins/popular-ids` — Markdown 表，常见 ticker → CoinGecko ID
- `coin-mcp://chains/dex-supported` — Markdown 列表，DexScreener 支持的链 ID

## 配置

所有环境变量都是可选的。

| 变量 | 默认 | 效果 |
|---|---|---|
| `COINGECKO_API_KEY` | _未设_ | 设置后服务器走 Pro 端点并签名请求。Pro key（前缀 `CG-`）发送 `x-cg-pro-api-key`；否则 Demo（`x-cg-demo-api-key`）。 |

项目附 `.env.example`，复制为 `.env` 并按需填写。

## 接入 MCP 客户端

### Claude Desktop

把这段加到 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）或 `%APPDATA%\Claude\claude_desktop_config.json`（Windows）：

```json
{
  "mcpServers": {
    "coin-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/coin-mcp",
        "run",
        "coin-mcp"
      ],
      "env": {
        "COINGECKO_API_KEY": "your-optional-key"
      }
    }
  }
}
```

重启 Claude Desktop。49 个工具、8 个 prompt、3 个 resource 都会出现。

### Cursor

Cursor 的 MCP 配置同 JSON 格式。把这段放进 Cursor 设置的 `mcp.servers`。远程使用就启动 `--transport sse` 或 `--transport streamable-http`，让 Cursor 指向 URL。

### 自定义客户端

任何兼容 MCP 的客户端都能用。服务端按 MCP 标准的 JSON-RPC 协议跑你选的传输。

## 传输协议

```bash
# stdio（默认，本地 IDE / Claude Desktop 集成）
coin-mcp

# Server-Sent Events
coin-mcp --transport sse --host 127.0.0.1 --port 8000

# Streamable HTTP（托管 / 远程使用）
coin-mcp --transport streamable-http --host 127.0.0.1 --port 8000

# 公网绑定（无认证）— 必须显式加 flag，且会输出 stderr WARNING
coin-mcp --transport streamable-http --host 0.0.0.0 --port 8000 --allow-public
```

服务器自身没有认证。若绑非 loopback 主机，**前面必须挂带认证的反代**（Caddy、nginx、Traefik、Cloudflare Access 等）或防火墙限制。`--allow-public` 是有意设计的"自找麻烦"开关。

## 缓存层

分层 TTL 缓存透明嵌在每个 HTTP 调用之后，让 LLM 可以放心 fan-out 而不烧光你的 CoinGecko 配额。

| 端点模式 | TTL |
|---|---|
| `/simple/price` | 10 秒 |
| `/coins/markets` | 60 秒 |
| `/market_chart`, `/ohlc` | 60 秒 |
| `/tickers` | 30 秒 |
| `/search/trending` | 60 秒 |
| `/search` | 5 分钟 |
| `/global` | 60 秒 |
| `/coins/{id}`（详情） | 2 分钟 |
| `/coins/categories` | 5 分钟 |
| `/exchanges/{id}` | 10 分钟 |
| `/exchanges`（列表） | 30 分钟 |
| `/derivatives/exchanges` | 10 分钟 |
| `/nfts/{id}` | 5 分钟 |
| `/nfts/list` | 30 分钟 |
| `/companies/public_treasury` | 30 分钟 |
| `api.alternative.me` | 10 分钟 |
| _默认_ | 30 秒 |

LRU 上限 2,000 条。缓存键包含 URL、排序后的查询参数、以及已知鉴权 header 值的 sha-256 摘要——两个不同 key 的客户端永远不会共享缓存条目。错误绝不缓存。命中时返回 deep copy，调用方可以放心修改不会污染原值。运行时通过 `cache_stats` 工具检查。

## 安全模型

`coin-mcp` 是纯数据项目——不签名交易、不持有私钥、不写链上。威胁面因此较窄，但仍诚实列出：

- **路径注入校验**覆盖每个 URL 插值参数（coin ID、slug、地址）。非法输入返回 `{"error": "invalid <kind>"}`，不发任何 HTTP。
- **`compute_indicators` 行数上限** 5,000（`MAX_OHLCV_ROWS`），防止上游被 prompt-injection 投毒导致内存炸裂。
- **CCXT 实例缓存有界**（LRU 16），LLM 不能强迫分配全部 111 个交易所。
- **网络传输默认拒绝非 loopback 绑定**，必须显式 `--allow-public`，且打开就会有 stderr WARNING。
- **缓存键鉴权感知** —— 鉴权 header 经 sha-256 摘要参与键。多租户安全。
- **stdio 模式无 stdout 污染** —— 所有日志走 stderr，JSON-RPC 帧从不被破坏。

发现安全问题请开 issue（非 trivial 情况下），别直接 PR。

## 项目结构

```
coin-mcp/
├── server.py                   # 入口 — 串联各模块
├── pyproject.toml
├── README.md / README.zh.md
├── .env.example
├── .gitignore
├── coin_mcp/
│   ├── core.py                 # FastMCP 实例、helpers、指引文本
│   ├── coingecko.py            # 18 个 CoinGecko 工具
│   ├── ccxt_tools.py           # 7 个 CCXT 工具
│   ├── derivatives.py          # 3 个资金费率/OI 工具
│   ├── defillama.py            # 9 个 DefiLlama 工具
│   ├── dexscreener.py          # 5 个 DexScreener 工具
│   ├── aggregate.py            # 3 个跨源聚合工具
│   ├── sentiment.py            # 1 个恐慌贪婪指数
│   ├── indicators.py           # 1 个本地指标计算
│   ├── cache.py                # 2 个缓存自省工具 + 缓存实现
│   ├── prompts.py              # 8 个工作流 prompt
│   ├── resources.py            # 3 个静态 resource
│   └── transport.py            # CLI（stdio / SSE / streamable-HTTP）
└── tests/
    ├── conftest.py             # 自动清缓存 + mcp_server fixture
    ├── test_registry.py        # 工具计数 / 指引表 / prompt 引用合法性
    ├── test_cache_routing.py   # TTL 路由表 + 无遮蔽
    ├── test_indicators.py      # Wilder RSI 教科书向量、5 列回退、DoS 上限
    ├── test_http_envelope.py   # is_error truthy 语义 + 错误不缓存
    └── test_validators.py      # 路径注入拒绝（CG / DexScreener）
```

## 测试

```bash
uv sync --extra dev
uv run pytest
# 29 passed
```

高 ROI 测试覆盖：

- 每个注册工具都出现在 `instructions` 表里（LLM 可发现性）
- 每个 prompt body 引用的工具名 / 参数名都真实存在
- 17 条代表性 URL 的缓存 TTL 路由
- 缓存无遮蔽（`/coins/markets` vs `/coins/{id}` vs `/market_chart`）
- Wilder RSI 命中教科书 14 close 参考向量（≈70.46）
- 5 列 OHLCV 输入不会让 OBV 崩
- `compute_indicators` 拒绝 > 5,000 行
- `is_error` truthy 语义
- HTTP 错误响应不被缓存
- 路径注入在发 HTTP 前被拒

## 路线图

已记录但未实现：

- 大额转账监控（Whale Alert）
- Etherscan / 区块浏览器风格的只读 RPC
- 新闻 + 社交聚合（CryptoPanic / Santiment）
- 跨所聚合持仓量历史
- 可选 READ-ONLY 签名请求支持更高级别 API key

如果有具体想要集成的数据源，开 issue 附上 API 规格。

## 开源协议

MIT —— 完整文本见 [LICENSE](./LICENSE)。

## 致谢

站在巨人肩膀上。

- [Model Context Protocol](https://modelcontextprotocol.io/) —— Anthropic 的 AI ↔ 工具开放标准
- [CoinGecko API](https://www.coingecko.com/en/api) —— 互联网上最慷慨的免费加密数据层
- [CCXT](https://github.com/ccxt/ccxt) —— 让 100+ 交易所感觉像一个的统一库
- [DefiLlama](https://defillama.com/) —— 作为公共品的 DeFi 数据基础设施
- [DexScreener](https://dexscreener.com/) —— 无需鉴权的 DEX 侧市场数据
- [Alternative.me](https://alternative.me/crypto/fear-and-greed-index/) —— 加密恐慌贪婪指数

以及所有研究过设计的前辈 MCP 项目：
[doggybee/mcp-server-ccxt](https://github.com/doggybee/mcp-server-ccxt) 的分层缓存模式、
[QuantGeekDev/coincap-mcp](https://github.com/QuantGeekDev/coincap-mcp) 提示了 prompts 的价值、
[heurist-network/heurist-mesh-mcp-server](https://github.com/heurist-network/heurist-mesh-mcp-server) 多传输先例、
官方 [CoinGecko MCP](https://docs.coingecko.com/docs/mcp-server) 的动态工具发现这个值得后续追踪的方向。
