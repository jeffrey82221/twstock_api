-- raw_ohlcv_daily_twse
-- 上游：ohlcv_daily_twse_list（每 (stk_code, month_start_date) 一列）
-- 對應 endpoint：GET https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={YYYYMMDD}&stockNo={stk_code}
-- 上游資料源：TWSE 證交所 · 個股日成交資訊（per-stock-per-month）
--
-- 設計理念（rule 9 · one payload per event）：一個 (stk_code, month_start_date) 事件對應一列
--   payload。TWSE STOCK_DAY 一次回傳指定股票、指定月份的整月日 K（~20 個交易日 rows）。
--   相對「per-stock-per-day」設計，把 API request 數壓縮到 1/20；也支援歷史回填任意月份。
--
-- URL 設計：`date` 參數必須是「該月第一天」YYYYMMDD 字串；`stockNo` 為 4 碼股票代號。
--   TWSE 對 date 參數只看年月、忽略日；本 view 直接用 month_start_date 保證月初。
--
-- 設計理念（rule 3）：URL 組合僅使用 IMMUTABLE building blocks（custom.date_to_iso、REPLACE、
--   字串串接），http 呼叫走既有 IMMUTABLE wrapper custom.http_get_content。
--
-- payload 結構（供下游正規化參考）：
--   { stat: 'OK', title: '115年06月 2330 台積電...', fields: [...],
--     data: [['115/06/01', '60,942,792', '144,105,259,583', ...], ...],
--     total: N, notes: [...] }
--   stat != 'OK' 或個股該月未上市：data 為空陣列，正規化層 CROSS JOIN LATERAL 自然產出 0 列。
SELECT
    stk_code,
    month_start_date,
    custom.http_get_content(
        (
            'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date='
            || REPLACE(custom.date_to_iso(month_start_date), '-', '')
            || '&stockNo='
            || stk_code
        )::TEXT
    ) AS ohlcv
FROM {{ schema }}.ohlcv_daily_twse_list;
