function formatNumber(n) {
  if (n === null || n === undefined) return 'n/a';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(n);
}

function nodeClass(n) {
  if (n.is_seed) return 'dag-node-seed';
  if (n.is_raw) return 'dag-node-raw';
  return 'dag-node-normal';
}

async function loadDag() {
  const res = await fetch('/api/dag');
  const data = await res.json();
  renderDag(data);
}

function renderDag(data) {
  const { nodes, edges } = data;
  const layers = {};
  nodes.forEach((n) => {
    layers[n.layer] = layers[n.layer] || [];
    layers[n.layer].push(n);
  });

  const colWidth = 240;
  const rowHeight = 74;
  const nodePos = {};
  let maxRows = 0;
  Object.keys(layers).forEach((layerKey) => {
    layers[layerKey].forEach((n, idx) => {
      nodePos[n.id] = { x: Number(layerKey) * colWidth + 30, y: idx * rowHeight + 30 };
      maxRows = Math.max(maxRows, idx + 1);
    });
  });
  const maxLayer = Math.max(0, ...nodes.map((n) => n.layer));

  const nodesEl = document.getElementById('dag-nodes');
  const svg = document.getElementById('dag-svg');
  const container = document.getElementById('dag-container');
  nodesEl.innerHTML = '';
  svg.innerHTML = '';

  const width = (maxLayer + 1) * colWidth + 60;
  const height = maxRows * rowHeight + 60;
  container.style.width = width + 'px';
  container.style.height = Math.min(height, 560) + 'px';
  svg.setAttribute('width', width);
  svg.setAttribute('height', height);
  nodesEl.style.width = width + 'px';
  nodesEl.style.height = height + 'px';

  edges.forEach((e) => {
    const a = nodePos[e.from];
    const b = nodePos[e.to];
    if (!a || !b) return;
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', a.x + 200);
    line.setAttribute('y1', a.y + 23);
    line.setAttribute('x2', b.x);
    line.setAttribute('y2', b.y + 23);
    line.setAttribute('class', 'dag-edge');
    svg.appendChild(line);
  });

  nodes.forEach((n) => {
    const pos = nodePos[n.id];
    const div = document.createElement('div');
    div.className = 'dag-node ' + nodeClass(n);
    div.style.left = pos.x + 'px';
    div.style.top = pos.y + 'px';
    div.innerHTML =
      `<div class="dag-node-title">${n.id}</div>` +
      `<div class="dag-node-count">pop: ${formatNumber(n.pop_row_count)}</div>`;
    div.addEventListener('click', () => loadColumns(n.id));
    nodesEl.appendChild(div);
  });
}

async function loadColumns(name) {
  const el = document.getElementById('columns-content');
  el.textContent = '載入中...';
  try {
    const res = await fetch(`/api/view/${encodeURIComponent(name)}/columns`);
    if (!res.ok) {
      const err = await res.json();
      el.textContent = err.detail || '查無欄位';
      return;
    }
    const data = await res.json();
    const rows = data.columns
      .map((c) => `<tr><td>${c.name}</td><td>${c.type}</td></tr>`)
      .join('');
    el.innerHTML =
      `<h3>poc.${data.table}</h3>` +
      `<table class="col-table"><thead><tr><th>欄位</th><th>型別</th></tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    el.textContent = '查詢失敗：' + e;
  }
}

async function loadGrowth() {
  const res = await fetch('/api/pop/growth');
  const data = await res.json();
  const tbody = document.querySelector('#growth-table tbody');
  tbody.innerHTML = data.growth
    .map(
      (g) => `
      <tr>
        <td>${g.table_name}</td>
        <td>${g.last_count ?? '-'}</td>
        <td>${g.sample_count}</td>
        <td>${g.hours_observed}</td>
        <td>${g.estimated_daily_growth === null ? '樣本不足' : g.estimated_daily_growth}</td>
      </tr>`
    )
    .join('');
}

async function loadSchedules() {
  const res = await fetch('/api/schedules');
  const data = await res.json();
  const tbody = document.querySelector('#schedules-table tbody');
  tbody.innerHTML = data.schedules
    .map(
      (s) => `
      <tr>
        <td>${s.jobname}</td>
        <td>${s.schedule}</td>
        <td>${s.active ? '是' : '否'}</td>
        <td>${s.last_status ?? '-'}</td>
        <td>${s.last_start_time ?? '-'}</td>
        <td>${s.last_end_time ?? '-'}</td>
      </tr>`
    )
    .join('');
}

let jobPollTimer = null;

function showJobStatus(job) {
  const panel = document.getElementById('job-status');
  if (!job) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');
  document.getElementById('job-name').textContent = job.name;
  const badge = document.getElementById('job-badge');
  badge.textContent = job.status;
  badge.className = 'badge badge-' + job.status;
  document.getElementById('job-log').textContent = job.logs.join('\n');
}

async function pollJob() {
  const res = await fetch('/api/jobs/current');
  const data = await res.json();
  showJobStatus(data.job);
  clearTimeout(jobPollTimer);
  if (data.job && data.job.status === 'running') {
    jobPollTimer = setTimeout(pollJob, 1500);
  } else {
    loadGrowth();
    loadSchedules();
    loadDag();
  }
}

async function triggerJob(url) {
  try {
    const res = await fetch(url, { method: 'POST' });
    if (res.status === 409) {
      alert('已有工作在執行中，請稍候');
      return;
    }
    const data = await res.json();
    showJobStatus(data.job);
    pollJob();
  } catch (e) {
    alert('觸發失敗：' + e);
  }
}

document.getElementById('btn-probe').addEventListener('click', () => {
  if (
    !confirm(
      'probe_all_throughput(restart=True) 會清空 throughput_config.json 並對每個 seed table 重新掃描，過程可能耗時甚久，確定執行？'
    )
  )
    return;
  triggerJob('/api/jobs/probe_all_throughput?restart=true');
});
document.getElementById('btn-schedules').addEventListener('click', () => {
  triggerJob('/api/jobs/setup_schedules');
});
document.getElementById('btn-truncate').addEventListener('click', () => {
  if (!confirm('truncate_cron_jobs 會停止所有資料抓取排程，確定執行？')) return;
  triggerJob('/api/jobs/truncate_cron_jobs');
});
document.getElementById('btn-refresh').addEventListener('click', () => {
  loadDag();
  loadGrowth();
  loadSchedules();
  pollJob();
});

loadDag();
loadGrowth();
loadSchedules();
pollJob();
setInterval(() => {
  loadDag();
  loadGrowth();
  loadSchedules();
}, 30000);
