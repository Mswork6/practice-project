import { api } from './api.js';
import { bindForm, bindLookup, confirmDelete, dialogError, editView, field, fieldsTable, lookup, noteField, openDialog, selectField } from './dialog.js';
import { recordButton } from './registry.js';
import { badge, contractStates, contractStatuses, contractTypes, date, escape, money, notify, today } from './ui.js';

const endpoint = 'contracts';

function term(record) {
  if (record.state !== 'active') return ['—', ''];
  if (record.days_remaining == null) return ['Без даты', ''];
  if (record.days_remaining < 0) return ['Срок истёк', 'negative'];
  if (record.days_remaining === 0) return ['Истекает сегодня', 'attention'];
  return [`${record.days_remaining} дн.`, record.days_remaining <= 30 ? 'attention' : ''];
}

function addCounterparty(form, choose) {
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
    const data = Object.fromEntries([...fields].map((input) => [input.dataset.partyField, input.value.trim() || null]));
    errorLabel.hidden = true;
    save.disabled = true;
    try {
      if (!data.name) throw new Error('Введите наименование контрагента.');
      const party = await api('counterparties', { method: 'POST', data });
      choose(party);
      notify('Контрагент создан и выбран.');
      fields.forEach((input) => { input.value = ''; });
      setOpen(false);
      form.elements.counterparty_id.focus();
    } catch (error) {
      errorLabel.textContent = error.message;
      errorLabel.hidden = false;
    } finally {
      save.disabled = false;
    }
  });
}

function edit(record, reload) {
  const view = editView(record ? 'Редактирование договора' : 'Новый договор', `
    ${field('number', 'Номер договора', record?.number, 'required maxlength="80" autofocus')}
    ${selectField('type', 'Тип договора', contractTypes, record?.type)}
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
    ${field('start_date', 'Дата начала', record?.start_date ?? today(), 'type="date" required')}
    ${field('end_date', 'Дата окончания', record?.end_date, 'type="date"')}
    ${field('amount', 'Сумма по договору, ₽', record?.amount, 'type="number" min="0.01" step="0.01"')}
    ${selectField('status', 'Статус записи', contractStatuses, record?.status)}
    ${noteField('note', 'Примечание', record?.note)}`);
  const form = bindForm(view, async (values) => {
    const data = Object.fromEntries([...values].map(([key, value]) => [key, value.trim() || null]));
    await api(record ? `${endpoint}/${record.id}` : endpoint, { method: record ? 'PATCH' : 'POST', data });
    notify(record ? 'Договор обновлён.' : 'Договор добавлен.');
    reload();
  });
  const linkedProperty = record && {
    id: record.property_id, name: record.property_name, status: record.property_archived ? 'archived' : null,
  };
  const refreshProperties = bindLookup(form, 'property_id', 'properties',
    (item) => [item.name, item.address, item.status === 'archived' ? 'архив' : ''].filter(Boolean).join(' · '),
    linkedProperty, () => ({ exclude_archived: true, contract_type: form.elements.type.value }));
  const choose = bindLookup(form, 'counterparty_id', 'counterparties', (item) => item.tax_id ? `${item.name} · ИНН ${item.tax_id}` : item.name,
    record && { id: record.counterparty_id, name: record.counterparty_name });
  addCounterparty(form, choose);
  function updateEndDate() {
    form.elements.end_date.required = form.elements.type.value !== 'sale';
    form.elements.end_date.min = form.elements.start_date.value;
  }
  updateEndDate();
  form.elements.type.addEventListener('change', () => {
    updateEndDate();
    refreshProperties(record?.property_archived || form.elements.type.value === record?.type ? linkedProperty : null);
  });
  form.elements.start_date.addEventListener('change', updateEndDate);
}

export async function contractDetail(id, reload) {
  const view = openDialog('Карточка договора', '<p role="status">Загрузка…</p>');
  try {
    const record = await api(`${endpoint}/${id}`);
    const html = `
      ${fieldsTable([
        ['ID', record.id], ['Тип', contractTypes[record.type]],
        ['Объект', record.property_archived ? `${record.property_name} · архив` : record.property_name],
        ['Контрагент', record.counterparty_name], ['Дата начала', date(record.start_date)],
        ['Дата окончания', date(record.end_date)], ['Состояние', contractStates[record.state]],
        ['Статус записи', contractStatuses[record.status]],
        ['Осталось', term(record)[0]], ['Сумма', record.amount == null ? '—' : money(record.amount)],
        ['Примечание', record.note, true],
      ])}
      <div class="detail-actions"><button type="button" class="button danger" data-delete>Удалить</button>
      <button type="button" class="button primary" data-edit>Редактировать</button></div>`;
    if (!view.set(`Договор № ${record.number}`, html)) return;
    view.content.querySelector('[data-edit]').addEventListener('click', () => edit(record, reload));
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
  filters: [['type', 'Тип договора', contractTypes, 'Все типы'], ['state', 'Состояние', contractStates, 'Все состояния']],
  columns: ['Договор', 'Контрагент', 'Окончание', 'Осталось', 'Сумма', 'Состояние'],
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
