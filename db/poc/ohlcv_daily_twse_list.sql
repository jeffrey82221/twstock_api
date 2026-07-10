-- ohlcv_daily_twse_list
-- 上游：company_basic_info（過濾 market='上市'）
-- 用途：TWSE 上市每檔個股 × 每月一列的「日 K 事件母體」。搭配 TWSE STOCK_DAY endpoint
--       (per-stock-per-month payload，一次拉整月日 K) 使用；下游 raw_ohlcv_daily_twse 對每
--       (stk_code, month_start_date) 打一次 HTTP，一次拿回該股該月 20 上下個交易日 OHLCV。
--
-- 設計理念（rule 20 · 資料源分流）：TWSE `exchangeReport/STOCK_DAY` 與 TPEx
--   `tradingStock` 為不同資料源，故 seed 分成 twse_list / tpex_list 兩張，各自對應獨立
--   pop.<seed>，可設不同 batch_size 增量填滿。
--
-- 設計理念（rule 15 · 母體大小）：seed 從 listing_date 起 generate_series 到 CURRENT_DATE，
--   月粒度。以 ~1300 檔上市 × 平均 15 年 × 12 月 ≈ 234k 列 seed 上限。個股上市前月份不會產生
--   （generate_series 起點即為上市月）。
--
-- 設計理念（rule 3 / rule 16 · immutable + pg_ivm）：僅使用 IMMUTABLE building blocks
--   （make_date, EXTRACT, generate_series），無 http、無 JOIN、無 window。CURRENT_DATE 為
--   STABLE 於物化時求值一次；listing_date 由上游 custom.parse_iso_date（IMMUTABLE）供給。
--
-- 設計理念（rule 6 · rows 唯一）：(stk_code, month_start_date) 唯一 — 同一檔股票在同一個月
--   最多一列。generate_series step 為 INTERVAL '1 month' 且起點為月初，天然去重。
--
-- 設計理念（rule 13 · 不做過濾）：不排除 delisting、停牌、上櫃轉上市等情境；下游 raw 若拿到
--   空 payload（stat != 'OK' 或 data=[]）於正規化層自然攤平為 0 列。listing_date IS NOT NULL
--   例外：generate_series 起點必須非 NULL，屬技術性 guard、非業務過濾。
SELECT
    stk_code,
    generate_series(
        make_date(EXTRACT(YEAR FROM listing_date)::INT, EXTRACT(MONTH FROM listing_date)::INT, 1),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date
FROM {{ schema }}.company_basic_info
WHERE market = '上市'
  AND listing_date IS NOT NULL;
