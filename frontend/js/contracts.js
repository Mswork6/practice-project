import { api, FormError } from './api.js';
import { updateRequiredMark } from './form-validation.js';
import { DATE_FLOOR, dateLimits, notEarlier, notLater } from './date-limits.js';
import { bindForm, bindLookup, confirmAction, confirmDelete, editView, field, fieldsTable, lookup, noteField, loadDetail, relatedTable, selectField } from './dialog.js';
import { accrualDetail } from './finance.js';
import { financialSummary } from './financial-summary.js';
import { recordButton } from './registry.js';
import { badge, contractStates, contractTypes, date, escape, money, notify } from './ui.js';

const endpoint = 'contracts';
const rentalTypes = ['real_estate_rent', 'land_rent'];
const expiryTerms = { '': 'Все', '0-30': 'До 30 дней', '31-90': '31–90 дней', '91-180': '91–180 дней', '181+': 'Более 180 дней' };

function setupFilters(form, initial, reload) {
  const { term, expiry_month: month, sort, state } = form.elements;
  const chips = [...form.querySelectorAll('[data-term]')];
  const monthChip = form.querySelector('[data-month]');
  function show() {
    chips.forEach(chip => chip.setAttribute('aria-pressed', String(chip.dataset.term === term.value)));
    monthChip.hidden = !month.value;
    if (month.value) {
      monthChip.querySelector('span').textContent = 'Окончание · ' +
        new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric', timeZone: 'UTC' })
          .format(new Date(`${month.value}-01T00:00:00Z`));
    }
  }
  if (Object.hasOwn(expiryTerms, initial.get('term'))) term.value = initial.get('term');
  if (/^\d{4}-(0[1-9]|1[0-2])$/.test(initial.get('expiry_month'))) month.value = initial.get('expiry_month');
  if (term.value || month.value) {
    state.value = 'active';
    sort.value = 'end_asc';
  }
  for (const chip of chips) {
    chip.onclick = () => {
      term.value = chip.dataset.term;
      month.value = '';
      state.value = term.value ? 'active' : '';
      if (term.value) sort.value = 'end_asc';
      show();
      reload();
    };
  }
  monthChip.querySelector('button').onclick = () => {
    month.value = '';
    show();
    reload();
  };
  state.addEventListener('change', () => {
    if (state.value !== 'active') {
      term.value = '';
      month.value = '';
      show();
    }
  });
  show();
}

function term(record) {
  if (record.state !== 'active') return ['–', ''];
  if (record.days_remaining == null) return ['Без даты', ''];
  if (record.days_remaining === 0) return ['Истекает сегодня', 'attention'];
  return [`${record.days_remaining} дн.`, record.days_remaining <= 30 ? 'attention' : ''];
}

function nextDay(isoDate) {
  const day = new Date(`${isoDate}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() + 1);
  return day.toISOString().slice(0, 10);
}

function edit(record, reload, source = null) {
  const initial = record ?? source;
  const { day, soon, distant } = dateLimits();
  const active = record?.state === 'active';
  const activeRental = active && rentalTypes.includes(record.type);
  const earliestStart = source?.state === 'expired' ? nextDay(source.end_date) : '';
  const startMin = earliestStart > DATE_FLOOR ? earliestStart : DATE_FLOOR;
  const heading = record ? 'Редактирование договора' : source ? 'Новый договор на основе предыдущего' : 'Новый договор';
  const view = editView(heading, `
    ${source ? `<p class="full-width subheading">На основе договора № ${escape(source.number)}. Укажите новый номер и период действия.</p>` : ''}
    ${field('number', 'Номер договора', record?.number, 'required maxlength="80" autofocus')}
    ${selectField('type', 'Тип договора', contractTypes, initial?.type)}
    ${lookup('property_id', 'Объект', 'Название, адрес или кадастровый номер')}
    ${lookup('counterparty_id', 'Контрагент', 'Название или ИНН')}
    ${field('start_date', 'Дата начала', record?.start_date ?? day, `type="date" required min="${startMin}" max="${active ? day : soon}"
      data-min-message="${startMin > DATE_FLOOR ? 'Новый договор должен начинаться после даты окончания предыдущего.' : notEarlier('Дата начала договора')}"
      data-max-message="${active ? 'Изменённые даты должны оставлять договор действующим на сегодня.' : notLater('Дата начала договора', soon)}"`)}
    ${field('end_date', 'Дата окончания', record?.end_date, 'type="date"')}
    ${field('amount', 'Сумма по договору, ₽', initial?.amount, 'inputmode="decimal" data-decimal')}
    ${noteField('note', 'Примечание', source ? `На основе договора № ${source.number}.` : record?.note)}`);
  const form = bindForm(view, async (values) => {
    const data = Object.fromEntries([...values].map(([key, value]) => [key, value.trim() || null]));
    if (source?.property_archived && data.property_id === String(source.property_id)) {
      throw new FormError('', [{
        loc: ['body', 'property_id'], msg: 'Восстановите исходный объект из архива или выберите другой объект.',
      }]);
    }
    const url = record ? `${endpoint}/${record.id}` : source ? `${endpoint}/${source.id}/successor` : endpoint;
    await api(url, { method: record ? 'PATCH' : 'POST', data });
    notify(record ? 'Договор обновлён.' : 'Договор добавлен.');
    reload();
  });
  const linkedProperty = initial && {
    id: initial.property_id, name: initial.property_name, status: initial.property_archived ? 'archived' : null,
  };
  const refreshProperties = bindLookup(form, 'property_id', 'properties',
    (item) => [item.name, item.address, item.status === 'archived' ? 'архив' : ''].filter(Boolean).join(' · '),
    linkedProperty, () => ({ exclude_archived: true, contract_type: form.elements.type.value }));
  bindLookup(form, 'counterparty_id', 'counterparties', (item) => item.tax_id ? `${item.name} · ИНН ${item.tax_id}` : item.name,
    initial && { id: initial.counterparty_id, name: initial.counterparty_name });
  const { type, start_date: start, end_date: end } = form.elements;
  if (activeRental) {
    for (const option of type.options) option.disabled = !rentalTypes.includes(option.value);
  }
  function updateEndDate() {
    const limited = active && (activeRental || rentalTypes.includes(type.value));
    const cap = limited ? record.end_date : '';
    const follows = start.value > DATE_FLOOR;
    const activeFloor = active && start.value <= day;
    end.required = type.value !== 'sale' || (active && Boolean(record.end_date));
    end.min = activeFloor ? day : follows ? start.value : DATE_FLOOR;
    end.max = cap && cap <= distant ? cap : distant;
    end.dataset.minMessage = activeFloor ? 'Изменённые даты должны оставлять договор действующим на сегодня.' :
      follows ? 'Дата окончания не может быть раньше даты начала.' : notEarlier('Дата окончания договора');
    end.dataset.maxMessage = cap && cap <= distant ?
      'Увеличить срок действующей аренды можно через действие «Продлить».' : notLater('Дата окончания договора', distant);
    updateRequiredMark(end);
  }
  updateEndDate();
  form.elements.type.addEventListener('change', () => {
    updateEndDate();
    refreshProperties(record?.property_archived || form.elements.type.value === initial?.type ? linkedProperty : null);
  });
  form.elements.start_date.addEventListener('input', updateEndDate);
}

function editNote(record, reload) {
  const view = editView('Примечание к договору', noteField('note', 'Примечание', record.note));
  bindForm(view, async (values) => {
    await api(`${endpoint}/${record.id}`, { method: 'PATCH', data: { note: values.get('note').trim() || null } });
    notify('Примечание обновлено.');
    reload();
  });
}

function extend(record, reload) {
  const { distant } = dateLimits();
  const view = editView('Продление договора', `
    <div class="full-width">${fieldsTable([['Номер договора', record.number], ['Текущая дата окончания', date(record.end_date)]])}</div>
    ${field('end_date', 'Новая дата окончания', '', `type="date" min="${nextDay(record.end_date)}" max="${distant}" required autofocus
      data-min-message="Новая дата окончания должна быть позже текущей даты окончания договора." data-max-message="${notLater('Дата окончания договора', distant)}"`, true)}`, 'Продлить');
  bindForm(view, async (values) => {
    await api(`${endpoint}/${record.id}/extend`, { method: 'POST', data: { end_date: values.get('end_date') } });
    notify('Договор продлён.');
    reload();
  });
}

function confirmClosure(record, action, reload) {
  const complete = action === 'complete';
  const label = complete ? 'Завершить' : 'Расторгнуть';
  confirmAction(complete ? 'Завершение договора' : 'Расторжение договора',
    `${label} договор № ${record.number}? Даты договора и финансовая история сохранятся.`, label, async () => {
      await api(`${endpoint}/${record.id}/${action}`, { method: 'POST' });
      notify(complete ? 'Договор завершён.' : 'Договор расторгнут.');
      reload();
    });
}

export function contractDetail(id, reload) {
  const related = { endpoint: 'accruals', params: { contract_id: id } };
  return loadDetail(`${endpoint}/${id}`, related, (record, initial, show) => {
    const active = record.state === 'active';
    const expired = record.state === 'expired';
    const canEdit = active || record.state === 'pending';
    const canExtend = active && !record.property_archived && record.end_date && rentalTypes.includes(record.type);
    const html = `
      ${fieldsTable([
        ['ID', record.id], ['Тип', contractTypes[record.type]],
        ['Объект', record.property_archived ? `${record.property_name} · архив` : record.property_name],
        ['Контрагент', record.counterparty_name], ['Дата начала', date(record.start_date)],
        ['Дата окончания', date(record.end_date)], ['Статус договора', contractStates[record.state]],
        ...(active ? [['Осталось', term(record)[0]]] : []), ['Сумма', record.amount == null ? '–' : money(record.amount)],
        ['Примечание', record.note, true],
      ])}
      ${financialSummary(record.finance)}
      <section class="related"><h3>Начисления по договору</h3><div data-accruals></div></section>
      <div class="detail-actions"><button type="button" class="button danger" data-delete${initial.total ? ' disabled' : ''}>Удалить</button>
        ${canEdit || expired ? `<button type="button" class="button" data-edit>${expired ? 'Изменить примечание' : 'Редактировать'}</button>` : ''}
        ${active || expired ? '<button type="button" class="button" data-complete>Завершить</button>' : ''}
        ${active ? '<button type="button" class="button danger" data-terminate>Расторгнуть</button>' : ''}
        ${canExtend ? '<button type="button" class="button primary" data-extend>Продлить</button>' : ''}
        ${!canEdit ? '<button type="button" class="button primary" data-successor>Создать новый договор на основе</button>' : ''}
      </div>
      ${initial.total ? '<p class="subheading">Удаление недоступно: по договору есть начисления.</p>' : ''}`;
    const view = show(`Договор № ${record.number}`, html);
    relatedTable(view.content.querySelector('[data-accruals]'), {
      ...related, initial, columns: ['Начисление', 'Сумма', 'Остаток'],
      renderRow: (item) => `<tr><td>${recordButton(item, `№ ${item.id} · ${date(item.accrual_date)}`)}</td>
        <td class="number">${money(item.amount)}</td><td class="number">${money(item.remaining)}</td></tr>`,
      empty: 'По договору пока нет начислений.', onClick: (accrualId) => accrualDetail(accrualId, reload),
    });
    view.content.querySelector('[data-edit]')?.addEventListener('click', () => expired ? editNote(record, reload) : edit(record, reload));
    view.content.querySelector('[data-complete]')?.addEventListener('click', () => confirmClosure(record, 'complete', reload));
    view.content.querySelector('[data-terminate]')?.addEventListener('click', () => confirmClosure(record, 'terminate', reload));
    view.content.querySelector('[data-extend]')?.addEventListener('click', () => extend(record, reload));
    view.content.querySelector('[data-successor]')?.addEventListener('click', () => edit(null, reload, record));
    view.content.querySelector('[data-delete]').addEventListener('click', () => {
      confirmDelete(`Удалить договор № ${record.number}? Удаление возможно, если по договору нет начислений.`, async () => {
        await api(`${endpoint}/${id}`, { method: 'DELETE' });
        notify('Договор удалён.');
        reload();
      });
    });
  });
}

export const contracts = {
  title: 'Реестр договоров', subtitle: 'Договоры, контрагенты и сроки окончания',
  addLabel: 'Добавить договор', searchHint: 'Номер, объект или контрагент', endpoint,
  filters: [['type', 'Тип договора', contractTypes, 'Все типы'], ['state', 'Статус договора', contractStates, 'Все статусы']],
  columns: ['Договор', 'Контрагент', 'Начало', 'Окончание', 'Осталось', 'Сумма', 'Статус договора'],
  sortable: { 'Начало': 'start', 'Окончание': 'end' },
  filterControls: `<div class="quick-filters" role="group" aria-labelledby="term-label">
    <span id="term-label" class="filter-label">Оставшийся срок действия договора</span>
    <input type="hidden" name="term"><input type="hidden" name="expiry_month"><input type="hidden" name="sort">
    ${Object.entries(expiryTerms).map(([term, label]) => `<button type="button" class="button small" data-term="${term}">${label}</button>`).join('')}
    <div class="month-filter" data-month hidden><span></span><button type="button" class="button small" aria-label="Сбросить месяц">×</button></div>
  </div>`,
  setupFilters,
  renderRow(item) {
    const tone = { active: 'positive', pending: 'attention', expired: 'negative', terminated: 'negative' }[item.state] ?? '';
    return `<tr><td>${recordButton(item, item.number)}
      <span class="secondary">${escape(contractTypes[item.type])} · ${escape(item.property_name)}</span></td>
      <td>${escape(item.counterparty_name)}</td><td class="number">${date(item.start_date)}</td><td class="number">${date(item.end_date)}</td>
      <td>${badge(...term(item))}</td><td class="number">${item.amount == null ? '–' : money(item.amount)}</td>
      <td>${badge(contractStates[item.state], tone)}</td></tr>`;
  },
  edit, detail: contractDetail,
};
