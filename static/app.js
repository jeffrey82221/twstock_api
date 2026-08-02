// API base：本機開發使相對路徑；deploy_website 會將下方 placeholder 替換成代理路徑。
const API_BASE = (function(){
  const placeholder = '__PORT_5000__';
  // 在使用者部署版會被替換為代理 URL；本機使用時仍為上述字串，退化為空。
  return placeholder.startsWith('__') ? '' : placeholder;
})();
const api = (path) => API_BASE + path;

const $ = (sel) => document.querySelector(sel);
const form = $("#searchForm");
const kw = $("#kw");
const asof = $("#asof");
const resultEl = $("#result");
const emptyEl = $("#empty");
const suggestEl = $("#suggest");

// 預設今天
asof.valueAsDate = new Date();

// ---------- 共用 ----------
const fmtInt = (n) =>
  n == null || isNaN(n) ? "—" : Number(n).toLocaleString("en-US");
const fmtFloat = (n, digits = 2) =>
  n == null || isNaN(n) ? "—" : Number(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

function fmtMoneyTW(n) {
  if (n == null || isNaN(n)) return "—";
  const v = Number(n);
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + " 億";
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + " 萬";
  return v.toLocaleString();
}

function fmtPct(p) {
  if (p == null || isNaN(p)) return "—";
  const cls = p >= 0 ? "delta-pos" : "delta-neg";
  const sign = p >= 0 ? "+" : "";
  return `<span class="${cls}">${sign}${Number(p).toFixed(2)}%</span>`;
}

function gmLabel(s) {
  if (!s) return "總經理";
  const m = s.match(/^(總裁|執行長|CEO)[:：]/i);
  return m ? `總經理 / ${m[1]}` : "總經理";
}
function gmValue(s) {
  if (!s) return "—";
  return s.replace(/^(總裁|執行長|CEO)[:：]\s*/i, "");
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------- 搜尋建議 ----------
let searchTimer = null;
kw.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = kw.value.trim();
  if (q.length < 1) {
    suggestEl.innerHTML = "";
    return;
  }
  searchTimer = setTimeout(async () => {
    try {
      const r = await fetch(api(`/api/search?q=${encodeURIComponent(q)}&limit=8`));
      const data = await r.json();
      suggestEl.innerHTML = (data.results || [])
        .map(
          (it) =>
            `<span class="pill" data-code="${it.stock_id}">${it.stock_id} · ${it.short_name || it.company_name} <span class="muted">${it.market}</span></span>`
        )
        .join("");
      suggestEl.querySelectorAll(".pill").forEach((el) => {
        el.addEventListener("click", () => {
          kw.value = el.dataset.code;
          suggestEl.innerHTML = "";
          form.requestSubmit();
        });
      });
    } catch (e) {
      // ignore
    }
  }, 200);
});

document.querySelectorAll('.empty a[data-q]').forEach((a) =>
  a.addEventListener('click', (e) => {
    e.preventDefault();
    kw.value = a.dataset.q;
    form.requestSubmit();
  })
);

// ---------- 主流程 ----------
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = kw.value.trim();
  if (!q) return;
  let stockId = q;
  if (!/^\d{4,6}[A-Z]?$/.test(q)) {
    const r = await fetch(api(`/api/search?q=${encodeURIComponent(q)}&limit=1`));
    const data = await r.json();
    if (!data.results || data.results.length === 0) {
      renderFatalError(`查無「${q}」相關公司`);
      return;
    }
    stockId = data.results[0].stock_id;
  }
  await loadCompanyParallel(stockId, asof.value);
});

function renderFatalError(msg) {
  emptyEl.classList.add("hidden");
  resultEl.classList.remove("hidden");
  resultEl.innerHTML = `<div class="card"><div class="error">${escapeHtml(msg)}</div></div>`;
}

/**
 * 平行呼叫 6 個分項 endpoint，每張卡片各自獨立顯示載入/錯誤/結果。
 * basic 是骨架的關鍵：拿到後立刻填頭部；其他 5 個卡片獨立更新。
 */
async function loadCompanyParallel(stockId, asOf) {
  emptyEl.classList.add("hidden");
  resultEl.classList.remove("hidden");

  // 1. 立刻渲染所有區塊的 skeleton
  resultEl.innerHTML = renderSkeleton(stockId, asOf);
  resetSources();
  resultEl.scrollIntoView({ behavior: "smooth", block: "start" });

  // 2. 平行呼叫六個 endpoint
  const asOfQS = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
  const stockEnc = encodeURIComponent(stockId);

  const endpoints = [
    { key: "basic",         url: `/api/company/${stockEnc}/basic`,           asOf: false },
    { key: "businessItems", url: `/api/company/${stockEnc}/business-items`,  asOf: false },
    { key: "valueChain",    url: `/api/company/${stockEnc}/value-chain`,     asOf: false },
    { key: "financials",    url: `/api/company/${stockEnc}/financials`,      asOf: true  },
    { key: "revenue",       url: `/api/company/${stockEnc}/revenue`,         asOf: true  },
    { key: "productRevenue",url: `/api/company/${stockEnc}/product-revenue`, asOf: true  },
    { key: "dividend",      url: `/api/company/${stockEnc}/dividend`,        asOf: true  },
  ];

  // 啟動 6 個非阻塞請求；每個獨立 then() 更新對應卡片
  for (const ep of endpoints) {
    const url = api(ep.url + (ep.asOf ? asOfQS : ""));
    fetch(url)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (!r.ok) {
          updateCardError(ep.key, body.detail || `HTTP ${r.status}`);
          return;
        }
        updateCard(ep.key, body, stockId);
      })
      .catch((e) => {
        updateCardError(ep.key, e.message || "網路錯誤");
      });
  }
}

// ---------- Skeleton ----------
function renderSkeleton(stockId, asOf) {
  return `
    <div class="card" id="card-basic" data-loading="1">
      <div class="head skeleton-head">
        <span class="code">${escapeHtml(stockId)}</span>
        <span class="name skel skel-text" style="width:200px"></span>
        <span class="market skel skel-text" style="width:48px"></span>
        <span class="asof">基準日 ${escapeHtml(asOf || "—")}</span>
      </div>
      <div class="kv">${skelKvRows(12)}</div>
    </div>

    <div class="card" id="card-value-chain" data-loading="1">
      <h3 class="section-title">產業鏈上下游定位</h3>
      <div class="muted skel-line">載入中…</div>
    </div>

    <div class="card" id="card-business-items" data-loading="1">
      <h3 class="section-title">主要營業項目（公司登記所營事業）</h3>
      <div class="muted skel-line">載入中…</div>
    </div>

    <div class="card" id="card-financials" data-loading="1">
      <h3 class="section-title">獲利（TTM 滾動四季）</h3>
      <div class="kv">${skelKvRows(5)}</div>
    </div>

    <div class="card" id="card-revenue" data-loading="1">
      <h3 class="section-title">營收</h3>
      <div class="kv">${skelKvRows(3)}</div>
    </div>

    <div class="card" id="card-product-revenue" data-loading="1">
      <h3 class="section-title">主要產品比重（公開資訊觀測站）</h3>
      <div class="muted skel-line">載入中…</div>
    </div>

    <div class="card" id="card-dividend" data-loading="1">
      <h3 class="section-title">股利股息</h3>
      <div class="kv">${skelKvRows(4)}</div>
    </div>

    <div class="card" id="card-sources">
      <h3 class="section-title">資料來源</h3>
      <div class="muted" id="sources-list" style="font-size:13px">—</div>
      <div class="muted" style="font-size:12px;margin-top:6px">註：TTM = trailing twelve months，回溯基準日往前的滾動四季 / 十二個月加總。基準日設為過去日期時，會以該日「已公告」的最後一份資料為準。前端會平行呼叫 6 個分項 endpoint，各區塊獨立載入與顯示錯誤。</div>
    </div>
  `;
}

// 收集所有卡片回傳的 source 字串，去重後渲染到「資料來源」卡片
const _sourcesSet = new Set();
function addSource(s) {
  if (!s) return;
  if (Array.isArray(s)) { s.forEach(addSource); return; }
  _sourcesSet.add(String(s));
  const el = document.getElementById('sources-list');
  if (el) el.textContent = Array.from(_sourcesSet).join(' · ');
}
function resetSources() {
  _sourcesSet.clear();
  const el = document.getElementById('sources-list');
  if (el) el.textContent = '—';
}

function skelKvRows(n) {
  let s = "";
  for (let i = 0; i < n; i++) {
    s += `<div><dt class="skel skel-text" style="width:60px"></dt><dd class="skel skel-text" style="width:120px;height:18px"></dd></div>`;
  }
  return s;
}

// ---------- 卡片更新分派 ----------
function updateCard(key, data, stockId) {
  // 任何 endpoint 回傳的 source 都累加到「資料來源」卡片
  if (data && data.source) addSource(data.source);
  switch (key) {
    case "basic":         return updateBasic(data, stockId);
    case "businessItems": return updateBusinessItems(data);
    case "valueChain":    return updateValueChain(data, stockId);
    case "financials":    return updateFinancials(data);
    case "revenue":       return updateRevenue(data);
    case "productRevenue":return updateProductRevenue(data);
    case "dividend":      return updateDividend(data);
  }
}

function updateCardError(key, msg) {
  const idMap = {
    basic: "card-basic",
    businessItems: "card-business-items",
    valueChain: "card-value-chain",
    financials: "card-financials",
    revenue: "card-revenue",
    productRevenue: "card-product-revenue",
    dividend: "card-dividend",
  };
  const titleMap = {
    basic: "公司基本資料",
    businessItems: "主要營業項目（公司登記所營事業）",
    valueChain: "產業鏈上下游定位",
    financials: "獲利（TTM 滾動四季）",
    revenue: "營收",
    productRevenue: "主要產品比重（公開資訊觀測站）",
    dividend: "股利股息",
  };
  const el = document.getElementById(idMap[key]);
  if (!el) return;
  el.dataset.loading = "0";
  el.innerHTML = `
    <h3 class="section-title">${titleMap[key]}</h3>
    <div class="error">載入失敗：${escapeHtml(msg)}</div>
  `;
}

// ---------- basic ----------
function updateBasic(d, stockId) {
  const el = document.getElementById("card-basic");
  if (!el) return;
  el.dataset.loading = "0";

  if (!d.found) {
    el.innerHTML = `
      <div class="head">
        <span class="code">${escapeHtml(stockId)}</span>
        <span class="name">查無此公司</span>
      </div>
      <div class="error">${escapeHtml(d.error || "查無此公司基本資料（可能為興櫃或已下市）")}</div>
    `;
    return;
  }

  const asOf = d.as_of || asof.value || "";
  el.innerHTML = `
    <div class="head">
      <span class="code">${escapeHtml(stockId)}</span>
      <span class="name">${escapeHtml(d.short_name || d.company_name || "")}</span>
      <span class="market">${escapeHtml(d.market || "")}</span>
      <span class="asof">基準日 ${escapeHtml(asOf)}</span>
    </div>
    <div class="kv">
      <div><dt>公司全名</dt><dd>${escapeHtml(d.company_name) || "—"}</dd></div>
      <div><dt>英文名稱</dt><dd>${escapeHtml(d.english_name) || "—"}</dd></div>
      <div><dt>股票代號</dt><dd class="num">${escapeHtml(stockId)}</dd></div>
      <div><dt>統一編號</dt><dd class="num">${escapeHtml(d.tax_id) || "—"}</dd></div>
      <div><dt>實收資本額</dt><dd class="num big">${fmtMoneyTW(d.paid_in_capital)} <span class="unit">NTD</span></dd></div>
      <div><dt>產業別</dt><dd>${escapeHtml(d.industry_name) || "—"}</dd></div>
      <div><dt>${gmLabel(d.general_manager)}</dt><dd>${escapeHtml(gmValue(d.general_manager))}</dd></div>
      <div><dt>董事長</dt><dd>${escapeHtml(d.chairman) || "—"}</dd></div>
      <div><dt>成立日期</dt><dd class="num">${escapeHtml(d.incorporation_date) || "—"}</dd></div>
      <div><dt>上市/上櫃日期</dt><dd class="num">${escapeHtml(d.listing_date) || "—"}</dd></div>
      <div><dt>網站</dt><dd>${d.website ? `<a href="${escapeHtml(d.website)}" target="_blank" rel="noopener">${escapeHtml(d.website)}</a>` : "—"}</dd></div>
      <div><dt>地址</dt><dd>${escapeHtml(d.address) || "—"}</dd></div>
    </div>
  `;
}

// ---------- business-items ----------
function updateBusinessItems(d) {
  const el = document.getElementById("card-business-items");
  if (!el) return;
  el.dataset.loading = "0";

  if (!d.found) {
    el.innerHTML = `
      <h3 class="section-title">主要營業項目（公司登記所營事業）</h3>
      <div class="muted">${escapeHtml(d.error || "查無此公司的所營事業資料。")}</div>
    `;
    return;
  }

  const narrative = d.narrative || [];
  const categories = d.categories || [];

  el.innerHTML = `
    <h3 class="section-title">主要營業項目（公司登記所營事業）</h3>
    ${
      narrative.length
        ? `<ol class="biz-list">${narrative.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ol>`
        : `<div class="muted" style="margin-bottom:8px">本公司未填寫敘述性所營事業。</div>`
    }
    ${
      categories.length
        ? `<div class="biz-cats"><div class="muted" style="font-size:12px;margin-bottom:6px">行業分類（中華民國行業標準分類代碼）</div>${categories
            .map((c) => `<span class="cat-pill" title="${escapeHtml(c.code)}">${escapeHtml(c.desc)}</span>`)
            .join("")}</div>`
        : ""
    }
  `;
}

// ---------- value-chain ----------
function updateValueChain(d, stockId) {
  const el = document.getElementById("card-value-chain");
  if (!el) return;
  el.dataset.loading = "0";

  // 後端在背景索引中
  if (d.status === "loading") {
    el.innerHTML = `
      <h3 class="section-title">產業鏈上下游定位</h3>
      <div class="muted">首次查詢背景載入中（預計 5~15 秒），請稍後重新查詢。</div>
    `;
    return;
  }
  // 索引不可用
  if (d.status && d.status !== "ready") {
    el.innerHTML = `
      <h3 class="section-title">產業鏈上下游定位</h3>
      <div class="muted">產業鏈資料目前不可用。</div>
    `;
    return;
  }
  // 查無公司
  if (!d.found) {
    el.innerHTML = `
      <h3 class="section-title">產業鏈上下游定位</h3>
      <div class="muted">${escapeHtml(d.error || "查無此公司資料。")}</div>
    `;
    return;
  }

  const memberships = d.memberships || [];
  if (memberships.length === 0) {
    el.innerHTML = `
      <h3 class="section-title">產業鏈上下游定位</h3>
      <div class="muted">本公司未被櫃買中心『產業價值鏈資訊平台』列入任一產業鏈。</div>
    `;
    return;
  }

  // 依 ic_code 分組
  const groups = {};
  for (const m of memberships) {
    if (!groups[m.ic_code]) groups[m.ic_code] = { ic_name: m.ic_name, items: [] };
    groups[m.ic_code].items.push(m);
  }
  const neighbors = d.neighbors_by_chain || {};

  let html = `<h3 class="section-title">產業鏈上下游定位</h3>`;
  for (const ic_code of Object.keys(groups)) {
    const g = groups[ic_code];
    const nb = neighbors[ic_code] || {};
    const selfSegs = new Set(g.items.map((m) => m.segment));
    // 彈性讀取：優先使用新的 streams。舊版本未提供時才從上/中/下游組裝
    let streams = Array.isArray(nb.streams) ? nb.streams : null;
    if (!streams) {
      streams = [];
      if ((nb.upstream || []).length)   streams.push({ segment: '上游',   companies: nb.upstream });
      if ((nb.midstream || []).length)  streams.push({ segment: '中游',   companies: nb.midstream });
      if ((nb.downstream || []).length) streams.push({ segment: '下游',   companies: nb.downstream });
    }
    html += `
      <div class="chain-block" data-ic="${escapeHtml(ic_code)}">
        <div class="chain-head">
          <span class="chain-name">${escapeHtml(g.ic_name)}</span>
          <span class="chain-tag">${escapeHtml(ic_code)}</span>
          <button class="btn-expand" data-ic="${escapeHtml(ic_code)}">展開全圖</button>
        </div>
        <div class="chain-self">
          ${g.items.map((m) =>
            `<span class="seg-pill seg-${segCls(m.segment)}">
              <b>${escapeHtml(m.segment)}</b> · ${escapeHtml(m.top_name)}${m.sub_name && m.sub_name !== m.top_name ? ' → ' + escapeHtml(m.sub_name) : ''}
            </span>`
          ).join('')}
        </div>
        <div class="chain-stream">
          ${streams.map((s) => renderStreamRow(s.segment, s.companies, selfSegs.has(s.segment) ? stockId : null, 6)).join('')}
        </div>
        <div class="chain-full hidden" id="chain-full-${escapeHtml(ic_code)}"></div>
      </div>
    `;
  }
  el.innerHTML = html;

  // 綁定展開按鈕
  el.querySelectorAll('.btn-expand').forEach((btn) => {
    btn.addEventListener('click', () => {
      expandChainFull(btn.dataset.ic, stockId);
      btn.textContent = btn.textContent === '展開全圖' ? '收起全圖' : '展開全圖';
    });
  });
}

// 分段名轉換為 CSS 類名：預設上/中/下游保留原色套；其他名稱以 hash 輪流使用 alt1/alt2/alt3
function segCls(label) {
  if (label === '上游') return 'up';
  if (label === '中游') return 'mid';
  if (label === '下游') return 'down';
  let h = 0;
  for (let i = 0; i < (label || '').length; i++) h = (h * 31 + label.charCodeAt(i)) >>> 0;
  return 'alt' + (h % 3 + 1);
}

function renderStreamRow(label, list, selfId, maxShow) {
  if (!list || list.length === 0) return '';
  const shown = list.slice(0, maxShow);
  const moreCount = list.length - shown.length;
  const cls = segCls(label);
  return `
    <div class="stream-row stream-${cls}">
      <span class="stream-label">${label}</span>
      <span class="stream-companies">
        ${shown.map((c) =>
          `<a class="co-link${c.stk_code === selfId ? ' is-self' : ''}" data-stkcode="${escapeHtml(c.stk_code)}" title="${escapeHtml(c.top_name + ' / ' + c.sub_name)}">${escapeHtml(c.stk_code)} ${escapeHtml(c.name)}</a>`
        ).join('')}
        ${moreCount > 0 ? `<span class="more">+${moreCount}</span>` : ''}
      </span>
    </div>
  `;
}

async function expandChainFull(ic_code, selfId) {
  const target = document.getElementById(`chain-full-${ic_code}`);
  if (!target) return;
  if (!target.classList.contains('hidden')) {
    target.classList.add('hidden');
    target.innerHTML = '';
    return;
  }
  target.classList.remove('hidden');
  target.innerHTML = `<div class="muted">載入中…</div>`;
  try {
    const r = await fetch(api(`/api/chain/${encodeURIComponent(ic_code)}`));
    const data = await r.json();
    target.innerHTML = renderFullChain(data, selfId);
  } catch (e) {
    target.innerHTML = `<div class="error">載入失敗</div>`;
  }
}

function renderFullChain(chain, selfId) {
  if (!chain || !chain.segments) return '<div class="muted">無資料</div>';
  // 彈性順序：優先上中下游，其餘分段按官方出現順序接在後面
  const preferred = ['上游', '中游', '下游'];
  const allSegs = Object.keys(chain.segments);
  const segOrder = [
    ...preferred.filter((s) => allSegs.includes(s)),
    ...allSegs.filter((s) => !preferred.includes(s)),
  ];
  let html = '<div class="full-chain">';
  for (const seg of segOrder) {
    const tops = chain.segments[seg] || [];
    if (tops.length === 0) continue;
    const cls = segCls(seg);
    html += `<div class="full-seg full-seg-${cls}"><div class="full-seg-title">${escapeHtml(seg)}</div>`;
    for (const top of tops) {
      html += `<div class="full-top"><div class="full-top-name">${escapeHtml(top.top_name)}</div>`;
      for (const sub of top.sub_chains || []) {
        const showName = sub.sub_name && sub.sub_name !== top.top_name;
        html += `<div class="full-sub">${showName ? `<div class="full-sub-name">${escapeHtml(sub.sub_name)} <span class="muted">· ${(sub.companies||[]).length} 家</span></div>` : ''}
          <div class="full-companies">
            ${(sub.companies||[]).map((c) =>
              `<a class="co-link${c.stk_code === selfId ? ' is-self' : ''}" data-stkcode="${escapeHtml(c.stk_code)}">${escapeHtml(c.stk_code)} ${escapeHtml(c.name)}</a>`
            ).join('')}
          </div></div>`;
      }
      html += `</div>`;
    }
    html += `</div>`;
  }
  html += '</div>';
  return html;
}

// ---------- financials ----------
function updateFinancials(d) {
  const el = document.getElementById("card-financials");
  if (!el) return;
  el.dataset.loading = "0";

  if (!d.found) {
    el.innerHTML = `
      <h3 class="section-title">獲利（TTM 滾動四季）</h3>
      <div class="muted">${escapeHtml(d.error || "查無此公司的財報資料。")}</div>
    `;
    return;
  }

  const eps = d.eps || {};
  const ni = d.net_income || {};
  const epsTtmTag = (eps.ttm_quarters || []).map((q) => `<span class="q">${escapeHtml(q)}</span>`).join("");
  const niTtmTag = (ni.ttm_quarters || []).map((q) => `<span class="q">${escapeHtml(q)}</span>`).join("");
  const asOf = d.as_of || "";

  el.innerHTML = `
    <h3 class="section-title">獲利 · 截至 ${escapeHtml(asOf)}（TTM 滾動四季）</h3>
    <div class="kv">
      <div>
        <dt>EPS（TTM）</dt>
        <dd class="num big">${eps.ttm != null ? fmtFloat(eps.ttm, 2) : "—"} <span class="unit">元</span></dd>
        <div class="tag-row">${epsTtmTag || '<span class="muted">資料不足 4 季</span>'}</div>
      </div>
      <div>
        <dt>最新單季 EPS</dt>
        <dd class="num">${eps.latest_quarter_value != null ? fmtFloat(eps.latest_quarter_value, 2) + " 元" : "—"}</dd>
        <div class="tag-row">${eps.latest_quarter_date ? `<span class="q">${escapeHtml(eps.latest_quarter_date)}</span>` : ""}</div>
      </div>
      <div>
        <dt>淨利（TTM）</dt>
        <dd class="num big">${fmtMoneyTW(ni.ttm)} <span class="unit">NTD</span></dd>
        <div class="tag-row">${niTtmTag || '<span class="muted">資料不足 4 季</span>'}</div>
      </div>
      <div>
        <dt>最新單季淨利</dt>
        <dd class="num">${fmtMoneyTW(ni.latest_quarter_value)}</dd>
        <div class="tag-row">${ni.latest_quarter_date ? `<span class="q">${escapeHtml(ni.latest_quarter_date)}</span>` : ""}</div>
      </div>
      <div>
        <dt>營業利潤率（TTM）</dt>
        <dd class="num big">${d.operating_margin_pct != null ? fmtFloat(d.operating_margin_pct, 2) + "%" : "—"}</dd>
      </div>
    </div>
  `;
}

// ---------- revenue ----------
function updateRevenue(d) {
  const el = document.getElementById("card-revenue");
  if (!el) return;
  el.dataset.loading = "0";

  if (!d.found) {
    el.innerHTML = `
      <h3 class="section-title">營收</h3>
      <div class="muted">${escapeHtml(d.error || "查無此公司的月營收資料。")}</div>
    `;
    return;
  }

  el.innerHTML = `
    <h3 class="section-title">營收</h3>
    <div class="kv">
      <div>
        <dt>最新月營收 (${escapeHtml(d.latest_month_label) || "—"})</dt>
        <dd class="num big">${fmtMoneyTW(d.latest_month_value)} <span class="unit">NTD</span></dd>
        <div class="tag-row"><span class="q">YoY ${fmtPct(d.latest_month_yoy_pct)}</span></div>
      </div>
      <div>
        <dt>近 12 月營收（TTM）</dt>
        <dd class="num big">${fmtMoneyTW(d.ttm_value)} <span class="unit">NTD</span></dd>
        <div class="tag-row"><span class="q">YoY ${fmtPct(d.ttm_yoy_pct)}</span></div>
      </div>
      <div>
        <dt>季財報合計營收（參照）</dt>
        <dd class="num">${fmtMoneyTW(d.ttm_from_financial_statements)}</dd>
      </div>
    </div>
  `;
}

// ---------- product-revenue ----------
function updateProductRevenue(d) {
  const el = document.getElementById("card-product-revenue");
  if (!el) return;
  el.dataset.loading = "0";

  // 期間標籤：民國年 + 西元年轉換
  const periodLabel = (() => {
    if (!d.year || !d.month) return "";
    const ad = Number(d.year) + 1911;
    return ` · 民國 ${d.year}/${d.month}（西元 ${ad}/${d.month}）`;
  })();

  // 連線/解析錯誤
  if (d.error) {
    el.innerHTML = `
      <h3 class="section-title">主要產品比重（公開資訊觀測站）</h3>
      <div class="muted">${escapeHtml(d.error)}</div>
    `;
    return;
  }

  // 採用 IFRSs 後自願申報，或無資料
  if (!d.found) {
    el.innerHTML = `
      <h3 class="section-title">主要產品比重（公開資訊觀測站）</h3>
      <div class="muted">${escapeHtml(d.notes || "該公司未於MOPS申報「各項產品業務營收統計表」。")}</div>
    `;
    return;
  }

  const items = d.items || [];
  // 按金額降序排序（MOPS 原順為序號）
  const sorted = items.slice().sort((a, b) => (b.amount || 0) - (a.amount || 0));

  // 請注意：不含銷貨退回，兩者加起來才是 total_revenue + sales_return。
  // 這裡 donut 以 (各項加總 + sales_return) 為 100%，能讓退回並不會被忽略。
  const itemsSum = sorted.reduce((s, it) => s + (it.amount || 0), 0);
  const salesReturn = d.sales_return || 0;
  const denom = itemsSum + salesReturn || 1;

  // 以計算後的百分比（統一分母）佈局圖表
  const slices = sorted.map((it, i) => ({
    rank: it.rank || "",
    name: it.name,
    amount: it.amount || 0,
    pct: ((it.amount || 0) / denom) * 100,
    color: PRODUCT_COLORS[i % PRODUCT_COLORS.length],
  }));
  if (salesReturn > 0) {
    slices.push({
      rank: "−",
      name: "銷貨退回及折讓",
      amount: salesReturn,
      pct: (salesReturn / denom) * 100,
      color: "var(--text-muted)",
      isReturn: true,
    });
  }

  const donutSvg = renderDonut(slices, fmtMoneyTW(d.total_revenue));
  const legend = slices.map((s, i) => `
    <div class="pr-legend-row${s.isReturn ? " is-return" : ""}" data-idx="${i}">
      <span class="pr-swatch" style="background:${s.color}"></span>
      <span class="pr-leg-name" title="${escapeHtml(s.name)}">
        <span class="pr-rank">${escapeHtml(s.rank)}</span> ${escapeHtml(s.name)}
      </span>
      <span class="pr-leg-amount num">${fmtMoneyTW(s.amount)}</span>
      <span class="pr-leg-pct num">${fmtFloat(s.pct, 2)}%</span>
    </div>
  `).join("");

  el.innerHTML = `
    <h3 class="section-title">主要產品比重${periodLabel}</h3>
    <div class="pr-flex">
      <div class="pr-donut-wrap">${donutSvg}</div>
      <div class="pr-legend">
        <div class="pr-legend-row pr-legend-head">
          <span></span>
          <span>產品 / 業務項目</span>
          <span class="pr-leg-amount">金額</span>
          <span class="pr-leg-pct">佔比</span>
        </div>
        ${legend || '<div class="muted">未解析到產品項目。</div>'}
      </div>
    </div>
    <div class="pr-summary">
      <div><span class="muted">合計業務營收淨額</span><b class="num">${fmtMoneyTW(d.total_revenue)}</b></div>
      ${d.sales_return != null ? `<div><span class="muted">減：銷貨退回及折讓</span><b class="num">${fmtMoneyTW(d.sales_return)}</b></div>` : ""}
      ${d.company_name ? `<div><span class="muted">申報公司名稱</span><b>${escapeHtml(d.company_name)}</b></div>` : ""}
    </div>
    <div class="muted" style="font-size:12px;margin-top:8px">資料為 MOPS 上該公司最近一次「各項產品業務營收統計表」申報，原表金額單位為仟元，此處已轉換為元。Donut 以「各項產品 + 銷貨退回及折讓」為 100%，金額加總等於實際業務收入。</div>
  `;

  // hover 高亮聯動：legend «→» slice
  const svg = el.querySelector("svg.pr-donut");
  const rows = el.querySelectorAll(".pr-legend-row[data-idx]");
  rows.forEach((row) => {
    const idx = row.dataset.idx;
    row.addEventListener("mouseenter", () => highlightSlice(svg, idx, true));
    row.addEventListener("mouseleave", () => highlightSlice(svg, idx, false));
  });
  if (svg) {
    svg.querySelectorAll("path[data-idx]").forEach((p) => {
      const idx = p.dataset.idx;
      p.addEventListener("mouseenter", () => {
        highlightSlice(svg, idx, true);
        const row = el.querySelector(`.pr-legend-row[data-idx="${idx}"]`);
        if (row) row.classList.add("is-hover");
      });
      p.addEventListener("mouseleave", () => {
        highlightSlice(svg, idx, false);
        const row = el.querySelector(`.pr-legend-row[data-idx="${idx}"]`);
        if (row) row.classList.remove("is-hover");
      });
    });
  }
}

// 12 色調盤（主色 + 調和色）
const PRODUCT_COLORS = [
  "#1853d6", "#0d8348", "#d97706", "#9333ea",
  "#dc2626", "#0891b2", "#65a30d", "#ea580c",
  "#7c3aed", "#0284c7", "#be185d", "#4d7c0f",
];

// 純 SVG donut chart。slices: [{name, amount, pct, color, isReturn}]
function renderDonut(slices, centerLabel) {
  const size = 220;
  const cx = size / 2, cy = size / 2;
  const rOuter = 95;
  const rInner = 60;
  // start 從頂端（-90°）
  let angle = -Math.PI / 2;
  const totalPct = slices.reduce((s, x) => s + x.pct, 0) || 1;
  const paths = slices.map((s, i) => {
    const sweep = (s.pct / totalPct) * Math.PI * 2;
    const a0 = angle;
    const a1 = angle + sweep;
    angle = a1;
    // 只有一個 slice 時特別處理（畫成全環）
    if (sweep >= Math.PI * 2 - 1e-6) {
      return `<path data-idx="${i}" class="pr-slice" fill="${s.color}" d="
        M ${cx + rOuter} ${cy}
        A ${rOuter} ${rOuter} 0 1 1 ${cx - rOuter} ${cy}
        A ${rOuter} ${rOuter} 0 1 1 ${cx + rOuter} ${cy}
        M ${cx + rInner} ${cy}
        A ${rInner} ${rInner} 0 1 0 ${cx - rInner} ${cy}
        A ${rInner} ${rInner} 0 1 0 ${cx + rInner} ${cy} Z" fill-rule="evenodd">
        <title>${escapeHtml(s.name)} · ${fmtFloat(s.pct,2)}%</title>
      </path>`;
    }
    const largeArc = sweep > Math.PI ? 1 : 0;
    const x0o = cx + rOuter * Math.cos(a0), y0o = cy + rOuter * Math.sin(a0);
    const x1o = cx + rOuter * Math.cos(a1), y1o = cy + rOuter * Math.sin(a1);
    const x0i = cx + rInner * Math.cos(a1), y0i = cy + rInner * Math.sin(a1);
    const x1i = cx + rInner * Math.cos(a0), y1i = cy + rInner * Math.sin(a0);
    const d = `M ${x0o} ${y0o} A ${rOuter} ${rOuter} 0 ${largeArc} 1 ${x1o} ${y1o} L ${x0i} ${y0i} A ${rInner} ${rInner} 0 ${largeArc} 0 ${x1i} ${y1i} Z`;
    return `<path data-idx="${i}" class="pr-slice" fill="${s.color}" d="${d}"><title>${escapeHtml(s.name)} · ${fmtFloat(s.pct,2)}%</title></path>`;
  }).join("");

  return `
    <svg class="pr-donut" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="主要產品比重">
      ${paths}
      <text x="${cx}" y="${cy - 6}" text-anchor="middle" class="pr-donut-center-label">業務收入淨額</text>
      <text x="${cx}" y="${cy + 14}" text-anchor="middle" class="pr-donut-center-value">${escapeHtml(centerLabel)}</text>
    </svg>
  `;
}

function highlightSlice(svg, idx, on) {
  if (!svg) return;
  svg.querySelectorAll("path[data-idx]").forEach((p) => {
    if (p.dataset.idx === idx) {
      p.classList.toggle("is-hover", on);
    } else {
      p.classList.toggle("is-dim", on);
    }
  });
}

// ---------- dividend ----------
function updateDividend(d) {
  const el = document.getElementById("card-dividend");
  if (!el) return;
  el.dataset.loading = "0";

  if (!d.found) {
    el.innerHTML = `
      <h3 class="section-title">股利股息</h3>
      <div class="muted">${escapeHtml(d.error || "查無此公司的股利資料。")}</div>
    `;
    return;
  }

  const dv = d.dividend;
  const asOf = d.as_of || "";

  if (!dv) {
    el.innerHTML = `
      <h3 class="section-title">股利股息</h3>
      <div class="muted">截至 ${escapeHtml(asOf)} 無已公告之股利紀錄。</div>
    `;
    return;
  }

  el.innerHTML = `
    <h3 class="section-title">股利股息（截至 ${escapeHtml(asOf)} 的最後一次發放）</h3>
    <div class="kv">
      <div><dt>所屬期間</dt><dd>${escapeHtml(dv.year) || "—"}</dd></div>
      <div><dt>現金股利</dt><dd class="num big">${fmtFloat(dv.cash_dividend, 4)} <span class="unit">元/股</span></dd></div>
      <div><dt>股票股利</dt><dd class="num">${fmtFloat(dv.stock_dividend, 4)} 元/股</dd></div>
      <div><dt>除息交易日</dt><dd class="num">${escapeHtml(dv.cash_ex_dividend_date) || "—"}</dd></div>
      <div><dt>現金發放日</dt><dd class="num">${escapeHtml(dv.cash_payment_date) || "—"}</dd></div>
      <div><dt>除權交易日</dt><dd class="num">${escapeHtml(dv.stock_ex_dividend_date) || "—"}</dd></div>
      <div><dt>公告日</dt><dd class="num">${escapeHtml(dv.announcement_date) || "—"}</dd></div>
    </div>
  `;
}

// ---------- 子公司連結點擊（事件委派）----------
resultEl.addEventListener('click', (e) => {
  const a = e.target.closest('.co-link');
  if (!a) return;
  e.preventDefault();
  const code = a.dataset.stkcode;
  if (!code) return;
  kw.value = code;
  suggestEl.innerHTML = '';
  form.requestSubmit();
});

// =====================================================================
// 三大法人買賣超（單日）查詢面板
// =====================================================================
(function initInstitutional() {
  const instForm = document.getElementById("instForm");
  const instStk = document.getElementById("instStk");
  const instDate = document.getElementById("instDate");
  const instResult = document.getElementById("instResult");
  if (!instForm || !instStk || !instDate || !instResult) return;

  // 預設日期：今天（同 asof）
  const today = new Date();
  const y = today.getFullYear();
  const m = String(today.getMonth() + 1).padStart(2, "0");
  const d = String(today.getDate()).padStart(2, "0");
  instDate.value = `${y}-${m}-${d}`;

  function fmtShares(n) {
    if (n === null || n === undefined) return "—";
    const num = Number(n);
    if (!Number.isFinite(num)) return "—";
    const s = Math.abs(num).toLocaleString("en-US");
    return num < 0 ? `-${s}` : num > 0 ? `+${s}` : s;
  }
  function signClass(n) {
    if (n === null || n === undefined) return "";
    const num = Number(n);
    if (!Number.isFinite(num) || num === 0) return "";
    return num > 0 ? "pos" : "neg";
  }
  function row(k, v) {
    const cls = signClass(v);
    return `<div class="inst-row"><span class="k">${escapeHtml(k)}</span>` +
      `<span class="v ${cls}">${escapeHtml(fmtShares(v))}</span></div>`;
  }
  function totalRow(k, v) {
    const cls = signClass(v);
    return `<div class="inst-row total"><span class="k">${escapeHtml(k)}</span>` +
      `<span class="v ${cls}">${escapeHtml(fmtShares(v))}</span></div>`;
  }

  function renderInst(data) {
    if (!data) {
      instResult.innerHTML = `<div class="inst-empty">查無資料。</div>`;
      return;
    }
    if (!data.found) {
      instResult.innerHTML =
        `<div class="inst-card"><div class="inst-empty">` +
        `找不到股票代號 <b>${escapeHtml(data.stk_code)}</b>。` +
        `請確認代號是否正確（僅支援 TWSE / TPEx 已上市/上櫃公司）。</div></div>`;
      return;
    }
    const r = data.row;
    if (!r) {
      instResult.innerHTML =
        `<div class="inst-card"><div class="head">` +
        `<span class="code">${escapeHtml(data.stk_code)}</span>` +
        `<span class="meta">${escapeHtml(data.market || "")} · ${escapeHtml(data.trade_date)}</span>` +
        `</div><div class="inst-empty">當日無三大法人資料（非交易日、當日尚未收盤、或上游暫時無回應）。</div>` +
        (data.source ? `<div class="inst-source">資料來源：${escapeHtml(data.source)}</div>` : "") +
        `</div>`;
      return;
    }
    instResult.innerHTML =
      `<div class="inst-card">
        <div class="head">
          <span class="name">${escapeHtml(r.stock_name || "")}</span>
          <span class="code">${escapeHtml(r.stk_code)}</span>
          <span class="meta">${escapeHtml(data.market || "")} · ${escapeHtml(r.trade_date)}</span>
        </div>
        <div class="inst-grid">
          ${row("外陸資買賣超（不含外資自營商）", r.foreign_investors_net_buy_sell)}
          ${row("外資自營商買賣超", r.foreign_dealers_net_buy_sell)}
          ${row("投信買賣超", r.investment_trust_net_buy_sell)}
          ${row("自營商合計", r.dealers_net_buy_sell)}
          ${row("自營商 · 自行買賣", r.dealers_proprietary_net_buy_sell)}
          ${row("自營商 · 避險", r.dealers_hedge_net_buy_sell)}
        </div>
        ${totalRow("三大法人買賣超合計（上游 payload 提供）", r.total_institutional_net_buy_sell)}
        ${data.source ? `<div class="inst-source">資料來源：${escapeHtml(data.source)}</div>` : ""}
      </div>`;
  }

  async function runInst() {
    const stk = instStk.value.trim();
    const date = instDate.value.trim();
    if (!stk || !date) return;
    const btn = instForm.querySelector("button");
    btn.disabled = true;
    instResult.innerHTML = `<div class="inst-empty">查詢中 ...</div>`;
    try {
      const r = await fetch(api(
        `/api/institutional-net-buy-sell?stk_code=${encodeURIComponent(stk)}` +
        `&date=${encodeURIComponent(date)}`
      ));
      if (!r.ok) {
        const detail = await r.text();
        let msg = detail;
        try { msg = JSON.parse(detail).detail || detail; } catch (_) {}
        instResult.innerHTML =
          `<div class="inst-card"><div class="inst-empty">錯誤：${escapeHtml(String(msg))}</div></div>`;
        return;
      }
      renderInst(await r.json());
    } catch (e) {
      instResult.innerHTML =
        `<div class="inst-card"><div class="inst-empty">請求失敗：${escapeHtml(String(e))}</div></div>`;
    } finally {
      btn.disabled = false;
    }
  }

  instForm.addEventListener("submit", (e) => {
    e.preventDefault();
    runInst();
  });
})();
