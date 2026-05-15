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
  // TWSE 偶會在欄位前加職稱（總裁:、執行長:），呈現出來
  const m = s.match(/^(總裁|執行長|CEO)[:：]/i);
  return m ? `總經理 / ${m[1]}` : "總經理";
}
function gmValue(s) {
  if (!s) return "—";
  return s.replace(/^(總裁|執行長|CEO)[:：]\s*/i, "");
}

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

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = kw.value.trim();
  if (!q) return;
  // 若是純數字當作代號；否則先呼叫 search 拿第一筆
  let stockId = q;
  if (!/^\d{4,6}[A-Z]?$/.test(q)) {
    const r = await fetch(api(`/api/search?q=${encodeURIComponent(q)}&limit=1`));
    const data = await r.json();
    if (!data.results || data.results.length === 0) {
      renderError(`查無「${q}」相關公司`);
      return;
    }
    stockId = data.results[0].stock_id;
  }
  await loadCompany(stockId, asof.value);
});

async function loadCompany(stockId, asOf) {
  emptyEl.classList.add("hidden");
  resultEl.classList.remove("hidden");
  resultEl.innerHTML = `<div class="card"><div class="muted">查詢中…</div></div>`;
  try {
    const url = api(`/api/company/${encodeURIComponent(stockId)}${asOf ? `?as_of=${asOf}` : ""}`);
    const r = await fetch(url);
    if (!r.ok) {
      const errBody = await r.json().catch(() => ({}));
      renderError(errBody.detail || `HTTP ${r.status}`);
      return;
    }
    const data = await r.json();
    render(data);
  } catch (e) {
    renderError(e.message || "查詢失敗");
  }
}

function renderError(msg) {
  resultEl.innerHTML = `<div class="card"><div class="error">${msg}</div></div>`;
}

// ---------- 產業價值鏈 ----------
function renderValueChainCard(selfId, vc) {
  if (!vc || vc.status === 'loading') {
    return `<div class="card"><h3 class="section-title">產業鏈上下游定位</h3><div class="muted">首次查詢背景載入中（預計 5~15 秒），請稍後重新查詢。</div></div>`;
  }
  if (!vc || vc.status !== 'ready') {
    return ''; // unavailable: 隱藏
  }
  const mb = vc.memberships || [];
  if (mb.length === 0) {
    return `<div class="card"><h3 class="section-title">產業鏈上下游定位</h3><div class="muted">本公司未被櫃買中心『產業價值鏈資訊平台』列入任一產業鏈。</div></div>`;
  }

  // 依 ic_code 分組 memberships
  const groups = {};
  for (const m of mb) {
    if (!groups[m.ic_code]) groups[m.ic_code] = { ic_name: m.ic_name, items: [] };
    groups[m.ic_code].items.push(m);
  }

  const neighbors = vc.neighbors_by_chain || {};

  let html = `<div class="card"><h3 class="section-title">產業鏈上下游定位</h3>`;
  for (const ic_code of Object.keys(groups)) {
    const g = groups[ic_code];
    const nb = neighbors[ic_code] || { upstream: [], midstream: [], downstream: [] };
    // 本公司在該鏈的 segment 集合
    const selfSegs = new Set(g.items.map((m) => m.segment));
    html += `
      <div class="chain-block" data-ic="${ic_code}">
        <div class="chain-head">
          <span class="chain-name">${escapeHtml(g.ic_name)}</span>
          <span class="chain-tag">${ic_code}</span>
          <button class="btn-expand" data-ic="${ic_code}">展開全圖</button>
        </div>
        <div class="chain-self">
          ${g.items.map((m) =>
            `<span class="seg-pill seg-${m.segment === '上游' ? 'up' : m.segment === '中游' ? 'mid' : 'down'}">
              <b>${m.segment}</b> · ${escapeHtml(m.top_name)}${m.sub_name && m.sub_name !== m.top_name ? ' → ' + escapeHtml(m.sub_name) : ''}
            </span>`
          ).join('')}
        </div>
        <div class="chain-stream">
          ${renderStreamRow('上游', nb.upstream, selfSegs.has('上游') ? selfId : null, 6)}
          ${renderStreamRow('中游', nb.midstream, selfSegs.has('中游') ? selfId : null, 6)}
          ${renderStreamRow('下游', nb.downstream, selfSegs.has('下游') ? selfId : null, 6)}
        </div>
        <div class="chain-full hidden" id="chain-full-${ic_code}"></div>
      </div>
    `;
  }
  html += '</div>';
  return html;
}

function renderStreamRow(label, list, selfId, maxShow) {
  if (!list || list.length === 0) return '';
  const shown = list.slice(0, maxShow);
  const moreCount = list.length - shown.length;
  const cls = label === '上游' ? 'up' : label === '中游' ? 'mid' : 'down';
  return `
    <div class="stream-row stream-${cls}">
      <span class="stream-label">${label}</span>
      <span class="stream-companies">
        ${shown.map((c) =>
          `<a class="co-link${c.stk_code === selfId ? ' is-self' : ''}" data-stkcode="${c.stk_code}" title="${escapeHtml(c.top_name + ' / ' + c.sub_name)}">${c.stk_code} ${escapeHtml(c.name)}</a>`
        ).join('')}
        ${moreCount > 0 ? `<span class="more">+${moreCount}</span>` : ''}
      </span>
    </div>
  `;
}

// 展開/收起全圖
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
    const r = await fetch(api(`/api/chain/${ic_code}`));
    const data = await r.json();
    target.innerHTML = renderFullChain(data, selfId);
  } catch (e) {
    target.innerHTML = `<div class="error">載入失敗</div>`;
  }
}

function renderFullChain(chain, selfId) {
  if (!chain || !chain.segments) return '<div class="muted">無資料</div>';
  const segOrder = ['上游', '中游', '下游'];
  let html = '<div class="full-chain">';
  for (const seg of segOrder) {
    const tops = chain.segments[seg] || [];
    if (tops.length === 0) continue;
    const cls = seg === '上游' ? 'up' : seg === '中游' ? 'mid' : 'down';
    html += `<div class="full-seg full-seg-${cls}"><div class="full-seg-title">${seg}</div>`;
    for (const top of tops) {
      html += `<div class="full-top"><div class="full-top-name">${escapeHtml(top.top_name)}</div>`;
      for (const sub of top.sub_chains || []) {
        const showName = sub.sub_name && sub.sub_name !== top.top_name;
        html += `<div class="full-sub">${showName ? `<div class="full-sub-name">${escapeHtml(sub.sub_name)} <span class="muted">· ${(sub.companies||[]).length} 家</span></div>` : ''}
          <div class="full-companies">
            ${(sub.companies||[]).map((c) =>
              `<a class="co-link${c.stk_code === selfId ? ' is-self' : ''}" data-stkcode="${c.stk_code}">${c.stk_code} ${escapeHtml(c.name)}</a>`
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

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function render(d) {
  const b = d.basic || {};
  const eps = d.eps || {};
  const rev = d.revenue || {};
  const ni = d.net_income || {};
  const dv = d.dividend;

  const epsTtmTag = (eps.ttm_quarters || []).map((q) => `<span class="q">${q}</span>`).join("");
  const niTtmTag = (ni.ttm_quarters || []).map((q) => `<span class="q">${q}</span>`).join("");

  // 主要營業項目（商工 API 所營事業）
  const bi = b.business_items || { narrative: [], categories: [] };
  const narrative = bi.narrative || [];
  const categories = bi.categories || [];

  // 產業價值鏈定位
  const vc = d.value_chain || { status: 'unavailable', memberships: [], neighbors_by_chain: {} };

  let html = `
    <div class="card">
      <div class="head">
        <span class="code">${b.short_name && b.short_name !== d.stock_id ? d.stock_id : d.stock_id}</span>
        <span class="name">${b.short_name || b.company_name || ""}</span>
        <span class="market">${b.market || ""}</span>
        <span class="asof">基準日 ${d.as_of}</span>
      </div>
      <div class="kv">
        <div><dt>公司全名</dt><dd>${b.company_name || "—"}</dd></div>
        <div><dt>英文名稱</dt><dd>${b.english_name || "—"}</dd></div>
        <div><dt>股票代號</dt><dd class="num">${d.stock_id}</dd></div>
        <div><dt>統一編號</dt><dd class="num">${b.tax_id || "—"}</dd></div>
        <div><dt>實收資本額</dt><dd class="num big">${fmtMoneyTW(b.paid_in_capital)} <span class="unit">NTD</span></dd></div>
        <div><dt>產業別</dt><dd>${b.industry_name || "—"}</dd></div>
        <div><dt>${gmLabel(b.general_manager)}</dt><dd>${gmValue(b.general_manager)}</dd></div>
        <div><dt>董事長</dt><dd>${b.chairman || "—"}</dd></div>
        <div><dt>成立日期</dt><dd class="num">${b.incorporation_date || "—"}</dd></div>
        <div><dt>上市/上櫃日期</dt><dd class="num">${b.listing_date || "—"}</dd></div>
        <div><dt>網站</dt><dd>${b.website ? `<a href="${b.website}" target="_blank" rel="noopener">${b.website}</a>` : "—"}</dd></div>
        <div><dt>地址</dt><dd>${b.address || "—"}</dd></div>
      </div>
    </div>

    ${renderValueChainCard(d.stock_id, vc)}

    <div class="card">
      <h3 class="section-title">主要營業項目（公司登記所營事業）</h3>
      ${
        narrative.length
          ? `<ol class="biz-list">${narrative.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ol>`
          : `<div class="muted" style="margin-bottom:8px">本公司未填寫敘述性所營事業。</div>`
      }
      ${
        categories.length
          ? `<div class="biz-cats"><div class="muted" style="font-size:12px;margin-bottom:6px">行業分類（中華民國行業標準分類代碼）</div>${categories
              .map((c) => `<span class="cat-pill" title="${c.code}">${escapeHtml(c.desc)}</span>`)
              .join("")}</div>`
          : ""
      }
    </div>

    <div class="card">
      <h3 class="section-title">獲利 · 截至 ${d.as_of}（TTM 滾動四季）</h3>
      <div class="kv">
        <div>
          <dt>EPS（TTM）</dt>
          <dd class="num big">${eps.ttm != null ? fmtFloat(eps.ttm, 2) : "—"} <span class="unit">元</span></dd>
          <div class="tag-row">${epsTtmTag || '<span class="muted">資料不足 4 季</span>'}</div>
        </div>
        <div>
          <dt>最新單季 EPS</dt>
          <dd class="num">${eps.latest_quarter_value != null ? fmtFloat(eps.latest_quarter_value, 2) + " 元" : "—"}</dd>
          <div class="tag-row">${eps.latest_quarter_date ? `<span class="q">${eps.latest_quarter_date}</span>` : ""}</div>
        </div>
        <div>
          <dt>淨利（TTM）</dt>
          <dd class="num big">${fmtMoneyTW(ni.ttm)} <span class="unit">NTD</span></dd>
          <div class="tag-row">${niTtmTag || '<span class="muted">資料不足 4 季</span>'}</div>
        </div>
        <div>
          <dt>最新單季淨利</dt>
          <dd class="num">${fmtMoneyTW(ni.latest_quarter_value)}</dd>
          <div class="tag-row">${ni.latest_quarter_date ? `<span class="q">${ni.latest_quarter_date}</span>` : ""}</div>
        </div>
        <div>
          <dt>營業利潤率（TTM）</dt>
          <dd class="num big">${d.operating_margin_pct != null ? fmtFloat(d.operating_margin_pct, 2) + "%" : "—"}</dd>
        </div>
      </div>
    </div>

    <div class="card">
      <h3 class="section-title">營收</h3>
      <div class="kv">
        <div>
          <dt>最新月營收 (${rev.latest_month_label || "—"})</dt>
          <dd class="num big">${fmtMoneyTW(rev.latest_month_value)} <span class="unit">NTD</span></dd>
          <div class="tag-row"><span class="q">YoY ${fmtPct(rev.latest_month_yoy_pct)}</span></div>
        </div>
        <div>
          <dt>近 12 月營收（TTM）</dt>
          <dd class="num big">${fmtMoneyTW(rev.ttm_value)} <span class="unit">NTD</span></dd>
          <div class="tag-row"><span class="q">YoY ${fmtPct(rev.ttm_yoy_pct)}</span></div>
        </div>
        <div>
          <dt>季財報合計營收（參照）</dt>
          <dd class="num">${fmtMoneyTW(rev.ttm_from_financial_statements)}</dd>
        </div>
      </div>
    </div>
  `;

  if (dv) {
    html += `
      <div class="card">
        <h3 class="section-title">股利股息（截至 ${d.as_of} 的最後一次發放）</h3>
        <div class="kv">
          <div><dt>所屬期間</dt><dd>${dv.year || "—"}</dd></div>
          <div><dt>現金股利</dt><dd class="num big">${fmtFloat(dv.cash_dividend, 4)} <span class="unit">元/股</span></dd></div>
          <div><dt>股票股利</dt><dd class="num">${fmtFloat(dv.stock_dividend, 4)} 元/股</dd></div>
          <div><dt>除息交易日</dt><dd class="num">${dv.cash_ex_dividend_date || "—"}</dd></div>
          <div><dt>現金發放日</dt><dd class="num">${dv.cash_payment_date || "—"}</dd></div>
          <div><dt>除權交易日</dt><dd class="num">${dv.stock_ex_dividend_date || "—"}</dd></div>
          <div><dt>公告日</dt><dd class="num">${dv.announcement_date || "—"}</dd></div>
        </div>
      </div>
    `;
  } else {
    html += `<div class="card"><h3 class="section-title">股利股息</h3><div class="muted">截至 ${d.as_of} 無已公告之股利紀錄。</div></div>`;
  }

  html += `
    <div class="card">
      <h3 class="section-title">資料來源</h3>
      <div class="muted" style="font-size:13px">${(d.sources || []).join(" · ")}</div>
      <div class="muted" style="font-size:12px;margin-top:6px">註：TTM = trailing twelve months，回溯基準日往前的滾動四季 / 十二個月加總。基準日設為過去日期時，會以該日「已公告」的最後一份資料為準。</div>
    </div>
  `;

  resultEl.innerHTML = html;
  resultEl.scrollIntoView({ behavior: "smooth", block: "start" });

  // 綁定「展開全圖」按鈕
  resultEl.querySelectorAll('.btn-expand').forEach((btn) => {
    btn.addEventListener('click', () => {
      expandChainFull(btn.dataset.ic, d.stock_id);
      btn.textContent = btn.textContent === '展開全圖' ? '收起全圖' : '展開全圖';
    });
  });
}

// 公司連結 click delegation。render 多次不重複綁定。
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
