# tests/

Pytest test suite for the project.

## `test_sql_view_creation.py` — db/poc SQL 語法檢查

驗證 `db/poc/*.sql` 是否都能被 `pipeline.Pipeline.create_views()` 正確轉換成
`CREATE VIEW` 並建立成功。與直接呼叫 `Pipeline.create_views()` 不同的地方是：
本測試逐一（依照 DAG 相依順序）針對每個 SQL 檔案各自跑一次獨立的 pytest
測試案例，因此某一個 SQL 語法有誤時，pytest 的測試結果會直接標示出是哪個
`.sql` 檔案壞掉（測試 ID 即檔名），而不是像 `create_views()` 一樣遇到第一個
錯誤就整批中斷、看不出其他檔案的狀況。

### 為什麼用 `pgserver` 而不是真正的 Postgres

這個環境沒有可以連線的真實 Postgres（`db/docker-compose.yaml` 需要 Docker），
所以測試改用 [`pgserver`](https://pypi.org/project/pgserver/)（一個
純 `pip install` 即可用、內建 Postgres binary 的輕量套件）在測試時動態啟動
一個暫時的 Postgres instance 來模擬真實資料庫，測試結束即銷毀，不需要
Docker 或事先架設好的資料庫。

`pgserver` 內建的 Postgres 沒有 `http`、`pg_ivm`、`pg_cron` 這幾個編譯型
extension（這些無法單靠 `pip install` 取得）。`db/setting.sql` 原本會安裝
這些 extension，但 `Pipeline.create_views()` 建立 view 時只需要
`custom.*` 輔助函式與相關 schema **存在**（讓 view 的 SQL 能通過語法解析／
型別檢查），並不會真的執行 `http_get()` 對外發送請求。因此
`tests/conftest.py` 會先讀入真正的 `db/setting.sql`，過濾掉這三個
`CREATE EXTENSION` 陳述式、兩個 `http_set_curlopt()` 呼叫，以及兩個依賴
`pg_cron` 的 `public.job` / `public.job_run_details` view 定義，其餘內容
（schema、`custom.*` 函式、`mcp_reader` 權限設定）原封不動地在 `pgserver`
建立的資料庫上執行一次，讓測試環境盡量貼近正式環境。

若未來 `db/setting.sql` 有變動導致這些過濾規則對不上（例如陳述式數量不
符），`tests/conftest.py` 會直接丟出 `AssertionError` 提示需要更新過濾規則，
而不是默默漏掉或誤刪其他內容。

### 執行方式

```bash
pip install -r requirements.txt   # 內含 pgserver、pytest
pytest tests/test_sql_view_creation.py -v
```

第一次執行時 `pgserver` 會在暫存目錄初始化一份 Postgres data 目錄，之後同一
個 pytest session 內的所有測試共用同一個 instance（session-scoped fixture），
測試結束後自動清除。
