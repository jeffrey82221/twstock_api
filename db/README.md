# db/ — 設計理念

本目錄是整套「以 View 實踐取資料 pipeline」的核心。底層思想：**把外部 HTTP 取資料這件事，當成可以用 SQL view 串起來的純資料處理流程**，並透過 incremental materialized view（pg_ivm）+ pg_cron 把資料慢慢落盤實體化，繞過 API rate limit。

## 目標

> **解決資料實體化問題**：一連串會涉及 HTTP 取資料的流程，能用慢慢長大的「母體表」逐步擴大取資料量，同時保留 View 開發的快速迭代。

具體規則：

- **使用 IMV（Incremental Materialized View, pg_ivm）**：讓一連串會涉及 HTTP calling 的資料處理流程，可以透過慢慢增加的 base 母體，逐步擴大取資料量。
- **Cronjob 週期性寫入原始資料**：用 `pg_cron` 排程把資料一點一點塞進母體表，解決 API rate limit issue。
- **PoC 階段允許 V / MV 混用，方便快速擴充**。
- **V 版與 MV 版放在不同 schema**，互不污染。
- **分析性加工層**最終會做出一層 V / MV 混用的 view 提供下游使用。

## Schema 分層

| Schema | 用途 | 主要物件 |
| --- | --- | --- |
| **`poc`** | 研發階段：用來研發 SQL，讓 view 可以串接出整理好的資料流程。允許 V / MV 混用、允許 raw view 直接呼叫 `http_get_content`。 | `VIEW`（含呼叫 `http_get_content` 的 `_list` / `raw_*`），詳見 [`poc/README.md`](./poc/README.md)。 |
| **`pop`**（Populated Materialized）| 生產階段：用 incremental materialized view（pg_ivm）讓資料取得慢慢把資料表實體化。 | `INCREMENTAL MATERIALIZED VIEW`，永遠從 `hidden.*` 母體表往下計算，**不直接呼叫 HTTP**。建表方法見 [`populated/README.md`](./populated/README.md)。 |
| **`hidden`** | 母體層：放 `pop` 各 MV 的「實體 base table」。由 cronjob 從上游慢慢搬列進來，每次新增列都會觸發 pg_ivm 對 `pop` 的下游 MV 做增量更新。 | `TABLE`（每個 `_list` view 都對應一張 `hidden` 表）。 |
| `custom` | schema 無關的 immutable helper function（`http_get_content`、`parse_iso_date`、`date_to_iso`、`trunc_year` …），由 `db/immutable_func.sql` 渲染並安裝。 | `FUNCTION`。 |

## 資料流動方式

```
                           外部 REST API
                                │
                                ▼ http_get_content（mutable）
              ┌─────────────────────────────────────┐
              │  poc.{_list, raw_*, view, …}        │  PoC 開發層
              │  V / MV 混用，可直接打 HTTP         │
              └─────────────────────────────────────┘
                                │
                  抽出 _list 的 schema 與內容
                                ▼
              ┌─────────────────────────────────────┐
              │  hidden.<list_name> 母體表          │  母體層
              │  ← pg_cron 週期性 INSERT            │
              │     從 pop.<list_name>_candidate    │
              │     或上游 _list view 慢慢加列      │
              └─────────────────────────────────────┘
                                │ INSERT 觸發
                                ▼ pg_ivm 增量重算
              ┌─────────────────────────────────────┐
              │  pop.{_list, raw_*, view, …}        │  生產層
              │  全部是 IMV；不直接打 HTTP          │
              │  ← 純粹建立在 hidden.* 母體表上     │
              └─────────────────────────────────────┘
                                │
                                ▼
                        下游分析性加工層
                       （V / MV 混合一層）
```

關鍵運作方式：

1. **PoC schema 用來「設計 SQL」**。任何新欄位、新資料源都先在 `poc` 寫成 view，串通整條鏈再說。`poc` 允許 mutable function，可以直接打 HTTP。
2. **`hidden` 是慢慢長大的母體**。pg_cron 排程每隔幾分鐘 / 幾小時跑一次，從 `_list` view 取出尚未進母體表的新列，`INSERT` 進 `hidden.<list_name>`。
3. **`pop` 是 IMV**。`hidden.<list_name>` 每多一列，pg_ivm 自動把下游所有依賴的 `pop.*` materialized view 做增量重算。`pop` 不直接呼叫 HTTP，所以重算很便宜。
4. **分析性加工層** 最後可以混合 `pop.*` MV 與少量即時 `poc.*` V，作為對外查詢用的 final layer。

## 為什麼要這樣設計

- **解掉「資料還沒爬完，下游就動不了」的痛點**：傳統一次性爬蟲跑完才有資料，這裡只要母體表有列，下游就有列。
- **解掉 API rate limit**：每個 cronjob tick 只塞 N 列進母體；爬資料量取決於排程頻率與每次批量，與 rate limit 解耦。
- **解掉 PoC 與 Prod 互相干擾**：`poc` 不會污染 `pop`，因為它們在不同 schema。
- **解掉「IMV 不支援 mutable function」的限制**：透過把所有 HTTP / mutable 行為集中在 `poc` 與 `hidden` 的 cronjob，讓 `pop` 的 IMV 只看到純粹的 SQL，符合 pg_ivm 對 IMMUTABLE 的要求。

## 目錄

- [`poc/`](./poc/) — PoC schema 的 SQL，每個檔對應 `poc` 下一個 view / `_list` view，詳見 [`poc/README.md`](./poc/README.md)。
- [`populated/`](./populated/) — 從 `poc` 對應出來的 `pop`（IMV）與 `hidden`（母體表）建立流程，詳見 [`populated/README.md`](./populated/README.md)。
- [`setting.sql`](./setting.sql) — 全域設定：建立 `http` / `pg_ivm` / `pg_cron` extension、建立 `poc` / `pop` / `hidden` / `custom` schema、設定 curl timeout 與 `work_mem`。
- [`immutable_func.sql`](./immutable_func.sql) — 由 `pipeline.py` 以 Jinja 渲染後逐 schema 安裝的 immutable helper（`{{ schema }}.http_get_content`、`{{ schema }}.parse_iso_date` …）。
- [`docker-compose.yaml`](./docker-compose.yaml) / [`Dockerfile`](./Dockerfile) / [`enable_pg_cron.sh`](./enable_pg_cron.sh) — 本機開發容器。

## 用到的 Postgres extension

| Extension | 用途 |
| --- | --- |
| `http` | 在 SQL 內呼叫 HTTP（`http_get_content` 包在 `custom.http_get_content` 變成 IMMUTABLE wrapper）。 |
| `pg_ivm` | Incremental Materialized View，`pop` schema 的所有 MV 都用這個。 |
| `pg_cron` | 排程把列從上游 `_list` view 搬進 `hidden.*` 母體表。 |
