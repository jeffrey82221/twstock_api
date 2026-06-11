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

import httpx
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


def is_loaded() -> bool:
    return _state["loaded"]


def is_loading() -> bool:
    return _state["loading"]


def status() -> dict[str, Any]:
    return {
        "loaded": _state["loaded"],
        "loading": _state["loading"],
        "fetched_at": _state["fetched_at"],
        "chain_count": len(_state["chain_tree"]),
        "indexed_companies": len(_state["company_index"]),
        "errors": _state["errors"][-5:],
    }


def get_memberships(stock_id: str) -> list[dict]:
    return _state["company_index"].get(stock_id, [])


def get_chain(ic_code: str) -> Optional[dict]:
    return _state["chain_tree"].get(ic_code)


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
            _state["errors"].append({"ic_code": ic_code, "error": str(e)})
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

        _state["chain_tree"] = chain_tree
        _state["company_index"] = company_index
        _state["fetched_at"] = int(time.time())
        _state["loaded"] = True
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
        _state["chain_tree"] = data.get("chain_tree") or {}
        _state["company_index"] = data.get("company_index") or {}
        _state["fetched_at"] = fetched_at
        _state["loaded"] = bool(_state["chain_tree"])
        return _state["loaded"]
    except Exception as e:
        _state["errors"].append({"phase": "load", "error": str(e)})
        return False


async def ensure_loaded(background: bool = True) -> None:
    """若尚未載入，嘗試從磁碟讀；否則背景抓取。

    background=True：立即返回，背景任務跑抓取；首次查詢的公司不會等到結果。
    background=False：阻塞直到完成（測試用）。
    """
    if _state["loaded"]:
        return
    async with _load_lock:
        if _state["loaded"]:
            return
        if _load_from_disk():
            return
        if _state["loading"]:
            return
        if background:
            asyncio.create_task(_fetch_all())
        else:
            await _fetch_all()
