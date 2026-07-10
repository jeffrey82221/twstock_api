"""櫃買中心『產業價值鏈資訊平台』(https://ic.tpex.org.tw/) 爬讀與索引。

- 一次抓取 ~46 個產業鏈頁面（每頁 ~290 KB），用 BeautifulSoup 解析。
- 建立兩種索引：
    * chain_tree[ic_code] = {
          name, segments: { 上游/中游/下游: [
              {top_code, top_name, sub_chains: [{sub_code, sub_name, companies: [(stk_code, name)]}]}
          ]}
      }
    * company_index[stk_code] = [
          {ic_code, ic_name, segment, top_code, top_name, sub_code, sub_name}, ...
      ]
- 結果落盤至 data/icchain.json (約 1~2 MB)，TTL 7 天。
- 首次任一查詢觸發背景全量抓取；之前所有公司查詢的 value_chain 欄位回 status=loading。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Optional

import logging

import httpx

logger = logging.getLogger("twstock_api.icchain")
from bs4 import BeautifulSoup

# ---- 產業鏈代碼表（依研究報告整理） ----
IC_CHAINS: list[tuple[str, str]] = [
    ("D000", "半導體"),
    ("6000", "自動化"),
    ("R300", "電子商務"),
    ("J000", "被動元件"),
    ("I000", "通信網路"),
    ("K000", "連接器"),
    ("F000", "電腦週邊"),
    ("G000", "平面顯示器"),
    ("H000", "觸控面板"),
    ("L000", "印刷電路板"),
    ("B000", "休閒娛樂"),
    ("M000", "食品"),
    ("N000", "石化及塑橡膠"),
    ("O000", "紡織"),
    ("P000", "電機機械"),
    ("Q000", "鋼鐵"),
    ("S000", "建材營造"),
    ("T000", "交通運輸及航運"),
    ("U000", "金融"),
    ("V000", "貿易百貨"),
    ("W000", "油電燃氣"),
    ("Y000", "文化創意"),
    ("X000", "其他"),
    ("R000", "軟體服務"),
    ("1000", "水泥"),
    ("2000", "造紙"),
    ("3000", "汽車"),
    # 生技醫療
    ("C100", "製藥"),
    ("C200", "醫療器材"),
    ("C300", "食品生技"),
    ("C400", "再生醫療"),
    # 綠色能源
    ("A300", "電動車"),
    ("A200", "LED照明"),
    ("A100", "太陽能"),
    ("AB10", "汽電共生"),
    ("AB20", "風力發電"),
    ("E000", "能源元件"),
    ("AD10", "智慧電網"),
    # 數位科技
    ("5100", "區塊鏈"),
    ("5200", "金融科技"),
    ("5300", "人工智慧"),
    ("5400", "雲端運算"),
    ("5500", "資通訊安全"),
    ("5600", "大數據"),
    ("5700", "體驗科技"),
    ("5800", "運動科技"),
    # 前瞻科技
    ("4100", "太空衛星科技"),
]

BASE_URL = "https://ic.tpex.org.tw/introduce.php?ic={ic}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CACHE_PATH = os.path.join(DATA_DIR, "icchain.json")
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60

# ---- 全域記憶體狀態 ----
_state: dict[str, Any] = {
    "loaded": False,
    "loading": False,
    "fetched_at": None,
    "chain_tree": {},      # ic_code -> structure
    "company_index": {},   # stk_code -> list of memberships
    "errors": [],          # 抓取/解析錯誤紀錄
}
_load_lock = asyncio.Lock()

# ---- Response JSON bytes cache ----
# key: ic_code (已 uppercase), value: 完整序列化好的 JSON bytes（約當於
# `ChainResponse.model_dump_json().encode()`）。命中時 endpoint 直接回
# `Response(content=cached, media_type="application/json")`，整個跳過 pydantic
# validation + FastAPI json encode，這兩步是單一鏈回應的主要 CPU 成本
# (D000 有 492 家公司 / 幾百個 nested node，每次 re-serialize 不便宜)。
#
# 作廢時機：只有兩個地方會換 chain_tree 內容──`_fetch_all` 寫新
# tree 成功與 `_load_from_disk` 載入磁碟 cache 成功──兩者都會呼叫
# `_invalidate_response_cache()`。避免回舊資料。
_response_cache: dict[str, bytes] = {}


def _invalidate_response_cache() -> None:
    _response_cache.clear()

# ---- 毒 cache 防線 ----
# 至少要有這麼多比例的 ic_code 抓到至少一家公司，index 才算健康；
# 否則視為毒 cache / 壞抓，重新抓取。
# 為什麼是 0.7：實測 46 條鏈全部都應該有公司，留 30% 容忍新增鏈頁
# 上線初期尚無公司的情況，但也守住「大部分鏈都空」的災難情形。
MIN_HEALTHY_CHAIN_RATIO = 0.7


def _chain_company_count(chain: dict) -> int:
    """Total companies aggregated across all segments/tops/subs of one chain."""
    n = 0
    for tops in (chain or {}).get("segments", {}).values():
        for top in tops:
            for sub in top.get("sub_chains", []):
                n += len(sub.get("companies", []))
    return n


def _index_health(chain_tree: dict) -> tuple[int, int, float]:
    """Return (healthy_count, total, ratio) where healthy means the chain has >=1 company."""
    total = len(chain_tree)
    if total == 0:
        return 0, 0, 0.0
    healthy = sum(1 for c in chain_tree.values() if _chain_company_count(c) > 0)
    return healthy, total, healthy / total


def _index_looks_poisoned(chain_tree: dict) -> bool:
    """True if too many chains ended up with zero companies -- likely a
    partial or throttled fetch that should not be cached, or a stale
    poisoned disk cache that should not be loaded.
    """
    healthy, total, ratio = _index_health(chain_tree)
    if total == 0:
        return True
    return ratio < MIN_HEALTHY_CHAIN_RATIO


def is_loaded() -> bool:
    return _state["loaded"]


def is_loading() -> bool:
    return _state["loading"]


def status() -> dict[str, Any]:
    healthy, total, ratio = _index_health(_state["chain_tree"])
    return {
        "loaded": _state["loaded"],
        "loading": _state["loading"],
        "fetched_at": _state["fetched_at"],
        "chain_count": total,
        "healthy_chain_count": healthy,
        "health_ratio": round(ratio, 3),
        "indexed_companies": len(_state["company_index"]),
        "errors": _state["errors"][-5:],
    }


def get_memberships(stock_id: str) -> list[dict]:
    return _state["company_index"].get(stock_id, [])


def get_chain(ic_code: str) -> Optional[dict]:
    return _state["chain_tree"].get(ic_code)


def get_chain_response_bytes(ic_code: str) -> Optional[bytes]:
    """回這條鏈 pre-serialized 好的 JSON bytes（命中）或 None（miss）。

    Miss 時呼叫者負責建一份、再用下面 `set_chain_response_bytes` 寫入。
    """
    return _response_cache.get(ic_code)


def set_chain_response_bytes(ic_code: str, payload: bytes) -> None:
    _response_cache[ic_code] = payload


def list_chains() -> list[dict]:
    return [{"ic_code": c, "ic_name": n} for c, n in IC_CHAINS]


# ---------- HTML 解析 ----------
def _parse_chain_html(ic_code: str, ic_name: str, html: str) -> dict:
    """解析單一產業鏈頁，回傳 chain_tree[ic_code] 結構。"""
    soup = BeautifulSoup(html, "lxml")

    # Step 1: 取得每個 ic_link_XXX 屬於哪個 segment
    # 考量官方頁面有三種不同結構：
    #   A. 有 div.chain + div.chain-title-panel（多數產業，例 D000 半導體）
    #   B. 有 div.chain 但用 <h4> 當標題（例 5700 體驗科技）
    #   C. 沒有 div.chain，ic_link 直接平鋪在 main_ic_panel 下（例 B000 休閒娛樂、
    #      T000 交通運輸、U000 金融、R000 軟體服務等），此時視為單一 segment
    #      並以 ic_name 作為分段名。
    main = soup.find("div", id="main_ic_panel")
    seg_of_node: dict[str, tuple[str, str]] = {}
    if main:
        chain_blocks = main.find_all("div", class_="chain")
        if chain_blocks:
            for chain in chain_blocks:
                title_el = chain.find("div", class_="chain-title-panel")
                if title_el is not None:
                    seg = title_el.get_text(strip=True)
                else:
                    # Fallback A→B：用 <h4> 作為 segment 標題。
                    h4 = chain.find("h4")
                    if h4 is not None:
                        seg = h4.get_text(strip=True)
                    else:
                        # 這個 chain block 連標題都沒有——拿 ic_name 當沒選擇時的預設
                        seg = ic_name
                if not seg:
                    seg = ic_name
                for node in chain.find_all("div", id=re.compile(r"^ic_link_")):
                    top_code = node["id"].replace("ic_link_", "")
                    top_name = node.get_text(strip=True)
                    seg_of_node[top_code] = (seg, top_name)
        else:
            # Fallback C：沒有 div.chain，所有 ic_link 視為同一 segment
            # 用 ic_name 作為分段名稱（與官網呈現一致：這類產業本來就是一個平鋪清單）
            for node in main.find_all("div", id=re.compile(r"^ic_link_")):
                top_code = node["id"].replace("ic_link_", "")
                top_name = node.get_text(strip=True)
                seg_of_node[top_code] = (ic_name, top_name)

    # Step 2: 對每個 top-level node，看是否有 sub-chain
    # segments 改成按 HTML 出現順序動態建立（不再預先塞上中下游空陣列），
    # 以支援像 5300 人工智慧（應用與服務／核心技術／運算資源）這類非標準分段命名。
    segments: dict[str, list[dict]] = {}
    for top_code, (seg, top_name) in seg_of_node.items():
        if seg not in segments:
            segments[seg] = []

        sub_pnl = soup.find("div", id=f"sc-ind-pnl_{top_code}")
        sub_chains: list[dict] = []
        if sub_pnl:
            for sub in sub_pnl.find_all("div", id=re.compile(r"^sc_link_")):
                scode = sub["id"].replace("sc_link_", "")
                sname = sub.get_text(strip=True)
                # 清掉開頭的 ► 與末尾「(N家)」
                sname = re.sub(r"^[►▶▼\s]+", "", sname)
                sname = re.sub(r"\s*\(\d+家\)\s*$", "", sname).strip()
                comp_tbl = soup.find("table", id=f"sc_company_{scode}")
                companies = _parse_company_table(comp_tbl) if comp_tbl else []
                sub_chains.append({
                    "sub_code": scode,
                    "sub_name": sname,
                    "companies": companies,
                })

        # 若無 sub-chain，從 companyList_{top_code} 直接抓
        if not sub_chains:
            cl = soup.find("div", id=f"companyList_{top_code}")
            companies = _parse_company_list(cl) if cl else []
            if companies:
                # 用 top_name 當作唯一一個 sub
                sub_chains.append({
                    "sub_code": top_code,
                    "sub_name": top_name,
                    "companies": companies,
                })

        segments[seg].append({
            "top_code": top_code,
            "top_name": top_name,
            "sub_chains": sub_chains,
        })

    return {
        "ic_code": ic_code,
        "ic_name": ic_name,
        "segments": segments,
    }


def _parse_company_table(table) -> list[dict]:
    """sc_company_{sub_code} table。只取本國公司，跳過『知名外國企業』分組。"""
    return _extract_companies_with_section_filter(table)


def _parse_company_list(container) -> list[dict]:
    """companyList_{top_code} 區塊。只取本國公司。"""
    return _extract_companies_with_section_filter(container)


def _extract_companies_with_section_filter(node) -> list[dict]:
    """掃描節點內的 <b>...</b> 分組標題，跳過外國企業段。
    每進入新分組 (<b>) 就更新當前分組；只有當前分組為「本國*」時才收集 <a>。
    """
    if node is None:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    current_section = "本國"  # 預設視為本國（若無分組標題）

    # iterate descendants in document order
    for tag in node.descendants:
        if getattr(tag, "name", None) == "b":
            txt = tag.get_text(strip=True)
            if "外國" in txt:
                current_section = "外國"
            elif "本國" in txt:
                current_section = "本國"
            else:
                current_section = txt
        elif getattr(tag, "name", None) == "a":
            href = tag.get("href", "")
            m = re.search(r"stk_code=([A-Za-z0-9_\-]+)", href)
            if not m:
                continue
            if "本國" not in current_section:
                continue
            stk = m.group(1)
            name = tag.get_text(strip=True)
            if stk in seen:
                continue
            seen.add(stk)
            out.append({"stk_code": stk, "name": name})
    return out


# ---------- 抓取 + 索引 ----------
async def _fetch_one(client: httpx.AsyncClient, ic_code: str, ic_name: str) -> dict:
    url = BASE_URL.format(ic=ic_code)
    for attempt in range(3):
        try:
            r = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=30.0)
            r.raise_for_status()
            html = r.text
            return _parse_chain_html(ic_code, ic_name, html)
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(0.8 * (attempt + 1))
                continue
            status = None
            body = ""
            if isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code
                try:
                    body = e.response.text[:200]
                except Exception:
                    body = ""
            logger.error(
                "[icchain] fetch failed ic=%s status=%s url=%s :: %s body=%r",
                ic_code, status, url, e, body,
            )
            _state["errors"].append({"ic_code": ic_code, "status": status, "error": str(e)})
            return {"ic_code": ic_code, "ic_name": ic_name, "segments": {}, "error": str(e)}


async def _fetch_all() -> None:
    """抓取所有產業鏈頁面，重建索引並落盤。"""
    _state["loading"] = True
    _state["errors"] = []
    try:
        # 並發 6 個避免被擋
        sem = asyncio.Semaphore(6)
        chain_tree: dict[str, dict] = {}

        async with httpx.AsyncClient(follow_redirects=True) as client:
            async def task(ic_code: str, ic_name: str):
                async with sem:
                    chain_tree[ic_code] = await _fetch_one(client, ic_code, ic_name)

            await asyncio.gather(*(task(c, n) for c, n in IC_CHAINS))

        # 反向索引
        company_index: dict[str, list[dict]] = {}
        for ic_code, chain in chain_tree.items():
            ic_name = chain.get("ic_name", "")
            for seg, tops in chain.get("segments", {}).items():
                for top in tops:
                    for sub in top.get("sub_chains", []):
                        for c in sub.get("companies", []):
                            mb = {
                                "ic_code": ic_code,
                                "ic_name": ic_name,
                                "segment": seg,
                                "top_code": top["top_code"],
                                "top_name": top["top_name"],
                                "sub_code": sub["sub_code"],
                                "sub_name": sub["sub_name"],
                            }
                            company_index.setdefault(c["stk_code"], []).append(mb)

        # 毒 cache 防線：整批抓完後檢查健康度。若太多 ic_code 抓到零家
        # 公司（通常代表被限流 / 網路壞 / parser 對某批頁面失效），
        # 不覆蓋 in-memory / 不寫 disk，讓下一次 ensure_loaded 有機會
        # 重抓，而不是把毒結果 cache 7 天。
        healthy, total, ratio = _index_health(chain_tree)
        if _index_looks_poisoned(chain_tree):
            logger.error(
                "[icchain] fetched index looks poisoned: healthy=%d/%d ratio=%.2f "
                "< threshold=%.2f -- NOT caching. Empty ic_codes: %s",
                healthy, total, ratio, MIN_HEALTHY_CHAIN_RATIO,
                sorted(
                    ic for ic, c in chain_tree.items()
                    if _chain_company_count(c) == 0
                )[:20],
            )
            _state["errors"].append({
                "phase": "fetch_all",
                "error": (
                    f"poisoned fetch: healthy={healthy}/{total} "
                    f"ratio={ratio:.2f} < {MIN_HEALTHY_CHAIN_RATIO}"
                ),
            })
            # 保留 loaded 狀態不變。若之前是 True（舊 healthy cache 在），
            # 使用者仍能查到；若之前是 False，維持 False，下次 ensure_loaded
            # 會再試。
            return

        _state["chain_tree"] = chain_tree
        _state["company_index"] = company_index
        _state["fetched_at"] = int(time.time())
        _state["loaded"] = True
        # 內容已變，掉舊序列化 cache。
        _invalidate_response_cache()
        logger.info(
            "[icchain] fetched index OK: healthy=%d/%d ratio=%.2f companies=%d",
            healthy, total, ratio, len(company_index),
        )
        _save_to_disk()
    finally:
        _state["loading"] = False


def _save_to_disk() -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "fetched_at": _state["fetched_at"],
                "chain_tree": _state["chain_tree"],
                "company_index": _state["company_index"],
            }, f, ensure_ascii=False)
    except Exception as e:
        _state["errors"].append({"phase": "save", "error": str(e)})


def _load_from_disk() -> bool:
    try:
        if not os.path.exists(CACHE_PATH):
            return False
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        fetched_at = data.get("fetched_at") or 0
        if time.time() - fetched_at > CACHE_TTL_SECONDS:
            return False
        chain_tree = data.get("chain_tree") or {}
        # 毒 cache 防線：拒絕載入公司數過低的舊 cache（可能是上次
        # fetch 被限流 / 部分失敗時寫進去的）。留在磁碟上但不進記憶體，
        # 讓 ensure_loaded 走 _fetch_all 重抓；重抓成功後才會覆蓋 disk。
        if _index_looks_poisoned(chain_tree):
            healthy, total, ratio = _index_health(chain_tree)
            logger.warning(
                "[icchain] on-disk cache looks poisoned: healthy=%d/%d ratio=%.2f "
                "< threshold=%.2f -- ignoring, will re-fetch",
                healthy, total, ratio, MIN_HEALTHY_CHAIN_RATIO,
            )
            _state["errors"].append({
                "phase": "load",
                "error": (
                    f"poisoned disk cache: healthy={healthy}/{total} "
                    f"ratio={ratio:.2f} < {MIN_HEALTHY_CHAIN_RATIO}"
                ),
            })
            return False
        _state["chain_tree"] = chain_tree
        _state["company_index"] = data.get("company_index") or {}
        _state["fetched_at"] = fetched_at
        _state["loaded"] = bool(_state["chain_tree"])
        # 從磁碟新載 tree──舊序列化 cache 已不一致，作廢。
        _invalidate_response_cache()
        return _state["loaded"]
    except Exception as e:
        _state["errors"].append({"phase": "load", "error": str(e)})
        return False


async def ensure_loaded(background: bool = True, force: bool = False) -> None:
    """若尚未載入，嘗試從磁碟讀；否則背景抓取。

    - ``background=True``：立即返回，背景任務跑抓取；首次查詢的公司
      不會等到結果。
    - ``background=False``：阻塞直到完成（測試用）。
    - ``force=True``：強制忽略記憶體與磁碟 cache，重新抓一次。用於
      毒 cache 恢復或手動 refresh。
    """
    if force:
        # 清空記憶體 loaded 狀態，讓下面的邏輯走重抓路徑。
        _state["loaded"] = False
    if _state["loaded"]:
        return
    async with _load_lock:
        if _state["loaded"] and not force:
            return
        if not force and _load_from_disk():
            return
        if _state["loading"]:
            return
        if background:
            asyncio.create_task(_fetch_all())
        else:
            await _fetch_all()
