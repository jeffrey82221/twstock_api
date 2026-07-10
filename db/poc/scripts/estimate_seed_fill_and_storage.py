"""
Seed fill-time and storage estimation for db/poc/*.

Assumptions (documented explicitly in the resulting Markdown):
- pg_cron 每 1 分鐘一次 (period_minutes=1)
- 每 tick 每張 seed insert `batch_size.json` 中對應的 row_cnt
- 每家上市/上櫃公司 = ~1,900（TWSE ~1,030 + TPEx ~870）
- 每家公司平均上市以來月數 (financial_month):
    產業母體約覆蓋公司自成立起，中位公司 20+ 年 → 平均 300 月/家
- 年頻母體：中位公司 25 年
- 季頻 yfinance：yfinance 提供最近 5 年（20 季）
- 除息事件：中位公司 20 年 × 平均 0.75 event/年 = 15 event
- product_revenue_filer: 近 5 年 = 60 個月 × 2 個市場（sii + otc） × 平均 200 家/月 有申報 = 24,000
- product_revenue_filer_scope: 60 個月 × 2 市場 = 120（超小）
- chain_list: 47 條產業鏈
- chain_info_list: ~2,200 條 (chain × 公司 fan-out)
"""

# Population assumptions ---------------------------------------------------
COMPANIES = 1900
LISTED_YEARS_MEDIAN = 25
LISTED_MONTHS_AVG = 300
YFIN_QUARTERS = 20  # yfinance 提供 20 季
DIV_EVENTS_PER_CO = 15
FILER_SCOPE_MONTHS = 60
MARKETS = 2
FILER_AVG_PER_SCOPE = 200
CHAINS = 47

# Row counts per seed ------------------------------------------------------
POP = {
    "product_revenue_filer_scope_list": FILER_SCOPE_MONTHS * MARKETS,
    "product_revenue_filer_list": FILER_SCOPE_MONTHS * MARKETS * FILER_AVG_PER_SCOPE,
    "dividend_event_list": COMPANIES * DIV_EVENTS_PER_CO,
    "dividend_event_yfinance_list": COMPANIES * DIV_EVENTS_PER_CO,
    "financial_year_list": COMPANIES * LISTED_YEARS_MEDIAN,
    "financial_year_yfinance_list": COMPANIES * LISTED_YEARS_MEDIAN,
    "financial_quarter_yfinance_list": COMPANIES * YFIN_QUARTERS,
    "chain_list": CHAINS,
    "chain_info_list": 2200,  # 展開後 (chain × 公司)
    "company_list": COMPANIES,  # DISTINCT stk_code
    "financial_month_list": COMPANIES * LISTED_MONTHS_AVG,
    "financial_month_twse_list": COMPANIES * LISTED_MONTHS_AVG,
}

# batch_size.json (row_cnt per tick, period=1min) --------------------------
# 從 repo root 的 batch_size.json 讀取，避免顯化值漂移。
import json
import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
with open(os.path.join(_REPO_ROOT, "batch_size.json"), encoding="utf-8") as _f:
    BATCH = json.load(_f)

# Per-row storage in downstream raw_* (bytes, approx) ---------------------
# raw_* 主要體積是 JSONB payload。每 seed 對應的 raw view payload 大小估算：
JSONB_BYTES = {
    "product_revenue_filer_scope_list": 6_000,   # filers 陣列每列 ~200 家 × ~15 bytes/name + wrapping
    "product_revenue_filer_list": 3_000,         # /product-revenue endpoint 單月產品線 JSON
    "dividend_event_list": 800,                  # 單一 event dividend payload
    "dividend_event_yfinance_list": 700,
    "financial_year_list": 4_000,                # 年報 JSON（annual EPS, revenue, net_income, margins, growth）
    "financial_year_yfinance_list": 3_500,
    "financial_quarter_yfinance_list": 2_500,
    "chain_list": None,                          # chain_list 本身是 seed 無 raw
    "chain_info_list": None,                     # chain_info_list 本身是 seed 無 raw
    "company_list": None,                        # company_list 只 join,無獨立 raw
    "financial_month_list": 1_200,               # revenue JSON: latest_month_value, yoy_pct, ttm_value 等
    "financial_month_twse_list": 1_200,
}

# Seed 自身平均 row 大小（BTree index + heap tuple）— for pop.<seed>
SEED_ROW_BYTES = 60  # (stk_code TEXT ~10, DATE 4, tuple header + row alignment ~40)

# Non-seed raw_* that don't have _list bound (fetched via company_list) ----
# raw_company_info: 每家一列
# raw_chain_info: 每條產業鏈一列
# raw_dividend_history*: 每家一列，內含事件陣列
# 這幾個列數 = 上游 seed 列數,不算成獨立 seed
STANDALONE_RAW = {
    # name: (rows, jsonb_bytes_per_row, upstream_seed_or_source)
    "raw_company_info": (COMPANIES, 5_000, "company_list"),
    "raw_chain_info": (CHAINS, 12_000, "chain_list"),  # 展開前完整 segments dict
    "raw_dividend_history": (COMPANIES, 12_000, "company_list"),  # events 陣列 15 筆
    "raw_dividend_history_yfinance": (COMPANIES, 12_000, "company_list"),
}

# --- Compute fill time and storage ---
def human_days(days):
    if days < 1:
        return f"{days*24:.1f} 小時"
    if days < 60:
        return f"{days:.1f} 天"
    return f"{days:.1f} 天 (~{days/30:.1f} 月)"

def human_bytes(b):
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

MIN_PER_DAY = 60 * 24

lines = []
lines.append("| seed | 母體列數 | 母體估算方法 | batch_size (row/min) | 填滿時間 | raw payload / 列 | 填滿後 raw 存放 | seed 本體存放 | 總計 |")
lines.append("| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |")

# Population estimation notes (Chinese)
NOTES = {
    "product_revenue_filer_scope_list": "近 5 年 60 月 × 2 市場 (sii+otc) — 由 `product_revenue_filer_scope_list.sql` 的 `INTERVAL '5 years'` × `VALUES('sii'),('otc')` 卡氏積直接產生",
    "product_revenue_filer_list": "60 月 × 2 市場 × 平均 ~200 家申報 = 24,000 — 每列來自 `raw_product_revenue_filers.filers.co_ids` 攤平",
    "dividend_event_list": "1,900 家 × 平均 15 event/家 (20 年 × 0.75 除息/年) — 由 `raw_dividend_history.events` 陣列 fan-out",
    "dividend_event_yfinance_list": "同上（yfinance 版），母體相同但資料源不同",
    "financial_year_list": "1,900 家 × 25 年（中位上市年數）— 由 `generate_series(incorporation_date, CURRENT_DATE, '1 year')` 產生",
    "financial_year_yfinance_list": "與 financial_year_list 內容完全一致（rule 20 分流），僅 seed 表獨立",
    "financial_quarter_yfinance_list": "1,900 家 × 20 季（yfinance 提供近 5 年）— 由 `raw_quarterly_financials_yfinance` 已抓的 quarter list 反查",
    "chain_list": "47 條產業鏈 — 由 `/api/chains` 一次抓完（服務內硬編碼 IC_CHAINS 常數）",
    "chain_info_list": "~2,200 = 47 鏈 × 平均 47 家/鏈 — 由 `raw_chain_info.segments` 3 層 LATERAL 展開（rule 21）",
    "company_list": "1,900 = 上市 ~1,030 + 上櫃 ~870 — 由 `chain_info_list` DISTINCT 而來",
    "financial_month_list": "1,900 家 × 300 月（平均上市月數，中位 25 年）— 由 `generate_series(incorporation_date, CURRENT_DATE, '1 month')`",
    "financial_month_twse_list": "與 financial_month_list 內容完全一致（rule 20 分流），僅 seed 表獨立",
}

total_raw_bytes = 0
total_seed_bytes = 0

for name, rows in POP.items():
    bs = BATCH[name]
    minutes = rows / bs
    days = minutes / MIN_PER_DAY
    jb = JSONB_BYTES[name]
    raw_bytes = rows * jb if jb else 0
    seed_bytes = rows * SEED_ROW_BYTES
    total_raw_bytes += raw_bytes
    total_seed_bytes += seed_bytes
    raw_col = f"~{jb:,} B" if jb else "—"
    raw_total_col = human_bytes(raw_bytes) if raw_bytes else "—"
    combined = human_bytes(raw_bytes + seed_bytes)
    lines.append(
        f"| `{name}` | {rows:,} | {NOTES[name]} | {bs} | {human_days(days)} | {raw_col} | {raw_total_col} | {human_bytes(seed_bytes)} | {combined} |"
    )

# Standalone raw section
lines2 = []
lines2.append("| raw view | 列數 | 母體來源 | payload / 列 | 存放空間 |")
lines2.append("| --- | ---: | --- | ---: | ---: |")
for name, (rows, jb, upstream) in STANDALONE_RAW.items():
    b = rows * jb
    total_raw_bytes += b
    lines2.append(f"| `{name}` | {rows:,} | {upstream} | ~{jb:,} B | {human_bytes(b)} |")

print("=== SEED TABLE ===")
for l in lines:
    print(l)
print()
print("=== STANDALONE RAW ===")
for l in lines2:
    print(l)
print()
print(f"總計 raw JSONB 空間  : {human_bytes(total_raw_bytes)}")
print(f"總計 seed 本體空間   : {human_bytes(total_seed_bytes)}")
print(f"總計                 : {human_bytes(total_raw_bytes + total_seed_bytes)}")
