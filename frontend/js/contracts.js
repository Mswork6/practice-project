import { api } from './api.js';
import { bindForm, bindLookup, confirmAction, confirmDelete, dialogError, editView, field, fieldsTable, lookup, noteField, openDialog, relatedTable, selectField } from './dialog.js';
import { accrualDetail } from './finance.js';
import { recordButton } from './registry.js';
import { badge, contractStates, contractTypes, date, escape, money, notify, today } from './ui.js';

const endpoint = 'contracts';

function term(record) {
  if (record.state !== 'active') return ['—', ''];
  if (record.days_remaining == null) return ['Без даты', ''];
  if (record.days_remaining === 0) return ['Истекает сегодня', 'attention'];
  return [`${record.days_remaining} дн.`, record.days_remaining <= 30 ? 'attention' : ''];
}

function addCounterparty(form, choose, view) {
  const toggle = form.querySelector('[data-new-party]');
  const editor = form.querySelector('.inline-editor');
  const fields = form.querySelectorAll('[data-party-field]');
  const save = editor.querySelector('[data-save-party]');
  const errorLabel = editor.querySelector('.form-error');
  function setOpen(open) {
    editor.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = open ? 'Свернуть форму контрагента' : 'Создать контрагента';
    if (open) fields[0].focus();
  }
  toggle.addEventListener('click', () => setOpen(editor.hidden));
  save.addEventListener('click', async () => {
    if (view.busy()) return;
    const data = Object.fromEntries([...fields].map((input) => [input.dataset.partyField, input.value.trim() || null]));
    errorLabel.hidden = true;
    save.disabled = true;
    view.setBusy(true);
    try {
      if (!data.name) throw new Error('Введите наименование контрагента.');
      const party = await api('counterparties', { method: 'POST', data });
      choose(party);
      notify('Контрагент создан и выбран.');
      fields.forEach((input) => { input.value = ''; });
      setOpen(false);
    } catch (error) {
      errorLabel.textContent = error.message;
      errorLabel.hidden = false;
    } finally {
      save.disabled = false;
      view.setBusy(false);
      (editor.hidden ? form.querySelector('[data-lookup=counterparty_id] [role=combobox]') : save).focus({ preventScroll: true });
    }
  });
}

function nextDay(isoDate) {
  const day = new Date(`${isoDate}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() + 1);
  return day.toISOString().slice(0, 10);
}

function edit(record, reload, source = null) {
  const initial = record ?? source;
  const day = today();
  const active = record?.state === 'active';
  const earliestStart = source?.state === 'expired' ? nextDay(source.end_date) : '';
  const heading = record ? 'Редактирование договора' : source ? 'Новый договор на основе предыдущего' : 'Новый договор';
  const view = editView(heading, `
    ${source ? `<p class="full-width subheading">На основе договора № ${escape(source.number)}. Укажите новый номер и период действия.</p>` : ''}
    ${field('number', 'Номер договора', record?.number, 'required maxlength="80" autofocus')}
    ${selectField('type', 'Тип договора', contractTypes, initial?.type)}
    ${lookup('property_id', 'Объект', 'Название, адрес или кадастровый номер')}
    ${lookup('counterparty_id', 'Контрагент', 'Название или ИНН')}
    <div class="full-width"><button type="button" class="button small" data-new-party aria-expanded="false" aria-controls="party-editor">Создать контрагента</button>
      <fieldset class="inline-editor" id="party-editor" hidden><legend>Новый контрагент</legend>
        <div class="form-grid">
          <label><span>Наименование</span><input data-party-field="name" maxlength="200"></label>
          <label><span>ИНН</span><input data-party-field="tax_id" maxlength="20"></label>
          <label class="full-width"><span>Контакт</span><input data-party-field="contact" maxlength="250"></label>
        </div>
        <p class="form-error" role="alert" hidden></p>
        <div class="form-actions"><button type="button" class="button small" data-save-party>Сохранить контрагента</button></div>
      </fieldset>
    </div>
    ${field('start_date', 'Дата начала', record?.start_date ?? day, `type="date" required${earliestStart ? ` min="${earliestStart}"` : ''}${active ? ` max="${day}"` : ''}`)}
    ${field('end_date', 'Дата окончания', record?.end_date, 'type="date"')}
    ${field('amount', 'Сумма по договору, ₽', initial?.amount, 'type="number" min="0.01" step="0.01"')}
    ${noteField('note', 'Примечание', source ? `На основе договора № ${source.number}.` : record?.note)}`);
  const form = bindForm(view, async (values) => {
    const data = Object.fromEntries([...values].map(([key, value]) => [key, value.trim() || null]));
    if (source?.property_archived && data.property_id === String(source.property_id)) {
      throw new Error('Восстановите исходный объект из архива или выберите другой объект.');
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
  const choose = bindLookup(form, 'counterparty_id', 'counterparties', (item) => item.tax_id ? `${item.name} · ИНН ${item.tax_id}` : item.name,
    initial && { id: initial.counterparty_id, name: initial.counterparty_name });
  addCounterparty(form, choose, view);
  function updateEndDate() {
    const start = form.elements.start_date.value;
    form.elements.end_date.required = form.elements.type.value !== 'sale' || (active && Boolean(record.end_date));
    form.elements.end_date.min = active && start < day ? day : start;
    form.elements.end_date.max = active ? record.end_date ?? '' : '';
  }
  updateEndDate();
  form.elements.type.addEventListener('change', () => {
    updateEndDate();
    refreshProperties(record?.property_archived || form.elements.type.value === initial?.type ? linkedProperty : null);
  });
  form.elements.start_date.addEventListener('change', updateEndDate);
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
  const view = editView('Продление договора', `
    <div class="full-width">${fieldsTable([['Номер договора', record.number], ['Текущая дата окончания', date(record.end_date)]])}</div>
    ${field('end_date', 'Новая дата окончания', '', `type="date" min="${nextDay(record.end_date)}" required autofocus`, true)}`, 'Продлить');
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

export async function contractDetail(id, reload, view = openDialog('Карточка договора', '<p role="status">Загрузка…</p>')) {
  view.refresh(() => contractDetail(id, reload, view));
  try {
    const record = await api(`${endpoint}/${id}`);
    const active = record.state === 'active';
    const expired = record.state === 'expired';
    const canEdit = active || record.state === 'pending';
    const canExtend = active && !record.property_archived && record.end_date &&
      ['real_estate_rent', 'land_rent'].includes(record.type);
    const html = `
      ${fieldsTable([
        ['ID', record.id], ['Тип', contractTypes[record.type]],
        ['Объект', record.property_archived ? `${record.property_name} · архив` : record.property_name],
        ['Контрагент', record.counterparty_name], ['Дата начала', date(record.start_date)],
        ['Дата окончания', date(record.end_date)], ['Статус договора', contractStates[record.state]],
        ...(active ? [['Осталось', term(record)[0]]] : []), ['Сумма', record.amount == null ? '—' : money(record.amount)],
        ['Примечание', record.note, true],
      ])}
      <section class="related"><h3>Начисления по договору</h3><div data-accruals></div></section>
      <div class="detail-actions"><button type="button" class="button danger" data-delete>Удалить</button>
        ${canEdit || expired ? `<button type="button" class="button" data-edit>${expired ? 'Изменить примечание' : 'Редактировать'}</button>` : ''}
        ${active || expired ? '<button type="button" class="button" data-complete>Завершить</button>' : ''}
        ${active ? '<button type="button" class="button danger" data-terminate>Расторгнуть</button>' : ''}
        ${canExtend ? '<button type="button" class="button primary" data-extend>Продлить</button>' : ''}
        ${!canEdit ? '<button type="button" class="button primary" data-successor>Создать новый договор на основе</button>' : ''}
      </div>`;
    if (!view.set(`Договор № ${record.number}`, html)) return;
    relatedTable(view.content.querySelector('[data-accruals]'), {
      endpoint: 'accruals', params: { contract_id: id }, columns: ['Начисление', 'Сумма', 'Остаток'],
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
  } catch (error) {
    dialogError(view, error);
  }
}

export const contracts = {
  title: 'Реестр договоров', subtitle: 'Договоры, контрагенты и сроки окончания',
  addLabel: 'Добавить договор', searchHint: 'Номер, объект или контрагент', endpoint,
  filters: [['type', 'Тип договора', contractTypes, 'Все типы'], ['state', 'Статус договора', contractStates, 'Все статусы']],
  columns: ['Договор', 'Контрагент', 'Окончание', 'Осталось', 'Сумма', 'Статус договора'],
  renderRow(item) {
    const tone = { active: 'positive', pending: 'attention', expired: 'negative', terminated: 'negative' }[item.state] ?? '';
    return `<tr><td>${recordButton(item, item.number)}
      <span class="secondary">${escape(contractTypes[item.type])} · ${escape(item.property_name)}</span></td>
      <td>${escape(item.counterparty_name)}</td><td class="number">${date(item.end_date)}</td>
      <td>${badge(...term(item))}</td><td class="number">${item.amount == null ? '—' : money(item.amount)}</td>
      <td>${badge(contractStates[item.state], tone)}</td></tr>`;
  },
  edit, detail: contractDetail,
};
