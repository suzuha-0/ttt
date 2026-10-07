'use strict';

// ---------- データ ----------
const STORAGE_KEY = 'kabu-note-v1';

function emptyState() {
  return { trades: [], dividends: [], prices: {}, names: {} };
}

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyState();
    return { ...emptyState(), ...JSON.parse(raw) };
  } catch (e) {
    console.error(e);
    return emptyState();
  }
}

let state = load();

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

// ---------- 表示ユーティリティ ----------
const yen = new Intl.NumberFormat('ja-JP', { maximumFractionDigits: 0 });
const dec = new Intl.NumberFormat('ja-JP', { maximumFractionDigits: 2 });

function fmtYen(n) { return '¥' + yen.format(Math.round(n)); }
function fmtSigned(n) { return (n > 0 ? '+' : n < 0 ? '−' : '') + '¥' + yen.format(Math.abs(Math.round(n))); }
function fmtPct(n) { return (n > 0 ? '+' : '') + dec.format(n * 100) + '%'; }
function signClass(n) { return n > 0 ? 'plus' : n < 0 ? 'minus' : ''; }

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const $ = sel => document.querySelector(sel);

function nameOf(code) { return state.names[code] || ''; }

// ---------- 計算（移動平均法） ----------
// 売買を日付順に処理し、銘柄ごとの保有と各売りの実現損益を求める。
function compute() {
  const sorted = state.trades
    .map((t, i) => ({ t, i }))
    .sort((a, b) => a.t.date.localeCompare(b.t.date) || a.i - b.i)
    .map(x => x.t);

  const pos = {};          // code -> { qty, cost }
  const realizedById = {}; // trade id -> 実現損益
  const errors = {};       // trade id -> エラーメッセージ

  for (const t of sorted) {
    const p = pos[t.code] || (pos[t.code] = { qty: 0, cost: 0 });
    if (t.side === 'buy') {
      p.qty += t.qty;
      p.cost += t.qty * t.price + t.fee;
    } else {
      if (t.qty > p.qty + 1e-9) {
        errors[t.id] = '保有数を超える売り';
      }
      const sellQty = Math.min(t.qty, p.qty);
      const avg = p.qty > 0 ? p.cost / p.qty : 0;
      const costOut = avg * sellQty;
      realizedById[t.id] = t.qty * t.price - t.fee - costOut;
      p.qty -= sellQty;
      p.cost -= costOut;
      if (p.qty < 1e-9) { p.qty = 0; p.cost = 0; }
    }
  }

  const holdings = Object.entries(pos)
    .filter(([, p]) => p.qty > 0)
    .map(([code, p]) => {
      const price = state.prices[code];
      const hasPrice = typeof price === 'number' && !isNaN(price);
      const market = hasPrice ? p.qty * price : p.cost;
      return {
        code,
        name: nameOf(code),
        qty: p.qty,
        cost: p.cost,
        avg: p.cost / p.qty,
        price: hasPrice ? price : null,
        market,
        unrealized: market - p.cost,
      };
    })
    .sort((a, b) => b.market - a.market);

  return { holdings, realizedById, errors };
}

// ---------- 描画 ----------
const PALETTE = ['#2563eb', '#f59e0b', '#10b981', '#8b5cf6', '#ef4444', '#06b6d4', '#ec4899', '#84cc16', '#64748b'];

function render() {
  const { holdings, realizedById, errors } = compute();
  renderDashboard(holdings, realizedById);
  renderTrades(realizedById, errors);
  renderDividends();
  renderYearly(realizedById);
  renderCodeList();
}

function renderDashboard(holdings, realizedById) {
  const market = holdings.reduce((s, h) => s + h.market, 0);
  const cost = holdings.reduce((s, h) => s + h.cost, 0);
  const unrealized = market - cost;
  const realized = Object.values(realizedById).reduce((s, v) => s + v, 0);
  const dividend = state.dividends.reduce((s, d) => s + d.amount, 0);
  const total = unrealized + realized + dividend;

  $('#sum-market').textContent = fmtYen(market);
  $('#sum-cost').textContent = fmtYen(cost);
  setSigned('#sum-unrealized', unrealized, cost ? ` (${fmtPct(unrealized / cost)})` : '');
  setSigned('#sum-realized', realized);
  setSigned('#sum-dividend', dividend);
  setSigned('#sum-total', total);

  const body = $('#holdings-body');
  if (!holdings.length) {
    body.innerHTML = '<tr><td colspan="10" class="empty">保有銘柄はありません。「売買履歴」から買いを記録してください。</td></tr>';
    $('#alloc').innerHTML = '';
    return;
  }

  body.innerHTML = holdings.map((h, i) => {
    const color = PALETTE[i % PALETTE.length];
    const ratio = market ? h.market / market : 0;
    return `<tr>
      <td><span class="swatch" style="background:${color}"></span>${esc(h.code)}</td>
      <td>${esc(h.name)}</td>
      <td class="num">${dec.format(h.qty)}</td>
      <td class="num">${dec.format(h.avg)}</td>
      <td class="num">${fmtYen(h.cost)}</td>
      <td class="num"><input class="price" type="number" step="any" min="0" data-code="${esc(h.code)}" value="${h.price ?? ''}" placeholder="未入力"></td>
      <td class="num">${fmtYen(h.market)}</td>
      <td class="num ${signClass(h.unrealized)}">${h.price === null ? '-' : fmtSigned(h.unrealized)}</td>
      <td class="num ${signClass(h.unrealized)}">${h.price === null ? '-' : fmtPct(h.unrealized / h.cost)}</td>
      <td class="num">${dec.format(ratio * 100)}%</td>
    </tr>`;
  }).join('');

  $('#alloc').innerHTML = holdings.map((h, i) =>
    `<span title="${esc(h.code)} ${esc(h.name)}" style="width:${market ? (h.market / market) * 100 : 0}%;background:${PALETTE[i % PALETTE.length]}"></span>`
  ).join('');
}

function setSigned(sel, n, suffix = '') {
  const el = $(sel);
  el.textContent = fmtSigned(n) + suffix;
  el.className = 'value ' + signClass(n);
}

function renderTrades(realizedById, errors) {
  const q = $('#trade-filter').value.trim().toLowerCase();
  const rows = state.trades
    .map((t, i) => ({ t, i }))
    .sort((a, b) => b.t.date.localeCompare(a.t.date) || b.i - a.i)
    .map(x => x.t)
    .filter(t => !q || t.code.toLowerCase().includes(q) || nameOf(t.code).toLowerCase().includes(q));

  const body = $('#trades-body');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="11" class="empty">記録がありません</td></tr>';
    return;
  }
  body.innerHTML = rows.map(t => {
    const gross = t.qty * t.price;
    const settle = t.side === 'buy' ? gross + t.fee : gross - t.fee;
    const r = realizedById[t.id];
    const err = errors[t.id];
    return `<tr>
      <td>${esc(t.date)}</td>
      <td class="${t.side === 'buy' ? 'side-buy' : 'side-sell'}">${t.side === 'buy' ? '買' : '売'}</td>
      <td>${esc(t.code)}</td>
      <td>${esc(nameOf(t.code))}</td>
      <td class="num">${dec.format(t.qty)}</td>
      <td class="num">${dec.format(t.price)}</td>
      <td class="num">${fmtYen(t.fee)}</td>
      <td class="num">${fmtYen(settle)}</td>
      <td class="num ${r === undefined ? '' : signClass(r)}">${r === undefined ? '' : fmtSigned(r)}${err ? ` <span class="minus" title="${esc(err)}">⚠</span>` : ''}</td>
      <td class="memo">${esc(t.memo)}</td>
      <td><button class="link" data-edit-trade="${t.id}">編集</button><button class="link del" data-del-trade="${t.id}">削除</button></td>
    </tr>`;
  }).join('');
}

function renderDividends() {
  const rows = [...state.dividends].sort((a, b) => b.date.localeCompare(a.date));
  const body = $('#dividends-body');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">記録がありません</td></tr>';
    return;
  }
  body.innerHTML = rows.map(d => `<tr>
    <td>${esc(d.date)}</td>
    <td>${esc(d.code)}</td>
    <td>${esc(nameOf(d.code))}</td>
    <td class="num">${fmtYen(d.amount)}</td>
    <td class="memo">${esc(d.memo)}</td>
    <td><button class="link" data-edit-div="${d.id}">編集</button><button class="link del" data-del-div="${d.id}">削除</button></td>
  </tr>`).join('');
}

function renderYearly(realizedById) {
  const years = {};
  const y = date => (years[date.slice(0, 4)] ||= { realized: 0, dividend: 0, count: 0 });
  for (const t of state.trades) {
    const row = y(t.date);
    row.count++;
    if (t.id in realizedById) row.realized += realizedById[t.id];
  }
  for (const d of state.dividends) y(d.date).dividend += d.amount;

  const keys = Object.keys(years).sort().reverse();
  const body = $('#yearly-body');
  if (!keys.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty">記録がありません</td></tr>';
    return;
  }
  body.innerHTML = keys.map(k => {
    const r = years[k];
    const sum = r.realized + r.dividend;
    return `<tr>
      <td>${k}年</td>
      <td class="num ${signClass(r.realized)}">${fmtSigned(r.realized)}</td>
      <td class="num">${fmtYen(r.dividend)}</td>
      <td class="num ${signClass(sum)}">${fmtSigned(sum)}</td>
      <td class="num">${r.count}</td>
    </tr>`;
  }).join('');
}

function renderCodeList() {
  const codes = new Set([...state.trades.map(t => t.code), ...Object.keys(state.names)]);
  $('#code-list').innerHTML = [...codes].sort()
    .map(c => `<option value="${esc(c)}">${esc(nameOf(c))}</option>`).join('');
}

// ---------- タブ ----------
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + btn.dataset.tab));
  });
});

// ---------- 現在値の入力 ----------
$('#holdings-body').addEventListener('change', e => {
  const input = e.target.closest('input.price');
  if (!input) return;
  const v = parseFloat(input.value);
  if (isNaN(v)) delete state.prices[input.dataset.code];
  else state.prices[input.dataset.code] = v;
  save();
  render();
});

// ---------- 売買フォーム ----------
const tradeForm = $('#trade-form');

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function resetTradeForm() {
  tradeForm.reset();
  tradeForm.id.value = '';
  tradeForm.date.value = today();
  $('#trade-form-title').textContent = '売買を記録';
  $('#trade-cancel').hidden = true;
}

// コード入力時に既知の銘柄名を補完
tradeForm.code.addEventListener('change', () => {
  const n = nameOf(tradeForm.code.value.trim());
  if (n && !tradeForm.name.value) tradeForm.name.value = n;
});

tradeForm.addEventListener('submit', e => {
  e.preventDefault();
  const f = tradeForm;
  const code = f.code.value.trim();
  const trade = {
    id: f.id.value || uid(),
    date: f.date.value,
    side: f.side.value,
    code,
    qty: parseFloat(f.qty.value),
    price: parseFloat(f.price.value),
    fee: parseFloat(f.fee.value) || 0,
    memo: f.memo.value.trim(),
  };
  const name = f.name.value.trim();
  if (name) state.names[code] = name;

  const idx = state.trades.findIndex(t => t.id === trade.id);
  if (idx >= 0) state.trades[idx] = trade;
  else state.trades.push(trade);

  save();
  resetTradeForm();
  render();
});

$('#trade-cancel').addEventListener('click', resetTradeForm);

$('#trades-body').addEventListener('click', e => {
  const editId = e.target.dataset.editTrade;
  const delId = e.target.dataset.delTrade;
  if (editId) {
    const t = state.trades.find(x => x.id === editId);
    const f = tradeForm;
    f.id.value = t.id;
    f.date.value = t.date;
    f.side.value = t.side;
    f.code.value = t.code;
    f.name.value = nameOf(t.code);
    f.qty.value = t.qty;
    f.price.value = t.price;
    f.fee.value = t.fee;
    f.memo.value = t.memo;
    $('#trade-form-title').textContent = '売買を編集';
    $('#trade-cancel').hidden = false;
    f.scrollIntoView({ behavior: 'smooth' });
  } else if (delId && confirm('この売買記録を削除しますか？')) {
    state.trades = state.trades.filter(t => t.id !== delId);
    save();
    render();
  }
});

$('#trade-filter').addEventListener('input', () => render());

// ---------- 配当フォーム ----------
const divForm = $('#div-form');

function resetDivForm() {
  divForm.reset();
  divForm.id.value = '';
  divForm.date.value = today();
  $('#div-form-title').textContent = '配当を記録';
  $('#div-cancel').hidden = true;
}

divForm.addEventListener('submit', e => {
  e.preventDefault();
  const f = divForm;
  const div = {
    id: f.id.value || uid(),
    date: f.date.value,
    code: f.code.value.trim(),
    amount: parseFloat(f.amount.value),
    memo: f.memo.value.trim(),
  };
  const idx = state.dividends.findIndex(d => d.id === div.id);
  if (idx >= 0) state.dividends[idx] = div;
  else state.dividends.push(div);
  save();
  resetDivForm();
  render();
});

$('#div-cancel').addEventListener('click', resetDivForm);

$('#dividends-body').addEventListener('click', e => {
  const editId = e.target.dataset.editDiv;
  const delId = e.target.dataset.delDiv;
  if (editId) {
    const d = state.dividends.find(x => x.id === editId);
    divForm.id.value = d.id;
    divForm.date.value = d.date;
    divForm.code.value = d.code;
    divForm.amount.value = d.amount;
    divForm.memo.value = d.memo;
    $('#div-form-title').textContent = '配当を編集';
    $('#div-cancel').hidden = false;
  } else if (delId && confirm('この配当記録を削除しますか？')) {
    state.dividends = state.dividends.filter(d => d.id !== delId);
    save();
    render();
  }
});

// ---------- エクスポート / インポート ----------
function download(filename, content, type) {
  const blob = new Blob([content], { type });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

$('#export-json').addEventListener('click', () => {
  download(`kabu-note-${today()}.json`, JSON.stringify(state, null, 2), 'application/json');
});

$('#import-json').addEventListener('change', async e => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (!Array.isArray(data.trades) || !Array.isArray(data.dividends)) throw new Error('形式が正しくありません');
    if (!confirm('現在のデータをインポートしたデータで置き換えます。よろしいですか？')) return;
    state = { ...emptyState(), ...data };
    save();
    render();
    alert('インポートしました');
  } catch (err) {
    alert('インポートに失敗しました: ' + err.message);
  } finally {
    e.target.value = '';
  }
});

$('#export-csv').addEventListener('click', () => {
  const { realizedById } = compute();
  const header = ['日付', '売買', 'コード', '銘柄名', '株数', '単価', '手数料', '実現損益', 'メモ'];
  const cell = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const lines = [...state.trades]
    .sort((a, b) => a.date.localeCompare(b.date))
    .map(t => [t.date, t.side === 'buy' ? '買' : '売', t.code, nameOf(t.code), t.qty, t.price, t.fee,
      t.id in realizedById ? Math.round(realizedById[t.id]) : '', t.memo].map(cell).join(','));
  // Excel で文字化けしないよう BOM を付ける
  download(`kabu-trades-${today()}.csv`, '﻿' + [header.map(cell).join(','), ...lines].join('\r\n'), 'text/csv');
});

$('#reset-all').addEventListener('click', () => {
  if (!confirm('全データを削除します。元に戻せません。よろしいですか？')) return;
  state = emptyState();
  save();
  render();
});

// ---------- 初期化 ----------
resetTradeForm();
resetDivForm();
render();
