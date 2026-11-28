import { api } from './api.js';
import { bindForm, confirmDelete, editView, field, fieldsTable, noteField, loadDetail, relatedTable, selectField } from './dialog.js';
import { recordButton } from './registry.js';
import { badge, contractStates, date, escape, notify, number, propertyStatuses, propertyTypes } from './ui.js';
import { contractDetail } from './contracts.js';

const endpoint = 'properties';
const area = (value) => value == null ? '–' : `${number(value)} м²`;

function edit(record, reload) {
  const view = editView(record ? 'Редактирование объекта' : 'Новый объект', `
    ${field('name', 'Наименование', record?.name, 'required maxlength="200" autofocus', true)}
    ${selectField('type', 'Категория', propertyTypes, record?.type)}
    ${record ? `<p class="lookup-hint">Статус – ${propertyStatuses[record.status]}.</p>` : ''}
    ${field('address', 'Адрес', record?.address, 'required maxlength="300"', true)}
    ${field('area', 'Площадь, м²', record?.area, 'inputmode="decimal" data-decimal')}
    ${field('cadastral_number', 'Кадастровый номер', record?.cadastral_number, 'maxlength="80" placeholder="00:00:0000000:000"')}
    ${noteField('note', 'Примечание', record?.note)}`);
  bindForm(view, async (values) => {
    const data = Object.fromEntries([...values].map(([key, value]) => [key, value.trim() || null]));
    await api(record ? `${endpoint}/${record.id}` : endpoint, { method: record ? 'PATCH' : 'POST', data });
    notify(record ? 'Объект обновлён.' : 'Объект добавлен.');
    reload();
  });
}

function detail(id, reload) {
  const related = { endpoint: 'contracts', params: { property_id: id } };
  return loadDetail(`${endpoint}/${id}`, related, (record, initial, show) => {
    const html = `
      ${fieldsTable([
        ['ID', record.id], ['Дата внесения', date(record.created_at)], ['Адрес', record.address, true],
        ['Категория', propertyTypes[record.type]], ['Статус', propertyStatuses[record.status]],
        ['Площадь', area(record.area)], ['Кадастровый номер', record.cadastral_number],
        ['Примечание', record.note, true],
      ])}
      <section class="related"><h3>Договоры объекта</h3><div data-related></div></section>
      <div class="detail-actions">
        ${record.status === 'archived' ? '<button type="button" class="button" data-archive>Восстановить</button>' :
          `<button type="button" class="button" data-archive${record.can_archive ? '' : ' disabled'}>Архивировать</button>`}
        <button type="button" class="button danger" data-delete>Удалить</button>
        <button type="button" class="button primary" data-edit>Редактировать</button>
      </div>
      ${record.status !== 'archived' && !record.can_archive ?
        '<p class="form-error">Архивирование доступно после окончания действующих и ожидающих начала договоров.</p>' : ''}`;
    const view = show(record.name, html);
    relatedTable(view.content.querySelector('[data-related]'), {
      ...related, initial, columns: ['Номер', 'Статус договора', 'Окончание'],
      renderRow: (item) => `<tr><td>${recordButton(item, item.number)}</td><td>${escape(contractStates[item.state])}</td><td class="number">${date(item.end_date)}</td></tr>`,
      empty: 'У объекта пока нет договоров.', onClick: (contractId) => contractDetail(contractId, reload),
    });
    view.content.querySelector('[data-archive]')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      view.setBusy(true);
      try {
        const restore = record.status === 'archived';
        await api(`${endpoint}/${id}`, { method: 'PATCH', data: { status: restore ? 'available' : 'archived' } });
        notify(restore ? 'Объект восстановлен.' : 'Объект перемещён в архив.');
        reload();
        view.close();
      } catch (error) {
        if (!view.current()) return;
        view.content.querySelector('.form-error')?.remove();
        const message = document.createElement('p');
        message.setAttribute('role', 'alert');
        message.className = 'form-error';
        message.textContent = error.message;
        view.content.append(message);
      } finally {
        button.disabled = false;
        view.setBusy(false);
      }
    });
    view.content.querySelector('[data-edit]').addEventListener('click', () => edit(record, reload));
    view.content.querySelector('[data-delete]').addEventListener('click', () => {
      confirmDelete(`Удалить объект «${record.name}»? Удаление возможно, если у объекта нет договоров.`, async () => {
        await api(`${endpoint}/${id}`, { method: 'DELETE' });
        notify('Объект удалён.');
        reload();
      });
    });
  });
}

export const properties = {
  title: 'Реестр объектов', subtitle: 'Муниципальные объекты и связанные с ними договоры',
  addLabel: 'Добавить объект', searchHint: 'Название, адрес или кадастровый номер', endpoint,
  filters: [['type', 'Категория', propertyTypes, 'Все категории'], ['status', 'Статус', propertyStatuses, 'Все статусы']],
  columns: ['Объект', 'Категория', 'Площадь', 'Статус', 'Дата внесения'],
  renderRow(item) {
    const tone = item.status === 'available' ? 'positive' : item.status === 'in_use' ? 'attention' : '';
    return `<tr><td>${recordButton(item, item.name)}<span class="secondary">${escape(item.address)}</span>${item.cadastral_number ? `<span class="secondary">${escape(item.cadastral_number)}</span>` : ''}</td>
      <td>${escape(propertyTypes[item.type])}</td><td class="number">${area(item.area)}</td>
      <td>${badge(propertyStatuses[item.status], tone)}</td><td class="number">${date(item.created_at)}</td></tr>`;
  },
  edit, detail,
};
