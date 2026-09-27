-- raw_institutional_net_buy_sell
-- 上游：institutional_monthly_list（每公司每月一筆 month_start_date）
-- 對應 endpoint: GET /api/institutional-net-buy-sell?stk_code={stk_code}&date={month_start_date}
-- 上游資料源：TWSE 三大法人買賣超日報 T86 / TPEx 三大法人買賣明細資訊 dailyTrade
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/institutional-net-buy-sell?stk_code=' || stk_code
            || '&date=' || custom.date_to_iso(month_start_date)::TEXT)::TEXT
    ) AS institutional_net_buy_sell,
    month_start_date AS as_of
FROM {{ schema }}.institutional_monthly_list
