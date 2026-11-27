import { api } from './api.js';
import { compactMoney, date, escape, icon, money, number, reveal, setAppDate, today } from './ui.js';

const modes = {
  income: { name: 'Доходы от имущества', icon: 'income', trend: 'Динамика доходов', structure: 'Структура доходов', color: '#f0cb75', financial: true },
  debt: { name: 'Задолженность', icon: 'debt', trend: 'Динамика задолженности', structure: 'Структура задолженности', color: '#f09093', financial: true },
  properties: { name: 'Объектов в реестре', icon: 'building', trend: 'Новые объекты по месяцам', structure: 'Состав реестра', color: '#8caedd' },
  renewals: { name: 'Договоров на продление', icon: 'calendar', trend: 'Договоры по месяцам окончания', structure: 'Оставшиеся сроки', color: '#9cc8ac' },
};
const colors = ['#eac16c', '#7eb896', '#709fd5', '#b195ce'];
const percent = new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 });

let selected = 'income';
let financialPeriod = 'last12';
let financialYear = Number(today().slice(0, 4));
let cachedDashboard;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function dashboardParams() {
  return { period: financialPeriod, year: financialPeriod === 'year' ? financialYear : null };
}

export function mountDashboard(root) {
  root.innerHTML = `
    <header class="page-header">
      <div><h1>Дашборд имущества</h1><p class="subheading">Поступления, реестр и сроки договоров</p></div>
      <form class="period-controls">
        <label><span>Финансовый период</span><select name="period">
          <option value="last12">Последние 12 месяцев</option><option value="year">Календарный год</option><option value="all">Всё время</option>
        </select></label>
        <label class="year-control" hidden><span>Год</span><input name="year" type="number" step="1" min="1" value="${financialYear}" max="${today().slice(0, 4)}" required></label>
      </form>
    </header>
    <div class="dashboard-data is-pending">
    <div class="kpi-grid" role="group" aria-label="Показатели дашборда">
      ${Object.entries(modes).map(([key, mode]) => `
        <button type="button" class="kpi" data-mode="${key}" aria-pressed="${selected === key}" aria-controls="analytics">
          <span class="kpi-icon">${icon(mode.icon)}</span>
          <span class="kpi-value">—</span>
          <span class="kpi-name">${mode.name}</span>
          <span class="kpi-context"></span>
        </button>`).join('')}
    </div>
    <div class="live-message" aria-live="polite" hidden></div>
    <div class="analytics" id="analytics" aria-live="polite" aria-atomic="false">
      <section class="panel" aria-labelledby="trend-title">
        <div class="panel-heading"><h2 id="trend-title"></h2><p id="trend-subtitle"></p></div>
        <div class="chart-trend">
          <canvas id="trend-chart" role="img"></canvas>
          <span class="chart-empty" id="trend-empty" hidden>Нет данных за этот период</span>
        </div>
      </section>
      <section class="panel" aria-labelledby="structure-title">
        <div class="panel-heading"><h2 id="structure-title"></h2><p id="structure-subtitle"></p></div>
        <div class="breakdown-layout">
          <div class="chart-breakdown">
            <canvas id="structure-chart" role="img"></canvas>
            <span class="chart-empty" id="structure-empty" hidden>Нет данных</span>
          </div>
          <ul class="legend"></ul>
        </div>
      </section>
    </div>
    </div>`;

  const form = root.querySelector('form');
  const period = form.elements.period;
  const year = form.elements.year;
  const message = root.querySelector('.live-message');
  const dashboardData = root.querySelector('.dashboard-data');
  const analytics = root.querySelector('#analytics');
  const cards = [...root.querySelectorAll('.kpi')];
  let controller;
  let data = cachedDashboard?.key === JSON.stringify(dashboardParams()) ? cachedDashboard.payload : null;
  let charts = [];
  let trendReveal;

  function clearCharts() {
    trendReveal?.cancel();
    charts.forEach((chart) => chart.destroy());
    charts = [];
  }

  function showMessage(text, error = false) {
    message.textContent = text;
    message.hidden = !text;
    message.classList.toggle('error', error);
    if (error) {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'button small';
      retry.textContent = 'Повторить';
      retry.addEventListener('click', load);
      message.append(retry);
    }
  }

  function render() {
    clearCharts();
    const mode = modes[selected];
    const panel = data?.panels[selected];
    const format = mode.financial ? money : number;
    root.querySelector('#trend-title').textContent = mode.trend;
    root.querySelector('#structure-title').textContent = mode.structure;
    root.querySelector('#trend-subtitle').textContent = data ? {
      income: dateRange(data),
      debt: 'На конец месяца · текущий месяц на дату среза',
      properties: 'Последние 12 календарных месяцев',
      renewals: `${date(data.contract_start)} — ${date(data.contract_end)} · действующие договоры`,
    }[selected] : '';
    root.querySelector('#structure-subtitle').textContent = data ? {
      income: dateRange(data),
      debt: `Просрочка на ${date(data.as_of)}`,
      properties: `Актуальные объекты на ${date(data.as_of)}`,
      renewals: `Действующие договоры на ${date(data.as_of)} · KPI включает сроки до 180 дней`,
    }[selected] : '';

    const trendCanvas = root.querySelector('#trend-chart');
    const structureCanvas = root.querySelector('#structure-chart');
    const hasTrend = panel?.trend.some(({ value }) => Number(value) !== 0);
    const total = panel?.structure.reduce((sum, { value }) => sum + Number(value), 0) ?? 0;
    trendCanvas.hidden = !hasTrend;
    structureCanvas.hidden = !total;
    root.querySelector('#trend-empty').hidden = !panel || hasTrend;
    root.querySelector('#structure-empty').hidden = !panel || total !== 0;
    root.querySelector('.legend').innerHTML = panel?.structure.map(({ label, value }, index) => `
      <li title="${escape(format(value))}">
        <span class="legend-dot" style="background:${colors[index]}" aria-hidden="true"></span>
        <span>${escape(label)}</span><strong>${total ? percent.format(Number(value) / total) : '0%'}</strong>
      </li>`).join('') ?? '';

    if (!panel) return;
    if (hasTrend) {
      trendCanvas.setAttribute('aria-label', `${mode.trend}. ${panel.trend.map(({ label, value }) => `${label}, ${format(value)}`).join('; ')}.`);
      const chart = new Chart(trendCanvas, trendConfig(mode, panel.trend, format));
      charts.push(chart);
      if (!reducedMotion.matches) {
        trendReveal = trendCanvas.animate(
          { clipPath: ['inset(0 100% 0 0)', 'inset(0 0 0 0)'] },
          { duration: 620, easing: 'cubic-bezier(.25, .65, .35, 1)' },
        );
      }
    }
    if (total) {
      const totalLabel = mode.financial ? compactMoney(total) : number(total);
      structureCanvas.setAttribute('aria-label', `${mode.structure}. Всего ${format(total)}. ${panel.structure.map(({ label, value }) => `${label}, ${format(value)}`).join('; ')}.`);
      charts.push(new Chart(structureCanvas, breakdownConfig(panel.structure, format, total, totalLabel)));
    }
  }

  function fillCards() {
    for (const card of cards) {
      const key = card.dataset.mode;
      const value = card.querySelector('.kpi-value');
      const context = card.querySelector('.kpi-context');
      if (!data) {
        value.textContent = '—';
        value.removeAttribute('title');
        context.textContent = '';
        continue;
      }
      const amount = data.kpis[key];
      value.textContent = modes[key].financial ? compactMoney(amount) : number(amount);
      value.title = modes[key].financial ? money(amount) : number(amount);
      context.textContent = {
        income: dateRange(data), debt: `На ${date(data.as_of)}`,
        properties: `На ${date(data.as_of)}`, renewals: 'Окончание в ближайшие 180 дней',
      }[key];
    }
  }

  async function load() {
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    const params = dashboardParams();
    const key = JSON.stringify(params);
    analytics.setAttribute('aria-busy', 'true');
    if (!data) showMessage('Загрузка показателей…');
    try {
      const payload = await api('dashboard', { params, signal });
      if (signal.aborted) return;
      if (!window.Chart) throw new Error('Не удалось загрузить Chart.js.');
      const changed = JSON.stringify(data) !== JSON.stringify(payload);
      data = payload;
      cachedDashboard = { key, payload };
      setAppDate(data.as_of);
      if (changed) showData();
      showMessage('');
    } catch (error) {
      if (signal.aborted) return;
      showMessage(data ? `${error.message} Показаны последние полученные данные.` : error.message, true);
    } finally {
      if (!signal.aborted) analytics.setAttribute('aria-busy', 'false');
    }
  }

  cards.forEach((card) => card.addEventListener('click', () => {
    if (selected === card.dataset.mode) return;
    selected = card.dataset.mode;
    for (const candidate of cards) candidate.setAttribute('aria-pressed', String(candidate === card));
    render();
    reveal(analytics, { duration: 360, offset: 0 });
  }));

  period.value = financialPeriod;
  year.closest('label').hidden = financialPeriod !== 'year';
  form.addEventListener('submit', (event) => { event.preventDefault(); changePeriod(); });
  period.addEventListener('change', changePeriod);
  year.addEventListener('change', changePeriod);

  function changePeriod() {
    year.closest('label').hidden = period.value !== 'year';
    if (period.value === 'year' && !year.reportValidity()) return;
    const previousKey = JSON.stringify(dashboardParams());
    financialPeriod = period.value;
    if (financialPeriod === 'year') financialYear = year.valueAsNumber;
    if (previousKey === JSON.stringify(dashboardParams())) return;
    data = null;
    showData();
    load();
  }

  function showData() {
    dashboardData.classList.toggle('is-pending', !data);
    fillCards();
    render();
  }

  showData();
  load();
  return () => { controller?.abort(); clearCharts(); };
}

function dateRange(data) {
  return data.period_start ? `${date(data.period_start)} — ${date(data.period_end)}` : `До ${date(data.period_end)}`;
}

const tooltip = { backgroundColor: '#2a2c34', titleColor: '#f2f1ef', bodyColor: '#f2f1ef', padding: 12 };

function trendConfig(mode, points, format) {
  return {
    type: 'line',
    data: {
      labels: points.map(({ label }) => label),
      datasets: [{
        data: points.map(({ value }) => Number(value)),
        borderColor: mode.color,
        backgroundColor({ chart }) {
          const { ctx, chartArea } = chart;
          if (!chartArea) return `${mode.color}18`;
          const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          gradient.addColorStop(0, `${mode.color}49`);
          gradient.addColorStop(1, `${mode.color}03`);
          return gradient;
        },
        borderWidth: 2.2, fill: true, tension: .23,
        pointRadius: points.length > 48 ? 0 : 2.3,
        pointHoverRadius: 5, pointBackgroundColor: mode.color, pointBorderWidth: 0,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      // The canvas reveals the line. Keep Chart.js animation enabled for the tooltip.
      animation: reducedMotion.matches ? false : { duration: 0 },
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { right: 7 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          ...tooltip,
          animations: { numbers: { duration: 240, easing: 'easeOutCubic' } },
          callbacks: { label: ({ raw }) => format(raw) },
        },
      },
      scales: {
        x: {
          grid: { display: false }, border: { color: '#353743' },
          ticks: {
            maxRotation: 0, maxTicksLimit: 8, color: '#a9abb7',
            callback(index) {
              const yearChanges = index === 0 || points[index].label.slice(-4) !== points[index - 1].label.slice(-4);
              return yearChanges ? points[index].label : points[index].label.split(' ')[0];
            },
          },
        },
        y: {
          beginAtZero: true, grid: { color: '#30323b' }, border: { display: false },
          ticks: { color: '#a9abb7', maxTicksLimit: 5, precision: mode.financial ? undefined : 0,
            callback: mode.financial ? (value) => compactMoney(value) : (value) => number(value) },
        },
      },
    },
  };
}

function drawBreakdownTotal(chart, label) {
  const { ctx } = chart;
  const { x, y, innerRadius } = chart.getDatasetMeta(0).data[0];
  if (!innerRadius) return;

  const radius = innerRadius * .88;
  const valueSize = Math.min(24, radius * .42);
  const captionSize = Math.min(13, radius * .26);
  const valueOffset = -captionSize * .65;
  const captionOffset = valueSize * .65;
  const font = '"Segoe UI", system-ui, sans-serif';

  ctx.save();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  // Use the chord at the outside edge of each line, so even its corners fit.
  for (const [text, size, offset, weight, color] of [
    [label, valueSize, valueOffset, 750, '#f2f1ef'],
    ['Всего', captionSize, captionOffset, 400, '#a9abb7'],
  ]) {
    const edge = Math.abs(offset) + size / 2;
    const width = 2 * Math.sqrt(radius ** 2 - edge ** 2);
    ctx.font = `${weight} ${size}px ${font}`;
    const fittedSize = size * Math.min(1, width / ctx.measureText(text).width);
    ctx.font = `${weight} ${fittedSize}px ${font}`;
    ctx.fillStyle = color;
    ctx.fillText(text, x, y + offset);
  }
  ctx.restore();
}

function breakdownConfig(points, format, total, totalLabel) {
  return {
    type: 'doughnut',
    plugins: [{
      id: 'breakdown-total',
      afterDatasetsDraw(chart) {
        drawBreakdownTotal(chart, totalLabel);
      },
    }],
    data: {
      labels: points.map(({ label }) => label),
      datasets: [{
        data: points.map(({ value }) => Number(value)), backgroundColor: colors,
        borderWidth: 2, borderColor: '#191b21', hoverOffset: 6,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '73%',
      animation: reducedMotion.matches ? false : { duration: 620, easing: 'easeOutCubic' },
      layout: { padding: 5 },
      plugins: {
        legend: { display: false },
        tooltip: {
          ...tooltip,
          animations: { opacity: { duration: 0 } },
          callbacks: { label: ({ raw }) => `${format(raw)} · ${percent.format(raw / total)}` },
        },
      },
    },
  };
}
