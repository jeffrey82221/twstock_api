-- raw_ohlcv_daily_tpex
-- 上游：ohlcv_daily_tpex_list（每 (stk_code, month_start_date) 一列）
-- 對應 endpoint：GET https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code={stk_code}&date={YYYY/MM/DD}&id=&response=json
-- 上游資料源：TPEx 櫃買中心 · 個股日成交資訊（per-stock-per-month）
--
-- 設計理念（rule 9 · one payload per event）：一個 (stk_code, month_start_date) 事件對應一列
--   payload，一次回傳指定股票、指定月份的整月日 K（~20 個交易日 rows）。API request 數壓縮至 1/20，
--   支援任意歷史月份回填。個股上櫃前月份 stat 仍為 'ok' 但 tables[0].data=[]，正規化層攤平 0 列。
--
-- URL 設計：TPEx 用 `date` 為 `YYYY/MM/DD` 字串（與 TWSE 的 YYYYMMDD 格式不同），`code` 為股票代號。
--
-- payload 結構（供下游正規化參考）：
--   { stat: 'ok', code: '6488', name: '環球晶', date: '20260601',
--     tables: [{ title: '個股日成交資訊', subtitle: '6488 環球晶 115年06月',
--                fields: [...], data: [['115/06/01', '4,452', '4,333,718', ...], ...],
--                totalCount: N }],
--     ... }
--   注意：tables 是陣列（可能多表），本次 endpoint 固定回單表；下游取 tables->0->'data'。
SELECT
    stk_code,
    month_start_date,
    custom.http_get_content(
        (
            'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code='
            || stk_code
            || '&date='
            || REPLACE(custom.date_to_iso(month_start_date), '-', '/')  -- YYYY-MM-DD → YYYY/MM/DD
            || '&id=&response=json'
        )::TEXT
    ) AS ohlcv
FROM {{ schema }}.ohlcv_daily_tpex_list;
