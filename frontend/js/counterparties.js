import { api } from './api.js';
import { bindForm, confirmDelete, editView, field, fieldsTable, loadDetail, relatedTable } from './dialog.js';
import { contractDetail } from './contracts.js';
import { financialSummary } from './financial-summary.js';
import { recordButton } from './registry.js';
import { contractStates, date, escape, notify, number } from './ui.js';

const endpoint = 'counterparties';

function edit(record, reload) {
  const view = editView(record ? 'Редактирование контрагента' : 'Новый контрагент', `
    ${field('name', 'Наименование', record?.name, 'required maxlength="200" autofocus', true)}
    ${field('tax_id', 'ИНН', record?.tax_id, 'required inputmode="numeric" placeholder="10 или 12 цифр"')}
    ${field('email', 'Электронная почта', record?.email, 'type="email" maxlength="250" placeholder="name@example.com"')}`);
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

function detail(id, reload) {
  const related = { endpoint: 'contracts', params: { counterparty_id: id } };
  return loadDetail(`${endpoint}/${id}`, related, (record, initial, show) => {
    const view = show(record.name, `
      ${fieldsTable([['ID', id], ['ИНН', record.tax_id], ['Электронная почта', record.email, true],
        ['Всего договоров', number(record.contract_count)], ['Действующие договоры', number(record.active_contract_count)]])}
      ${financialSummary(record.finance, true)}
      <section class="related"><h3>Договоры контрагента</h3><div data-contracts></div></section>
      <div class="detail-actions">
        <button type="button" class="button danger" data-delete${record.contract_count ? ' disabled' : ''}>Удалить</button>
        <button type="button" class="button primary" data-edit>Редактировать</button>
      </div>
      ${record.contract_count ? '<p class="subheading">Удаление недоступно: у контрагента есть связанные договоры.</p>' : ''}`);
    relatedTable(view.content.querySelector('[data-contracts]'), {
      ...related, initial, columns: ['Номер', 'Начало', 'Окончание', 'Статус договора'],
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
  });
}

export const counterparties = {
  endpoint, title: 'Реестр контрагентов', subtitle: 'Организации и предприниматели, связанные с договорами',
  addLabel: 'Добавить контрагента', searchHint: 'Наименование или ИНН', filters: [],
  columns: ['Наименование', 'ИНН', 'Всего договоров', 'Действующие договоры'],
  renderRow: record => `<tr><td>${recordButton(record, record.name)}</td><td>${escape(record.tax_id ?? '–')}</td>
    <td>${number(record.contract_count)}</td><td>${number(record.active_contract_count)}</td></tr>`,
  edit, detail,
};
