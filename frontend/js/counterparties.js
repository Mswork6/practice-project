import { api } from './api.js';
import { bindForm, confirmDelete, dialogError, editView, field, fieldsTable, openDialog, relatedTable } from './dialog.js';
import { contractDetail } from './contracts.js';
import { financialSummary } from './financial-summary.js';
import { recordButton } from './registry.js';
import { contractStates, date, escape, notify, number } from './ui.js';

const endpoint = 'counterparties';

function edit(record, reload) {
  const view = editView(record ? 'Редактирование контрагента' : 'Новый контрагент', `
    ${field('name', 'Наименование', record?.name, 'required maxlength="200" autofocus', true)}
    ${field('tax_id', 'ИНН', record?.tax_id, 'inputmode="numeric" maxlength="24"')}
    ${field('contact', 'Контакт', record?.contact, 'maxlength="250"')}`);
  bindForm(view, async values => {
    const data = Object.fromEntries([...values].map(([key, value]) => [key, value.trim() || null])
      .filter(([key, value]) => !record || value !== record[key]));
    await api(record ? `${endpoint}/${record.id}` : endpoint, {
      method: record ? 'PATCH' : 'POST',
      data,
    });
    notify('Контрагент сохранён.');
    reload();
  });
}

async function detail(id, reload, view = openDialog('Карточка контрагента', '<p role="status">Загрузка…</p>')) {
  view.refresh(() => detail(id, reload, view));
  try {
    const record = await api(`${endpoint}/${id}`);
    if (!view.set(record.name, `
      ${fieldsTable([['ID', id], ['ИНН', record.tax_id], ['Контакт', record.contact, true],
        ['Всего договоров', number(record.contract_count)], ['Действующие договоры', number(record.active_contract_count)]])}
      ${financialSummary(record.finance, true)}
      <section class="related"><h3>Договоры контрагента</h3><div data-contracts></div></section>
      <div class="detail-actions">
        <button type="button" class="button danger" data-delete>Удалить</button>
        <button type="button" class="button primary" data-edit>Редактировать</button>
      </div>`)) return;
    relatedTable(view.content.querySelector('[data-contracts]'), {
      endpoint: 'contracts', params: { counterparty_id: id }, columns: ['Номер', 'Начало', 'Окончание', 'Статус договора'],
      renderRow: c => `<tr><td>${recordButton(c, c.number)}</td><td class="number">${date(c.start_date)}</td>
        <td class="number">${date(c.end_date)}</td><td>${contractStates[c.state]}</td></tr>`,
      empty: 'Договоров пока нет.', onClick: contractId => contractDetail(contractId, reload),
    });
    view.content.querySelector('[data-edit]').onclick = () => edit(record, reload);
    view.content.querySelector('[data-delete]').onclick = () => {
      confirmDelete(`Удалить контрагента «${record.name}»? Удаление доступно при отсутствии связанных договоров.`, async () => {
        await api(`${endpoint}/${id}`, { method: 'DELETE' });
        notify('Контрагент удалён.');
        reload();
      });
    };
  } catch (error) {
    dialogError(view, error);
  }
}

export const counterparties = {
  endpoint, title: 'Реестр контрагентов', subtitle: 'Организации и предприниматели, связанные с договорами',
  addLabel: 'Добавить контрагента', searchHint: 'Наименование или ИНН', filters: [],
  columns: ['Наименование', 'ИНН', 'Всего договоров', 'Действующие договоры'],
  renderRow: record => `<tr><td>${recordButton(record, record.name)}</td><td>${escape(record.tax_id ?? '—')}</td>
    <td>${number(record.contract_count)}</td><td>${number(record.active_contract_count)}</td></tr>`,
  edit, detail,
};
