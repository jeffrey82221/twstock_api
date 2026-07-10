-- financial_month_twse_list
-- 上游：無外部（純 SQL generate_series × company_basic_info）
-- 用途：TWSE/MOPS 版月營收事件母體，與 FinMind 版 financial_month_list 分流。
--
-- 設計理念（rule 20）：不同資料源分流爬取 —— TWSE OpenAPI (t187ap05_L / mopsfin_t187ap05_O)
-- 與 MOPS t21sc03 的限流遠寬於 FinMind（FinMind 免費層 300 req/hr，t21sc03 有 24h server-side
-- cache 且無明顯 quota）。兩者共用同一個 seed 會讓快的被 FinMind 拖住 —— financial_month_list
-- 於 batch_size=1/min 時，全母體 (~570k 列) 需 ~396 天才能爬完。拆成兩張 _list 各自對應獨立
-- pop.<seed> 空表，可各自以不同的 batch limit 增量填充，互不阻塞。
--
-- 內容與 financial_month_list 完全一致 —— 拆分價值僅在於獨立 seed，讓 raw_monthly_revenue_twse
-- 可用大幅拉高的 batch_size 快速拉齊，raw_monthly_revenue（FinMind）保持保守進度。
SELECT
    stk_code,
    generate_series(
        make_date(EXTRACT(YEAR FROM incorporation_date)::INT, EXTRACT(MONTH FROM incorporation_date)::INT, 1),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date
FROM {{ schema }}.company_basic_info;
