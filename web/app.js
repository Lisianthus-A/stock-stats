'use strict';

const state = {
  trades: [],
  summary: null,
  side: 'buy',
  editingId: null,
  expanded: new Set(),
  importPayload: null,
  importCount: 0,
  stockOptionsKey: null,
};

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

const num = (value, dp = 2) =>
  Number(value || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });

const signed = (value, dp = 2) => (value > 0 ? '+' : '') + num(value, dp);
const signedPct = (value) => (value > 0 ? '+' : '') + num(value, 2) + '%';
const price = (value) => num(value, Number.isInteger(+value) ? 2 : 4);

const tone = (value) => (value > 0 ? 'up' : value < 0 ? 'down' : 'flat');
const sideText = (side) => (side === 'buy' ? '买入' : '卖出');

/* 用本地时区拼日期：toISOString() 是 UTC，东八区凌晨会算成前一天 */
const todayStr = () => {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `请求失败（${response.status}）`);
    error.details = payload.errors || [];
    throw error;
  }
  return payload;
}

let toastTimer;
function toast(message, kind = '') {
  const node = $('toast');
  node.textContent = message;
  node.className = 'toast' + (kind ? ' ' + kind : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add('hidden'), 3200);
}

async function load() {
  try {
    const [tradePayload, summary] = await Promise.all([
      api('/api/trades'),
      api('/api/summary'),
    ]);
    state.trades = tradePayload.trades;
    state.summary = summary;
    render();
  } catch (error) {
    toast('加载失败：' + error.message, 'error');
  }
}

/* ---------- render ---------- */

function render() {
  fillStockOptions();
  renderHeader();
  renderOverview();
  renderPositions();
  renderTrades();
  renderClosed();
}

function renderHeader() {
  const { totals, generated_at: generatedAt } = state.summary;
  $('asOfText').textContent =
    `统计基准日 ${state.summary.as_of} ｜ 已平仓 ${totals.closed_count} 笔 ｜ ` +
    `持仓 ${totals.position_count} 只 ｜ 更新于 ${generatedAt}`;

  const warnings = state.summary.warnings || [];
  const bar = $('warningBar');
  bar.classList.toggle('hidden', warnings.length === 0);
  if (warnings.length) {
    bar.innerHTML = warnings.map((w) => `<div>⚠ ${esc(w)}</div>`).join('');
  }
}

function renderOverview() {
  const { totals, stats, per_stock: perStock } = state.summary;

  $('heroPct').textContent = totals.closed_count ? signedPct(totals.overall_pct) : '—';
  $('heroPct').className = tone(totals.overall_pct);

  const cumulative = stats.cumulative_return;
  $('heroCumulative').textContent = totals.closed_count ? signedPct(cumulative) : '—';
  $('heroCumulative').className = 'hero-value ' + tone(cumulative);
  $('heroCumulative').title = totals.closed_count
    ? `每笔平仓收益率连乘：${stats.cumulative_factor} 倍`
    : '暂无已平仓交易';
  $('heroProfit').textContent = totals.closed_count ? signed(totals.total_profit) : '—';
  $('heroProfit').className = tone(totals.total_profit);
  $('heroCost').textContent = totals.closed_count ? num(totals.total_cost) : '—';

  const metrics = [
    ['已平仓笔数', String(totals.closed_count)],
    ['胜率', stats.win_count ? num(stats.win_rate, 2) + '%' : '—'],
    ['盈利 / 亏损笔数', `${stats.win_count} / ${stats.loss_count}`],
    ['盈亏比', stats.profit_factor === null ? '—' : num(stats.profit_factor, 2)],
    ['平均每笔盈亏', signed(stats.avg_profit), tone(stats.avg_profit)],
    ['平均每笔盈亏率', totals.closed_count ? signedPct(stats.avg_pct) : '—', tone(stats.avg_pct)],
    ['平均持有时间', totals.closed_count ? stats.avg_hold_text : '—'],
    ['持仓股票数', String(totals.position_count)],
    ['总交易笔数', String(totals.trade_count)],
  ];
  $('metricGrid').innerHTML = metrics
    .map(([label, value, cls]) =>
      `<div class="metric"><span>${esc(label)}</span><strong class="${cls || ''}">${esc(value)}</strong></div>`)
    .join('');

  renderEdge('bestTrade', stats.best);
  renderEdge('worstTrade', stats.worst);

  $('perStockBody').innerHTML = perStock.length
    ? perStock.map((row) => `
      <tr>
        <td class="mono">${esc(row.code)}</td>
        <td>${esc(row.name)}</td>
        <td class="num">${row.closed_count}</td>
        <td class="num ${tone(row.realized_profit)}">${signed(row.realized_profit)}</td>
        <td class="num ${tone(row.realized_profit)}">${signedPct(row.realized_pct)}</td>
        <td class="num">${row.avg_hold_days} 天</td>
      </tr>`).join('')
    : '<tr><td colspan="6" class="empty">暂无已平仓交易</td></tr>';
}

function renderEdge(id, pair) {
  const node = $(id);
  if (!pair) {
    node.textContent = '暂无数据';
    return;
  }
  node.innerHTML = `
    <span class="edge-profit ${tone(pair.profit)}">${signed(pair.profit)}（${signedPct(pair.pct)}）</span>
    <b>${esc(pair.code)} ${esc(pair.name)}</b><br>
    买入 ${pair.buy_date} @ ${price(pair.buy_price)} →
    卖出 ${pair.sell_date} @ ${price(pair.sell_price)}<br>
    持有 ${pair.hold_days} 天（${esc(pair.hold_text)}）`;
}

function renderPositions() {
  const positions = state.summary.positions;
  $('positionCount').textContent = String(positions.length);
  $('positionEmpty').classList.toggle('hidden', positions.length > 0);

  $('positionBody').innerHTML = positions.map((item) => {
    const open = state.expanded.has(item.code);
    return `
      <tr>
        <td class="mono">${esc(item.code)}</td>
        <td>${esc(item.name)}</td>
        <td class="num">${price(item.cost_price)}</td>
        <td>${item.first_buy_date}</td>
        <td class="num">${item.holding_text}（${item.holding_days} 天）</td>
        <td class="num">${item.open_count}</td>
        <td class="num">${item.closed_count}</td>
        <td class="num ${tone(item.realized_profit)}">${signed(item.realized_profit)}</td>
        <td><button class="link-btn" data-toggle="${esc(item.code)}">${open ? '收起' : '明细'}</button></td>
      </tr>
      ${open ? detailRow(item) : ''}`;
  }).join('');
}

function detailRow(position) {
  const lots = position.lots.map((lot) => `
    <tr>
      <td>${lot.buy_date}</td>
      <td class="num">${price(lot.buy_price)}</td>
      <td class="num">${lot.holding_text}（${lot.holding_days} 天）</td>
      <td class="note-cell">${esc(lot.note || '—')}</td>
    </tr>`).join('');

  const history = position.trades.map((t) => `
    <tr>
      <td>${t.trade_date}</td>
      <td><span class="badge badge-${t.side}">${sideText(t.side)}</span></td>
      <td class="num">${price(t.price)}</td>
      <td class="note-cell">${esc(t.note || '—')}</td>
    </tr>`).join('');

  return `
    <tr class="detail-row"><td colspan="9"><div class="detail-inner">
      <h4>未平仓买入（已持股时间按每笔买入日分别计算）</h4>
      <table><thead><tr><th>买入日</th><th class="num">买入价</th><th class="num">已持股时间</th><th>备注</th></tr></thead>
      <tbody>${lots}</tbody></table>
      <h4 style="margin-top:14px">该股全部交易</h4>
      <table><thead><tr><th>日期</th><th>类型</th><th class="num">价格</th><th>备注</th></tr></thead>
      <tbody>${history}</tbody></table>
    </div></td></tr>`;
}

function renderTrades() {
  $('tradeCount').textContent = String(state.trades.length);
  const keyword = $('tradeSearch').value.trim().toLowerCase();
  const sideFilter = $('tradeSideFilter').value;

  const rows = state.trades.filter((t) => {
    if (sideFilter && t.side !== sideFilter) return false;
    if (!keyword) return true;
    return [t.code, t.name, t.note].some((f) => String(f).toLowerCase().includes(keyword));
  });

  $('tradeEmpty').classList.toggle('hidden', rows.length > 0);
  $('tradeEmpty').textContent = state.trades.length
    ? '没有符合筛选条件的交易'
    : '还没有交易记录，先在上方录入一笔';

  $('tradeBody').innerHTML = rows.map((t) => `
    <tr>
      <td class="mono">${esc(t.trade_date)}</td>
      <td><span class="badge badge-${t.side}">${sideText(t.side)}</span></td>
      <td class="mono">${esc(t.code)}</td>
      <td>${esc(t.name)}</td>
      <td class="num">${price(t.price)}</td>
      <td class="note-cell" title="${esc(t.note)}">${esc(t.note || '—')}</td>
      <td>
        <div class="row-actions">
          <button class="link-btn" data-edit="${t.id}">编辑</button>
          <button class="link-btn danger" data-delete="${t.id}">删除</button>
        </div>
      </td>
    </tr>`).join('');
}

/** 录入框的代码联想与「已平仓」筛选项都来自同一份 stock_names，股票有变化时才重建 */
function fillStockOptions() {
  const names = state.summary.stock_names;
  const codes = Object.keys(names).sort();
  const key = codes.join(' ');
  if (key === state.stockOptionsKey) return;
  state.stockOptionsKey = key;

  $('codeList').innerHTML = codes
    .map((code) => `<option value="${esc(code)}">${esc(names[code])}</option>`)
    .join('');

  const select = $('closedCodeFilter');
  const current = select.value;
  select.innerHTML = '<option value="">全部股票</option>' +
    codes.map((code) =>
      `<option value="${esc(code)}">${esc(code)} ${esc(names[code])}</option>`).join('');
  select.value = codes.includes(current) ? current : '';
}

function renderClosed() {
  const pairs = state.summary.closed_pairs;
  const codeFilter = $('closedCodeFilter').value;
  const resultFilter = $('closedResultFilter').value;
  const keyword = $('closedSearch').value.trim().toLowerCase();

  const rows = pairs.filter((p) => {
    if (codeFilter && p.code !== codeFilter) return false;
    if (resultFilter && p.result !== resultFilter) return false;
    if (!keyword) return true;
    return [p.name, p.buy_note, p.sell_note].some((f) => String(f).toLowerCase().includes(keyword));
  });

  $('closedCount').textContent = String(pairs.length);
  $('closedEmpty').classList.toggle('hidden', rows.length > 0);
  $('closedEmpty').textContent = pairs.length
    ? '没有符合筛选条件的已平仓交易'
    : '暂无已平仓交易（需至少一笔买入和一笔卖出）';

  const profit = rows.reduce((sum, p) => sum + p.profit, 0);
  const cost = rows.reduce((sum, p) => sum + p.buy_price, 0);
  const pct = cost ? (profit / cost) * 100 : 0;
  $('closedSummary').innerHTML = rows.length
    ? `筛选出 <b>${rows.length}</b> 笔 ｜ 小计盈亏 <b class="${tone(profit)}">${signed(profit)}</b>
       ｜ 小计盈亏率 <b class="${tone(profit)}">${signedPct(pct)}</b>`
    : '';

  $('closedBody').innerHTML = rows.map((p) => `
    <tr>
      <td class="mono">${esc(p.code)}</td>
      <td>${esc(p.name)}</td>
      <td class="mono">${p.buy_date}</td>
      <td class="num">${price(p.buy_price)}</td>
      <td class="mono">${p.sell_date}</td>
      <td class="num">${price(p.sell_price)}</td>
      <td class="num ${tone(p.profit)}">${signed(p.profit)}</td>
      <td class="num ${tone(p.profit)}">${signedPct(p.pct)}</td>
      <td class="num">${p.hold_days} 天</td>
    </tr>`).join('');
}

/* ---------- form ---------- */

function setSide(side) {
  state.side = side;
  $('sideInput').value = side;
  document.querySelectorAll('.side-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.side === side);
  });
  const verb = side === 'buy' ? '买入' : '卖出';
  $('priceLabel').textContent = verb + '价格';
  $('priceInput').placeholder = verb + '价格';
  if (!state.editingId) {
    $('formTitle').textContent = '录入' + verb;
    $('submitBtn').textContent = '保存' + verb;
  }
}

function resetForm() {
  state.editingId = null;
  $('tradeForm').reset();
  $('nameInput').dataset.touched = '';
  $('dateInput').value = todayStr();
  $('formError').classList.add('hidden');
  $('cancelEditBtn').hidden = true;
  setSide(state.side);
}

function showFormError(message) {
  const node = $('formError');
  node.textContent = message;
  node.classList.remove('hidden');
}

function startEdit(id) {
  const trade = state.trades.find((t) => t.id === id);
  if (!trade) return;
  state.editingId = id;
  setSide(trade.side);
  $('codeInput').value = trade.code;
  $('nameInput').value = trade.name;
  $('dateInput').value = trade.trade_date;
  $('priceInput').value = trade.price;
  $('noteInput').value = trade.note;
  $('formTitle').textContent = `编辑交易 #${id}`;
  $('submitBtn').textContent = '保存修改';
  $('cancelEditBtn').hidden = false;
  $('formError').classList.add('hidden');
  $('tradeForm').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function deleteTrade(id) {
  if (!confirm('确定删除这条交易记录吗？删除后统计会立即重算。')) return;
  try {
    await api(`/api/trades/${id}`, { method: 'DELETE' });
    if (state.editingId === id) resetForm();
    toast('已删除');
    await load();
  } catch (error) {
    toast('删除失败：' + error.message, 'error');
  }
}

function localDateWarning(code, side, dateValue) {
  if (side !== 'sell' || !code || !dateValue) return null;
  const buys = state.trades
    .filter((t) => t.code === code && t.side === 'buy')
    .map((t) => t.trade_date)
    .sort();
  if (!buys.length) {
    return `「${code}」尚无买入记录，该笔卖出将无法配对，也不会计入盈亏统计。仍要保存吗？`;
  }
  if (dateValue < buys[0]) {
    return `「${code}」最早买入日期为 ${buys[0]}，该笔卖出无法配对，也不会计入盈亏统计。仍要保存吗？`;
  }
  return null;
}

/* ---------- 导入 / 导出 ---------- */

const importRows = (payload) => {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === 'object') {
    return [payload.trades, payload.records, payload.data].find(Array.isArray) || [];
  }
  return [];
};

const pickField = (row, keys) => {
  for (const key of keys) {
    const value = row?.[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return '';
};

function closeImportPanel() {
  state.importPayload = null;
  state.importCount = 0;
  $('importFile').value = '';
  showImportErrors([]);
  $('ioPanel').classList.add('hidden');
}

function showImportErrors(messages) {
  const list = $('ioErrors');
  list.innerHTML = messages.map((m) => `<li>${esc(m)}</li>`).join('');
  list.classList.toggle('hidden', !messages.length);
}

function showImportPreview(filename, payload) {
  const rows = importRows(payload);
  state.importPayload = payload;
  state.importCount = rows.length;

  if (!rows.length) {
    $('ioSummary').innerHTML =
      `文件 <b>${esc(filename)}</b> 里没找到交易记录数组（需要形如 <code>{"trades": [...]}</code>）。`;
    $('ioPreviewBody').innerHTML = '';
    $('ioAppendBtn').disabled = true;
    $('ioReplaceBtn').disabled = true;
    showImportErrors([]);
    $('ioPanel').classList.remove('hidden');
    return;
  }

  $('ioAppendBtn').disabled = false;
  $('ioReplaceBtn').disabled = false;

  const dates = rows
    .map((row) => pickField(row, ['trade_date', 'date', '日期']))
    .filter(Boolean)
    .map(String)
    .sort();
  const span = dates.length ? `${dates[0]} ~ ${dates[dates.length - 1]}` : '日期缺失';
  $('ioSummary').innerHTML =
    `文件 <b>${esc(filename)}</b> 解析到 <b>${rows.length}</b> 条记录 ｜ 现有 ${state.trades.length} 条 ｜ 日期 ${esc(span)}`;

  const preview = rows.slice(0, 5).map((row) => `
    <tr>
      <td class="mono">${esc(pickField(row, ['trade_date', 'date', '日期']))}</td>
      <td>${esc(pickField(row, ['side', 'type', '类型']))}</td>
      <td class="mono">${esc(pickField(row, ['code', '代码']))}</td>
      <td>${esc(pickField(row, ['name', '名称']))}</td>
      <td class="num">${esc(pickField(row, ['price', '价格']))}</td>
      <td class="note-cell">${esc(pickField(row, ['note', '备注']) || '—')}</td>
    </tr>`).join('');

  $('ioPreviewBody').innerHTML = rows.length > 5
    ? preview + `<tr><td colspan="6" class="empty">… 其余 ${rows.length - 5} 条</td></tr>`
    : preview;

  showImportErrors([]);
  $('ioPanel').classList.remove('hidden');
}

async function runImport(mode) {
  if (!state.importPayload) return;
  const count = state.importCount;
  if (mode === 'replace') {
    const ok = confirm(
      `覆盖导入会先删除数据库中现有的全部 ${state.trades.length} 条交易记录，` +
      `再写入文件里的 ${count} 条。此操作不可撤销，确定继续吗？`
    );
    if (!ok) return;
  }
  try {
    const result = await api('/api/import', {
      method: 'POST',
      body: JSON.stringify({ mode, payload: state.importPayload }),
    });
    closeImportPanel();
    resetForm();
    const label = mode === 'replace' ? '覆盖' : '追加';
    const warnings = result.warnings || [];
    const messages = [`已导入 ${result.imported} 条记录（${label}），现有 ${result.total} 条`, ...warnings];
    toast(messages.join('；'), warnings.length ? 'warn' : '');
    await load();
  } catch (error) {
    const details = error.details || [];
    if (details.length) {
      showImportErrors(details);
      $('ioPanel').classList.remove('hidden');
    }
    toast('导入失败：' + error.message, 'error');
  }
}

/* ---------- events ---------- */

function bindEvents() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
      document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
      tab.classList.add('active');
      $('view-' + tab.dataset.view).classList.add('active');
    });
  });

  document.querySelectorAll('.side-btn').forEach((btn) => {
    btn.addEventListener('click', () => setSide(btn.dataset.side));
  });

  $('refreshBtn').addEventListener('click', load);
  $('cancelEditBtn').addEventListener('click', resetForm);

  $('importBtn').addEventListener('click', () => $('importFile').click());
  $('importFile').addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      showImportPreview(file.name, JSON.parse(await file.text()));
    } catch (error) {
      state.importPayload = null;
      state.importCount = 0;
      showImportErrors(['文件不是合法的 JSON：' + error.message]);
      $('ioSummary').innerHTML = `文件 <b>${esc(file.name)}</b> 解析失败。`;
      $('ioPreviewBody').innerHTML = '';
      $('ioAppendBtn').disabled = true;
      $('ioReplaceBtn').disabled = true;
      $('ioPanel').classList.remove('hidden');
    }
  });
  $('ioAppendBtn').addEventListener('click', () => runImport('append'));
  $('ioReplaceBtn').addEventListener('click', () => runImport('replace'));
  $('ioCancelBtn').addEventListener('click', closeImportPanel);

  $('codeInput').addEventListener('input', () => {
    const known = state.summary?.stock_names?.[$('codeInput').value.trim()];
    if (known && !$('nameInput').dataset.touched) $('nameInput').value = known;
  });
  $('nameInput').addEventListener('input', () => {
    $('nameInput').dataset.touched = $('nameInput').value.trim() ? '1' : '';
  });

  $('tradeForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = {
      code: $('codeInput').value.trim(),
      name: $('nameInput').value.trim(),
      side: $('sideInput').value,
      trade_date: $('dateInput').value,
      price: $('priceInput').value,
      note: $('noteInput').value.trim(),
    };

    const warning = localDateWarning(payload.code, payload.side, payload.trade_date);
    if (warning && !confirm(warning)) return;

    const editing = state.editingId;
    try {
      const result = editing
        ? await api(`/api/trades/${editing}`, { method: 'PUT', body: JSON.stringify(payload) })
        : await api('/api/trades', { method: 'POST', body: JSON.stringify(payload) });
      resetForm();
      const saved = editing ? '已保存修改' : `已录入${sideText(payload.side)}记录`;
      toast(result.warning ? `${saved}（${result.warning}）` : saved, result.warning ? 'warn' : '');
      await load();
    } catch (error) {
      showFormError(error.message);
    }
  });

  $('positionBody').addEventListener('click', (event) => {
    const code = event.target.dataset?.toggle;
    if (!code) return;
    if (state.expanded.has(code)) state.expanded.delete(code);
    else state.expanded.add(code);
    renderPositions();
  });

  $('tradeBody').addEventListener('click', (event) => {
    const editId = event.target.dataset?.edit;
    if (editId) return startEdit(+editId);
    const deleteId = event.target.dataset?.delete;
    if (deleteId) deleteTrade(+deleteId);
  });

  ['tradeSearch', 'tradeSideFilter'].forEach((id) => {
    $(id).addEventListener('input', renderTrades);
  });
  ['closedCodeFilter', 'closedResultFilter', 'closedSearch'].forEach((id) => {
    $(id).addEventListener('input', renderClosed);
  });
}

bindEvents();
setSide('buy');
$('dateInput').value = todayStr();
load();
