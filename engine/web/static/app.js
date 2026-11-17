'use strict';

/* 本机页面：所有内容都来自 /api/*；页面只做排版，不新增结论或数字。 */

const state = {
  currentAsk: null,
  overview: null,
  pools: null,
  activePool: 'quality',
  poolSort: { key: null, dir: 1 },
  indices: null,
  ledger: null,
  status: null,
  modal: null,
  quoteDate: null,
};

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtMoney(value) {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtDistance(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const number = Number(value);
  return (number >= 0 ? '+' : '') + number.toFixed(1) + '%';
}

function fmtPlain(value) {
  if (value === null || value === undefined) return '—';
  return String(value);
}

async function api(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      throw new Error(text);
    }
  }
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function postJSON(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function fillSelect(select, values, keep) {
  const previous = keep === undefined ? select.value : keep;
  select.innerHTML = values.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
  if (values.includes(previous)) select.value = previous;
}

/* ---------- 卡片排版 ---------- */

function inlineBold(text) {
  return esc(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

function conclusionClass(label) {
  const text = String(label || '');
  if (text.includes('候选买点')) return 'candidate';
  if (text.includes('回避')) return 'avoid';
  if (text.includes('等待')) return 'wait';
  return 'unknown';
}

function lineClass(line) {
  if (line.startsWith('三档价值')) return 'metric-line values';
  if (line.startsWith('现价 ')) return 'metric-line';
  if (line.startsWith('来源与日期')) return 'source-line';
  if (line.startsWith('不构成投资建议')) return 'disclaimer';
  return '';
}

function renderCard(markdown) {
  const lines = String(markdown || '').split('\n');
  const out = [];
  let inList = false;
  const closeList = () => {
    if (inList) {
      out.push('</ul>');
      inList = false;
    }
  };
  for (const line of lines) {
    if (!line.trim()) {
      closeList();
      out.push('<div class="gap"></div>');
      continue;
    }
    let match = line.match(/^类型：(.+)$/);
    if (match) {
      closeList();
      const parts = match[1].split('｜');
      const type = parts.shift();
      out.push(
        '<div class="type-line"><span class="type-chip">' + esc(type) + '</span>' +
        '<span class="type-meaning">' + esc(parts.join('｜')) + '</span></div>'
      );
      continue;
    }
    if (line.startsWith('提示：') && line.includes('护城河')) {
      closeList();
      out.push('<div class="moat-hint">' + inlineBold(line) + '</div>');
      continue;
    }
    match = line.match(/^\*\*结论：(.+?)\*\*\s*—\s*(.*)$/);
    if (match) {
      closeList();
      out.push(
        '<div class="headline ' + conclusionClass(match[1]) + '">' +
        '<span class="conclusion">结论：' + esc(match[1]) + '</span>' +
        '<span class="reason">' + inlineBold(match[2]) + '</span></div>'
      );
      continue;
    }
    if (line.startsWith('- ')) {
      if (!inList) {
        out.push('<ul class="list">');
        inList = true;
      }
      out.push('<li>' + inlineBold(line.slice(2)) + '</li>');
      continue;
    }
    closeList();
    if (line.startsWith('【详情】')) {
      out.push('<div class="detail-title">详情</div>');
      continue;
    }
    out.push('<p class="' + lineClass(line) + '">' + inlineBold(line) + '</p>');
  }
  closeList();
  return out.join('');
}

/* ---------- 页签 ---------- */

const loaders = {
  ask: () => {},
  overview: loadOverview,
  pools: loadPools,
  indices: loadIndices,
  ledger: loadLedger,
  status: loadStatus,
};

function activatePanel(name) {
  if (loaders[name] && location.hash !== '#' + name) {
    history.replaceState(null, '', '#' + name);
  }
  document.querySelectorAll('.tab').forEach((button) => {
    button.classList.toggle('active', button.dataset.panel === name);
  });
  document.querySelectorAll('.panel').forEach((panel) => {
    panel.classList.toggle('active', panel.id === 'panel-' + name);
  });
  const loader = loaders[name];
  if (loader) loader();
}

function initTabs() {
  document.querySelectorAll('.tab').forEach((button) => {
    button.addEventListener('click', () => activatePanel(button.dataset.panel));
  });
}

/* ---------- 提问 ---------- */

function initAsk() {
  $('ask-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const question = $('ask-input').value.trim();
    if (question) runAsk(question, false);
  });
  const examples = [
    '招商银行怎么样',
    '贵州茅台怎么样',
    '现在有哪些候选买点',
    '好公司里谁离买点最近',
    '好公司池有哪些',
    '数据是哪天的',
  ];
  $('ask-examples').innerHTML = examples
    .map((question) => `<button class="chip" data-q="${esc(question)}">${esc(question)}</button>`)
    .join('');
  $('ask-examples').addEventListener('click', (event) => {
    const question = event.target.dataset.q;
    if (!question) return;
    $('ask-input').value = question;
    runAsk(question, false);
  });
}

async function runAsk(question, detail) {
  state.currentAsk = { question, detail };
  $('ask-result').innerHTML = '<p class="hint">正在按已批准规则计算…</p>';
  try {
    const payload = await postJSON('/api/ask', { question, detail });
    const intentLabel = payload.intent && payload.intent.intent_label ? payload.intent.intent_label : '—';
    $('ask-result').innerHTML =
      '<div class="card">' + renderCard(payload.markdown) + '</div>' +
      '<div class="card-actions"><button class="link" id="ask-detail">' +
      (detail ? '收起详细' : '展开详细') +
      '</button><span class="meta">问法：' + esc(intentLabel) + '</span></div>';
    $('ask-detail').addEventListener('click', () => runAsk(question, !detail));
  } catch (error) {
    $('ask-result').innerHTML = '<div class="error">查询失败：' + esc(error.message) + '</div>';
  }
}

/* ---------- 买点总览 ---------- */

async function loadOverview() {
  if (state.overview) return;
  $('overview-body').innerHTML = '<p class="hint">正在重算两个池与银行池的排行（约 1–2 秒）…</p>';
  try {
    state.overview = await api('/api/overview');
    fillSelect($('overview-pool'), ['全部池'].concat(state.overview.pool_filters));
    fillSelect($('overview-industry'), ['全部行业'].concat(state.overview.industries));
    fillSelect($('overview-ownership'), ['全部所有制'].concat(state.overview.ownerships));
    ['overview-pool', 'overview-industry', 'overview-ownership'].forEach((id) => {
      $(id).addEventListener('change', renderOverview);
    });
    renderOverview();
  } catch (error) {
    $('overview-body').innerHTML = '<div class="error">加载失败：' + esc(error.message) + '</div>';
  }
}

function overviewRowHtml(row) {
  return (
    '<tr>' +
    '<td class="num">' + esc(row.code) + '</td>' +
    '<td>' + esc(row.name) + '</td>' +
    '<td>' + esc(row.type) + '</td>' +
    '<td class="num">' + fmtMoney(row.price) + '</td>' +
    '<td class="num">' + fmtMoney(row.entry) + '</td>' +
    '<td class="num">' + fmtDistance(row.distance) + '</td>' +
    '<td><span class="badge ' + conclusionClass(row.conclusion_label) + '">' + esc(row.conclusion_label) + '</span></td>' +
    '<td>' + esc(row.grade || '—') + '</td>' +
    '</tr>'
  );
}

function overviewTable(rows, emptyText) {
  if (!rows.length) return '<p class="hint">' + esc(emptyText) + '</p>';
  const types = state.overview.types.filter((type) => rows.some((row) => row.type === type));
  const body = types
    .map((type) => {
      const items = rows.filter((row) => row.type === type);
      return (
        '<tr class="group-row"><td colspan="8">' + esc(type) + '（' + items.length + ' 家）</td></tr>' +
        items.map(overviewRowHtml).join('')
      );
    })
    .join('');
  return (
    '<table class="grid"><thead><tr>' +
    '<th>代码</th><th>名称</th><th>类型</th><th>现价</th><th>门槛</th><th>距门槛</th><th>结论</th><th>等级</th>' +
    '</tr></thead><tbody>' + body + '</tbody></table>'
  );
}

function renderOverview() {
  const payload = state.overview;
  const pool = $('overview-pool').value;
  const industry = $('overview-industry').value;
  const ownership = $('overview-ownership').value;
  const rows = payload.rows.filter((row) => {
    if (pool !== '全部池' && !(row.pools || []).includes(pool)) return false;
    if (industry !== '全部行业' && row.industry !== industry) return false;
    if (ownership !== '全部所有制' && row.ownership !== ownership) return false;
    return true;
  });
  const candidates = rows.filter((row) => row.conclusion === 'candidate_buy_point');
  const closest = rows.filter((row) => row.conclusion !== 'candidate_buy_point');
  $('overview-summary').textContent =
    '基于 ' + (payload.data_date || '—') + ' 行情，重算 ' + (payload.evaluated || '—') +
    ' 家；当前显示 ' + rows.length + ' 家，其中候选买点 ' + candidates.length + ' 个。';
  $('overview-body').innerHTML =
    '<h3>候选买点（' + candidates.length + ' 个）</h3>' +
    overviewTable(candidates, '按当前筛选没有候选买点。') +
    '<h3>距买点最近（尚未到买点，共 ' + closest.length + ' 家）</h3>' +
    overviewTable(closest, '按当前筛选没有可排行的公司。') +
    '<p class="hint">' + esc(payload.note) + '</p>' +
    '<details class="raw"><summary>看对应的终端排行卡片原文</summary>' +
    '<div class="card">' + renderCard(payload.card_markdown) + '</div></details>';
}

/* ---------- 三个池 ---------- */

async function loadPools() {
  if (state.pools) return;
  $('pool-body').innerHTML = '<p class="hint">正在读取三池名单与成绩单指标…</p>';
  try {
    state.pools = await api('/api/pools');
    $('pool-tabs').innerHTML = state.pools.order
      .map(
        (key, index) =>
          '<button class="subtab' + (index === 0 ? ' active' : '') + '" data-pool="' + key + '">' +
          esc(state.pools.pools[key].title) + '</button>'
      )
      .join('');
    $('pool-tabs').addEventListener('click', (event) => {
      const key = event.target.dataset.pool;
      if (!key) return;
      state.activePool = key;
      state.poolSort = { key: null, dir: 1 };
      document.querySelectorAll('#pool-tabs .subtab').forEach((button) => {
        button.classList.toggle('active', button.dataset.pool === key);
      });
      renderPools();
    });
    ['pool-search', 'pool-industry', 'pool-ownership', 'pool-cycle'].forEach((id) => {
      $(id).addEventListener('input', renderPools);
    });
    renderPools();
  } catch (error) {
    $('pool-body').innerHTML = '<div class="error">加载失败：' + esc(error.message) + '</div>';
  }
}

function renderPools() {
  const pool = state.pools.pools[state.activePool];
  const industries = ['全部行业'].concat(Array.from(new Set(pool.rows.map((row) => row.industry))).sort());
  const ownerships = ['全部所有制'].concat(Array.from(new Set(pool.rows.map((row) => row.ownership))).sort());
  const cycles = ['全部周期'].concat(Array.from(new Set(pool.rows.map((row) => row.cycle))).sort());
  if ($('pool-industry').dataset.pool !== state.activePool) {
    fillSelect($('pool-industry'), industries);
    fillSelect($('pool-ownership'), ownerships);
    fillSelect($('pool-cycle'), cycles);
    $('pool-industry').dataset.pool = state.activePool;
  }
  const excluded = pool.excluded || [];
  $('pool-meta').textContent =
    pool.title + '：' + pool.count + ' 家｜规则 ' + (pool.policy_id || '—') +
    '（' + (pool.policy_status || '—') + '）｜快照 ' + (pool.snapshot_id || '—') +
    '（' + (pool.as_of || '—') + '）。点任一公司可看卡片与成绩单；入池不等于买入。' +
    (excluded.length
      ? ' 另有 ' + excluded.length + ' 家因红线出池：' +
        excluded.slice(0, 6).map((item) => item.code + ' ' + item.name).join('、') +
        (excluded.length > 6 ? '……' : '') + '（见「数据状态 → 最近变动」）。'
      : '');
  const search = $('pool-search').value.trim().toLowerCase();
  const industry = $('pool-industry').value;
  const ownership = $('pool-ownership').value;
  const cycle = $('pool-cycle').value;
  let rows = pool.rows.filter((row) => {
    if (industry !== '全部行业' && row.industry !== industry) return false;
    if (ownership !== '全部所有制' && row.ownership !== ownership) return false;
    if (cycle !== '全部周期' && row.cycle !== cycle) return false;
    if (!search) return true;
    const haystack = (row.code + ' ' + row.name + ' ' + row.industry).toLowerCase();
    return haystack.includes(search);
  });
  if (state.poolSort.key) {
    const key = state.poolSort.key;
    const dir = state.poolSort.dir;
    rows = rows.slice().sort((left, right) => comparePoolRow(left, right, key) * dir);
  }
  const headers = ['代码', '名称', '行业', '所有制', '周期'].concat(pool.columns);
  const keys = ['code', 'name', 'industry', 'ownership', 'cycle'].concat(pool.columns.map((label) => 'metric:' + label));
  const head = headers
    .map((label, index) => '<th data-key="' + esc(keys[index]) + '">' + esc(label) + '</th>')
    .join('');
  const body = rows
    .map((row) => {
      const metrics = new Map(row.metrics.map((item) => [item.label, item.value]));
      const cells = [
        '<td class="num">' + esc(row.code) + '</td>',
        '<td>' + esc(row.name) + '</td>',
        '<td>' + esc(row.industry) + '</td>',
        '<td>' + esc(row.ownership) + '</td>',
        '<td>' + esc(row.cycle) + '</td>',
      ].concat(pool.columns.map((label) => '<td class="num">' + esc(metrics.get(label) || '—') + '</td>'));
      return '<tr data-code="' + esc(row.code) + '">' + cells.join('') + '</tr>';
    })
    .join('');
  $('pool-body').innerHTML =
    '<p class="hint">显示 ' + rows.length + ' / ' + pool.count + ' 家（点表头排序）</p>' +
    '<table class="grid"><thead><tr>' + head + '</tr></thead><tbody>' +
    (body || '<tr><td colspan="' + headers.length + '">没有符合条件的公司</td></tr>') +
    '</tbody></table>';
  $('pool-body').querySelectorAll('th').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.key;
      if (state.poolSort.key === key) {
        state.poolSort.dir = -state.poolSort.dir;
      } else {
        state.poolSort = { key: key, dir: 1 };
      }
      renderPools();
    });
  });
  $('pool-body').querySelectorAll('tbody tr[data-code]').forEach((tr) => {
    tr.addEventListener('click', () => openCompany(tr.dataset.code, state.activePool));
  });
}

function comparePoolRow(left, right, key) {
  if (key.startsWith('metric:')) {
    const label = key.slice(7);
    const a = valueOfMetric(left, label);
    const b = valueOfMetric(right, label);
    return compareValues(a, b);
  }
  return compareValues(left[key], right[key]);
}

function valueOfMetric(row, label) {
  const found = row.metrics.find((item) => item.label === label);
  if (!found) return null;
  const number = parseFloat(String(found.value).replace(/[^0-9.+-]/g, ''));
  return Number.isNaN(number) ? String(found.value) : number;
}

function compareValues(a, b) {
  if (a === null || a === undefined || a === '') return 1;
  if (b === null || b === undefined || b === '') return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), 'zh-Hans-CN');
}

/* ---------- 单家公司弹窗 ---------- */

async function openCompany(code, pool) {
  state.modal = null;
  $('modal').classList.remove('hidden');
  $('modal-title').textContent = '读取卡片…';
  $('modal-body').innerHTML = '<p class="hint">正在按已批准规则重算这张卡片…</p>';
  setModalTab('card');
  try {
    const payload = await api(
      '/api/company?code=' + encodeURIComponent(code) + '&pool=' + encodeURIComponent(pool || '')
    );
    if (!payload.ok) throw new Error(payload.error || '读取失败');
    state.modal = payload;
    $('modal-title').textContent = payload.name + '（' + payload.code + '）｜类型 ' + payload.company_type;
    renderModalTab('card');
  } catch (error) {
    $('modal-body').innerHTML = '<div class="error">读取失败：' + esc(error.message) + '</div>';
  }
}

function setModalTab(which) {
  $('modal-tab-card').classList.toggle('active', which === 'card');
  $('modal-tab-score').classList.toggle('active', which === 'score');
}

function renderModalTab(which) {
  const payload = state.modal;
  if (!payload) return;
  if (which === 'score') {
    if (!payload.scorecard) {
      $('modal-body').innerHTML = '<p class="hint">这次点击没有指定池。</p>';
      return;
    }
    $('modal-body').innerHTML = '<div class="card">' + renderCard(payload.scorecard.markdown) + '</div>';
    return;
  }
  $('modal-body').innerHTML =
    '<div class="card">' + renderCard(payload.card.markdown) + '</div>' +
    '<div class="card-actions"><button class="link" id="modal-detail">展开详细</button></div>';
  $('modal-detail').addEventListener('click', () => loadModalDetail(payload));
}

async function loadModalDetail(payload) {
  $('modal-body').innerHTML = '<p class="hint">正在展开详细…</p>';
  try {
    const detailed = await api(
      '/api/company?code=' + encodeURIComponent(payload.code) +
      '&pool=' + encodeURIComponent(payload.pool || '') + '&detail=1'
    );
    $('modal-body').innerHTML = '<div class="card">' + renderCard(detailed.card.markdown) + '</div>';
  } catch (error) {
    $('modal-body').innerHTML = '<div class="error">展开失败：' + esc(error.message) + '</div>';
  }
}

function closeModal() {
  $('modal').classList.add('hidden');
  state.modal = null;
}

function initModal() {
  $('modal-close').addEventListener('click', closeModal);
  $('modal').addEventListener('click', (event) => {
    if (event.target === $('modal')) closeModal();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('modal').classList.contains('hidden')) closeModal();
  });
  $('modal-tab-card').addEventListener('click', () => {
    setModalTab('card');
    renderModalTab('card');
  });
  $('modal-tab-score').addEventListener('click', () => {
    setModalTab('score');
    renderModalTab('score');
  });
}

/* ---------- 指数 ---------- */

async function loadIndices() {
  if (state.indices) {
    renderIndices();
    return;
  }
  $('indices-body').innerHTML = '<p class="hint">正在读取四只指数的位置与已提交序列…</p>';
  try {
    state.indices = await api('/api/indices');
    renderIndices();
  } catch (error) {
    $('indices-body').innerHTML = '<div class="error">加载失败：' + esc(error.message) + '</div>';
  }
}

function renderIndices() {
  const payload = state.indices;
  $('indices-body').innerHTML =
    payload.indices
      .map(
        (item) =>
          '<article class="index-card">' +
          '<h3>' + esc(item.name) + ' <span class="meta">' + esc(item.instrument_id) + '</span></h3>' +
          '<div class="index-grid">' +
          '<div><div class="card compact">' + renderCard(item.markdown) + '</div></div>' +
          '<figure class="chart">' + percentileChart(item.trend) + '</figure>' +
          '</div></article>'
      )
      .join('') +
    '<p class="hint">' + esc(payload.note) + '</p>';
}

function percentileChart(trend) {
  if (!trend || !trend.points || trend.points.length < 2) {
    const note = (trend && trend.note) || '没有足够的同源历史序列，未绘制分位走势。';
    return '<div class="chart-empty">' + esc(note) + '</div>';
  }
  const points = trend.points;
  const width = 640;
  const height = 240;
  const pad = { l: 42, r: 16, t: 18, b: 30 };
  const plotWidth = width - pad.l - pad.r;
  const plotHeight = height - pad.t - pad.b;
  const xAt = (index) => pad.l + (index * plotWidth) / (points.length - 1);
  const yAt = (value) => pad.t + ((100 - value) / 100) * plotHeight;
  const line = points
    .map((point, index) => (index ? 'L' : 'M') + xAt(index).toFixed(1) + ',' + yAt(point.percentile).toFixed(1))
    .join(' ');
  const grid = [0, 25, 50, 75, 100]
    .map(
      (value) =>
        '<line x1="' + pad.l + '" y1="' + yAt(value).toFixed(1) + '" x2="' + (width - pad.r) +
        '" y2="' + yAt(value).toFixed(1) + '" class="grid"/>' +
        '<text x="' + (pad.l - 8) + '" y="' + (yAt(value) + 4).toFixed(1) +
        '" class="axis" text-anchor="end">' + value + '</text>'
    )
    .join('');
  const last = points[points.length - 1];
  const first = points[0];
  return (
    '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="PE 近 5 年估值分位走势">' +
    grid +
    '<line x1="' + pad.l + '" y1="' + pad.t + '" x2="' + pad.l + '" y2="' + (height - pad.b) + '" class="axis-line"/>' +
    '<line x1="' + pad.l + '" y1="' + (height - pad.b) + '" x2="' + (width - pad.r) + '" y2="' + (height - pad.b) + '" class="axis-line"/>' +
    '<path d="' + line + '" class="trend"/>' +
    '<circle cx="' + xAt(points.length - 1).toFixed(1) + '" cy="' + yAt(last.percentile).toFixed(1) + '" r="4" class="dot"/>' +
    '<text x="' + pad.l + '" y="' + (height - 8) + '" class="axis">' + esc(first.month) + '</text>' +
    '<text x="' + (width - pad.r) + '" y="' + (height - 8) + '" class="axis" text-anchor="end">' + esc(last.month) + '</text>' +
    '<text x="' + (pad.l + 8) + '" y="' + (pad.t + 14) + '" class="chart-title">PE 近 5 年分位（当前 ' +
    Number(last.percentile).toFixed(1) + '%，' + esc(last.date) + '）</text>' +
    '</svg>' +
    '<figcaption>' + esc(trend.note) + '</figcaption>'
  );
}

/* ---------- 账本 ---------- */

async function loadLedger() {
  if (state.ledger) return;
  $('ledger-body').innerHTML = '<p class="hint">正在读取账本…</p>';
  try {
    state.ledger = await api('/api/ledger');
    $('ledger-companies').innerHTML = state.ledger.companies
      .map((company) => '<option value="' + esc(company.name) + '">' + esc(company.code) + ' · ' + company.count + ' 条</option>')
      .join('');
    renderLedger(state.ledger);
  } catch (error) {
    $('ledger-body').innerHTML = '<div class="error">加载失败：' + esc(error.message) + '</div>';
  }
  $('ledger-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const query = $('ledger-input').value.trim();
    try {
      const payload = await api('/api/ledger?q=' + encodeURIComponent(query));
      state.ledger = payload;
      renderLedger(payload);
    } catch (error) {
      $('ledger-body').innerHTML = '<div class="error">查询失败：' + esc(error.message) + '</div>';
    }
  });
}

function renderLedger(payload) {
  $('ledger-summary').textContent =
    '账本共 ' + payload.total + ' 条记录；当前显示 ' + payload.returned + ' 条。' +
    (payload.query ? '筛选：' + payload.query : '留空显示全部。') +
    ' 来源：' + payload.source;
  const body = payload.rows
    .map((row) => {
      const values = row.values || {};
      return (
        '<tr>' +
        '<td class="num">' + esc(row.analysis_date || '—') + '</td>' +
        '<td class="num">' + esc(row.code) + '</td>' +
        '<td>' + esc(row.name) + '</td>' +
        '<td><span class="badge ' + conclusionClass(row.conclusion_label) + '">' + esc(row.conclusion_label) + '</span></td>' +
        '<td class="num">' + fmtMoney(row.price) + (row.price_date ? '（' + esc(row.price_date) + '）' : '') + '</td>' +
        '<td class="num">' + fmtMoney(values.base) + '</td>' +
        '<td>' + esc(row.policy_version || '—') + '</td>' +
        '<td>' + (row.record_type === 'reversal' ? '改判' : '正式') + '</td>' +
        '<td><button class="link" data-record="' + esc(row.record_id) + '">复算</button></td>' +
        '</tr>'
      );
    })
    .join('');
  $('ledger-body').innerHTML =
    '<table class="grid"><thead><tr>' +
    '<th>分析日</th><th>代码</th><th>名称</th><th>结论</th><th>现价（日期）</th><th>基准价值</th>' +
    '<th>政策</th><th>类型</th><th>复算</th>' +
    '</tr></thead><tbody>' +
    (body || '<tr><td colspan="9">没有符合条件的记录</td></tr>') +
    '</tbody></table>';
  $('ledger-body').querySelectorAll('button[data-record]').forEach((button) => {
    button.addEventListener('click', () => runRecompute(button.dataset.record));
  });
}

async function runRecompute(recordId) {
  $('recompute-result').innerHTML = '<p class="hint">正在按记录引用的快照与政策复算…</p>';
  try {
    const payload = await postJSON('/api/recompute', { record_id: recordId });
    const labels = {
      consistent: '一致',
      inconsistent: '不一致',
      not_recomputable: '不可复算',
      not_found: '找不到记录',
    };
    const statusText = labels[payload.status] || payload.status;
    const fields = (payload.fields || [])
      .map(
        (field) =>
          '<tr><td>' + esc(field.name) + '</td><td class="num">' + esc(fmtPlain(field.recorded)) +
          '</td><td class="num">' + esc(fmtPlain(field.recomputed)) +
          '</td><td class="num">' + esc(fmtPlain(field.relative_difference)) +
          '</td><td>' + (field.ok ? '一致' : '不一致') + '</td></tr>'
      )
      .join('');
    $('recompute-result').innerHTML =
      '<div class="recompute-card">' +
      '<div>复算 ' + esc(payload.record_id || recordId) + '：' +
      '<span class="status-pill ' + esc(payload.status) + '">' + esc(statusText) + '</span></div>' +
      (payload.note ? '<p class="hint">' + esc(payload.note) + '</p>' : '') +
      (payload.message ? '<p class="hint">' + esc(payload.message) + '</p>' : '') +
      (fields
        ? '<table class="grid"><thead><tr><th>字段</th><th>记录值</th><th>复算值</th><th>相对差</th><th>结果</th></tr></thead><tbody>' +
          fields + '</tbody></table>'
        : '') +
      '</div>';
  } catch (error) {
    $('recompute-result').innerHTML = '<div class="error">复算失败：' + esc(error.message) + '</div>';
  }
}

/* ---------- 数据状态与顶部提示（H2/H6） ---------- */

function renderStatusAlert(payload) {
  const bar = $('alert-bar');
  if (!bar) return;
  const status = (payload && payload.status) || {};
  const quoteDate = status.quote_date || null;
  const stale = status.stale || null;
  if (state.quoteDate && quoteDate && state.quoteDate !== quoteDate) {
    bar.className = 'alert-bar';
    bar.innerHTML =
      '<span>数据已更新（行情 ' + esc(quoteDate) + '），点击刷新页面查看新价格与新结论。</span>' +
      '<button type="button" id="alert-refresh">刷新</button>';
    const button = $('alert-refresh');
    if (button) button.addEventListener('click', () => location.reload());
    return;
  }
  if (!state.quoteDate) state.quoteDate = quoteDate;
  if (stale && stale.message) {
    bar.className = 'alert-bar';
    bar.textContent = stale.message;
    return;
  }
  bar.className = 'alert-bar hidden';
  bar.innerHTML = '';
}

async function pollStatusAlert() {
  try {
    const payload = await api('/api/status');
    renderStatusAlert(payload);
    if (state.status && payload.status) state.status = payload;
  } catch (error) {
    /* 顶部提示失败不影响页面其它部分 */
  }
}

async function loadStatus() {
  if (state.status) {
    renderStatusAlert(state.status);
    return;
  }
  $('status-body').innerHTML = '<p class="hint">正在读取数据状态…</p>';
  try {
    state.status = await api('/api/status');
    renderStatus(state.status);
    renderStatusAlert(state.status);
  } catch (error) {
    $('status-body').innerHTML = '<div class="error">加载失败：' + esc(error.message) + '</div>';
  }
}

function renderStatus(payload) {
  const status = payload.status;
  const hstech = status.hstech;
  const rows = [
    ['行情日期', status.quote_date || '未取得（腾讯财经全市场快照）'],
  ];
  if (status.stale && status.stale.message) rows.push(['行情过期', status.stale.message]);
  rows.push(
    ['行情快照行数', String(status.quote_rows)],
    ['财务数据', status.financial],
    ['指数官方序列（沪深300）', status.index_official_series_date || '—'],
    ['官方当日估值账本', status.official_valuation_latest || '—'],
    [
      '恒生科技官方月度估值',
      hstech.date ? hstech.date + '（PE ' + hstech.pe + '，股息率 ' + hstech.dividend_yield_pct + '%）' : '—',
    ],
  );
  rows.push(
    [
      '个股政策',
      status.equity_policy.policy_id + '（' + status.equity_policy.status + '；批准 ' +
        status.equity_policy.approved_at + '；复核 ' + status.equity_policy.review_date + '）',
    ],
    ['三池规则', '批准 ' + status.pool_rules.approved_at + '；下次复核 ' + status.pool_rules.next_review_date],
    ['银行专用估值', '批准 ' + status.bank_valuation.approved_at + '；复核 ' + status.bank_valuation.review_date],
    ['上次自动更新', (status.update.run_at || '尚未运行') + '（' + (status.update.status || '—') + '）'],
  );
  const monitoring = status.monitoring || {};
  const scan = monitoring.last_scan || {};
  rows.push([
    '红线监测',
    scan.scan_date
      ? scan.scan_date + '（覆盖 ' + ((scan.counts || {}).covered || 0) + '/' +
        ((scan.counts || {}).members || 0) + '，命中 ' + ((scan.counts || {}).hit || 0) +
        '，未完成 ' + ((scan.counts || {}).incomplete || 0) + '）'
      : '尚未运行（下次每日更新自动扫描）',
  ]);
  rows.push(['已出池', String(monitoring.exclusions || 0) + ' 家']);
  const changes = (monitoring.changes || {}).items || [];
  const changeHtml = changes.length
    ? '<ul class="change-list">' + changes.map((item) =>
        '<li><span class="change-date">' + esc(item.date) + '</span>' +
        '<span class="change-type">' + esc(item.type) + '</span>' +
        '<span>' + esc(item.code ? item.code + ' ' + item.name + ' — ' : '') + esc(item.detail) + '</span></li>'
      ).join('') + '</ul>'
    : '<p class="hint">最近 365 天没有变动。</p>';
  $('status-body').innerHTML =
    '<dl class="status-grid">' +
    rows.map((row) => '<dt>' + esc(row[0]) + '</dt><dd>' + esc(row[1]) + '</dd>').join('') +
    '</dl>' +
    '<h3 class="changes-title">最近变动（最近 365 天，共 ' + changes.length + ' 条）</h3>' +
    changeHtml +
    '<p class="review-line">下一次复核日期：<strong>' + esc(payload.next_review_date || '—') + '</strong></p>' +
    '<p class="hint">每日自动更新需股东手动安装一次：<code>scripts/install-daily-update.sh</code>；' +
    '更新只追加、重复跳过、冲突不覆盖。页面不访问外网，也不改任何规则与账本旧记录。</p>';
}

/* ---------- 启动 ---------- */

function init() {
  initTabs();
  initAsk();
  initModal();
  const panel = (location.hash || '').replace('#', '');
  activatePanel(loaders[panel] ? panel : 'ask');
  const params = new URLSearchParams(location.search);
  const question = params.get('q');
  pollStatusAlert();
  window.setInterval(pollStatusAlert, 60000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) pollStatusAlert();
  });
  if (question) {
    $('ask-input').value = question;
    activatePanel('ask');
    runAsk(question, false);
  }
  const company = params.get('company');
  if (company) {
    activatePanel('pools');
    const pool = params.get('pool') || 'quality';
    state.activePool = pool;
    openCompany(company, pool);
  }
}

document.addEventListener('DOMContentLoaded', init);
