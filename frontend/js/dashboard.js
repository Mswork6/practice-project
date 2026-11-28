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
let cachedDashboard;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

export function mountDashboard(root) {
  let financialYear = Number(today().slice(0, 4));
  root.innerHTML = `
    <header class="page-header">
      <div><h1>Дашборд имущества</h1><p class="subheading">Поступления, реестр и сроки договоров</p></div>
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
        <div class="panel-heading trend-heading"><div><h2 id="trend-title"></h2><p id="trend-subtitle"></p>
          <p class="chart-hint" id="trend-hint" hidden>Нажмите на столбец, чтобы посмотреть договоры</p></div>
          <label class="year-control" hidden><span>Год доходов</span><select name="year"></select></label>
        </div>
        <div class="chart-trend">
          <canvas id="trend-chart" role="img"></canvas>
          <span class="chart-empty" id="trend-empty" hidden>Нет данных за этот период</span>
        </div>
      </section>
      <section class="panel" aria-labelledby="structure-title">
        <div class="panel-heading"><h2 id="structure-title"></h2><p id="structure-subtitle"></p>
          <p class="chart-hint" id="structure-hint" hidden>Нажмите на категорию, чтобы посмотреть договоры</p></div>
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

  const year = root.querySelector('select[name=year]');
  const message = root.querySelector('.live-message');
  const dashboardData = root.querySelector('.dashboard-data');
  const analytics = root.querySelector('#analytics');
  const cards = [...root.querySelectorAll('.kpi')];
  let controller;
  let data = cachedDashboard?.key === String(financialYear) ? cachedDashboard.payload : null;
  let charts = [];
  let trendReveal;

  function clearCharts() {
    trendReveal?.cancel();
    charts.forEach((chart) => { chart.canvas.style.cursor = ''; chart.destroy(); });
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
    const chartColors = selected === 'renewals' ? ['#f09093', '#eac16c', '#709fd5', '#7eb896'] : colors;
    year.closest('label').hidden = selected !== 'income';
    year.innerHTML = (data?.income_years ?? [financialYear]).map(value => `<option>${value}</option>`).join('');
    year.value = financialYear;
    const panel = data?.panels[selected];
    const format = mode.financial ? money : number;
    root.querySelector('#trend-title').textContent = mode.trend;
    root.querySelector('#structure-title').textContent = mode.structure;
    root.querySelector('#trend-subtitle').textContent = data ? {
      income: data.panels.income.trend.some(point => point.future) ? 'Поступления по месяцам · серые подписи — будущие месяцы' : 'Поступления по месяцам',
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
    const hasTrend = panel && (selected === 'income' || panel.trend.some(({ value }) => Number(value) !== 0));
    const total = panel?.structure.reduce((sum, { value }) => sum + Number(value), 0) ?? 0;
    trendCanvas.hidden = !hasTrend;
    structureCanvas.hidden = !total;
    root.querySelector('#trend-empty').hidden = !panel || hasTrend;
    root.querySelector('#structure-empty').hidden = !panel || total !== 0;
    const interactive = selected === 'renewals';
    root.querySelector('#trend-hint').hidden = !interactive || !hasTrend;
    root.querySelector('#structure-hint').hidden = !interactive || !total;
    const legend = root.querySelector('.legend');
    legend.classList.toggle('contract-legend', interactive);
    legend.innerHTML = panel?.structure.map(({ key, label, value }, index) => {
      const linked = interactive && Number(value) > 0;
      return `
      <li title="${escape(format(value))}">
        ${linked ? `<a class="legend-link" href="${contractLink('term', key)}">` : ''}
        <span class="legend-dot" style="background:${chartColors[index]}" aria-hidden="true"></span>
        <span>${escape(label)}</span><strong>${total ? percent.format(Number(value) / total) : '0%'}</strong>
        ${linked ? `<span class="legend-chevron">${icon('chevron-right')}</span></a>` : ''}
      </li>`;
    }).join('') ?? '';

    if (!panel) return;
    if (hasTrend) {
      trendCanvas.setAttribute('aria-label', `${mode.trend}. ${panel.trend.map(({ label, value, future }) =>
        `${label}, ${future ? 'будущий месяц' : format(value)}`).join('; ')}.`);
      const chart = new Chart(trendCanvas, selected === 'debt' ? trendConfig(mode, panel.trend, format) : barConfig(mode, panel.trend, format));
      charts.push(chart);
      if (selected === 'debt' && !reducedMotion.matches) {
        trendReveal = trendCanvas.animate(
          { clipPath: ['inset(0 100% 0 0)', 'inset(0 0 0 0)'] },
          { duration: 620, easing: 'cubic-bezier(.25, .65, .35, 1)' },
        );
      }
    }
    if (total) {
      const totalLabel = mode.financial ? compactMoney(total) : number(total);
      structureCanvas.setAttribute('aria-label', `${mode.structure}. Всего ${format(total)}. ${panel.structure.map(({ label, value }) => `${label}, ${format(value)}`).join('; ')}.`);
      charts.push(new Chart(structureCanvas, breakdownConfig(panel.structure, format, total, totalLabel, chartColors, selected === 'renewals')));
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
        income: `За ${data.period_start.slice(0, 4)} год`, debt: `На ${date(data.as_of)}`,
        properties: `На ${date(data.as_of)}`, renewals: 'Окончание в ближайшие 180 дней',
      }[key];
    }
  }

  async function load() {
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    const params = { year: financialYear };
    const key = String(financialYear);
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
      if (data) { financialYear = Number(data.period_start.slice(0, 4)); year.value = financialYear; }
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

  year.addEventListener('change', () => {
    financialYear = Number(year.value);
    load();
  });

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

function contractLink(key, value) {
  return '#contracts?' + new URLSearchParams({ [key]: value });
}

function linksToContracts(points, key) {
  const enabled = elements => elements.length && Number(points[elements[0].index].value) > 0;
  return {
    onHover(event, elements, chart) {
      chart.canvas.style.cursor = enabled(elements) ? 'pointer' : '';
    },
    onClick(event, elements) {
      if (enabled(elements)) location.hash = contractLink(key, points[elements[0].index].key);
    },
  };
}

function growBars() {
  let progress = 0;
  let request;
  let start;
  let heights;
  return {
    id: 'grow-bars',
    afterDraw(chart) {
      if (request !== undefined || !chart.chartArea) return;
      function drawFrame() {
        const time = performance.now();
        start ??= time;
        const elapsed = Math.min(1, (time - start) / 650);
        progress = reducedMotion.matches ? 1 : 1 - (1 - elapsed) ** 3;
        chart.draw();
        if (progress < 1) request = requestAnimationFrame(drawFrame);
      }
      request = requestAnimationFrame(drawFrame);
    },
    beforeDatasetDraw(_chart, { meta }) {
      if (progress === 1) return;
      // Draw against the final axes, independently of Chart.js responsive layout updates.
      heights = meta.data.map(bar => {
        const original = { y: bar.y, height: bar.height };
        bar.y = bar.base + (bar.y - bar.base) * progress;
        bar.height *= progress;
        return original;
      });
    },
    afterDatasetDraw(_chart, { meta }) {
      if (!heights) return;
      meta.data.forEach((bar, index) => Object.assign(bar, heights[index]));
      heights = null;
    },
    beforeDestroy() {
      cancelAnimationFrame(request);
    },
  };
}

function barConfig(mode, points, format) {
  const line = trendConfig(mode, points, format);
  return {
    type: 'bar',
    plugins: reducedMotion.matches ? [] : [growBars()],
    data: {
      labels: line.data.labels,
      datasets: [{
        data: points.map(point => point.future ? null : Number(point.value)),
        backgroundColor: mode.color + 'bf',
        hoverBackgroundColor: mode.color,
        borderRadius: 3,
        maxBarThickness: 48,
      }],
    },
    options: {
      ...line.options,
      interaction: { mode: 'nearest', intersect: true },
      ...(mode === modes.renewals ? linksToContracts(points, 'expiry_month') : {}),
      scales: {
        ...line.options.scales,
        x: {
          ...line.options.scales.x,
          ticks: { ...line.options.scales.x.ticks, maxTicksLimit: 12,
            color: ({ index }) => points[index]?.future ? '#717381' : '#d0d2dd' },
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

function breakdownConfig(points, format, total, totalLabel, chartColors, interactive) {
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
        data: points.map(({ value }) => Number(value)), backgroundColor: chartColors,
        borderWidth: 2, borderColor: '#191b21', hoverOffset: 6,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '73%',
      ...(interactive ? linksToContracts(points, 'term') : {}),
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
