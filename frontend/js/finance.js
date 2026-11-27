import { api } from './api.js';
import { bindForm, bindLookup, confirmDelete, dialogError, editView, field, fieldsTable, lookup, noteField, openDialog, relatedTable } from './dialog.js';
import { recordButton } from './registry.js';
import { badge, date, escape, money, notify, today } from './ui.js';

const endpoint = 'accruals';

function edit(record, reload) {
  const view = editView(record ? 'Редактирование начисления' : 'Новое начисление', `
    ${lookup('contract_id', 'Договор', 'Номер, объект или контрагент')}
    ${field('amount', 'Сумма начисления, ₽', record?.amount, 'type="number" min="0.01" step="0.01" required')}
    ${field('accrual_date', 'Дата начисления', record?.accrual_date ?? today(), 'type="date" required')}
    ${field('due_date', 'Срок оплаты', record?.due_date, 'type="date" required')}
    ${noteField('description', 'Описание', record?.description)}`);
  const form = bindForm(view, async (values) => {
    const data = Object.fromEntries([...values].map(([key, value]) => [key, value.trim() || null]));
    await api(record ? `${endpoint}/${record.id}` : endpoint, { method: record ? 'PATCH' : 'POST', data });
    notify(record ? 'Начисление обновлено.' : 'Начисление добавлено.');
    reload();
  });
  bindLookup(form, 'contract_id', 'contracts', (item) => `${item.number} · ${item.property_name}`, record && {
    id: record.contract_id, number: record.contract_number, property_name: record.property_name,
  });
  function updateDueDate() { form.elements.due_date.min = form.elements.accrual_date.value; }
  form.elements.accrual_date.addEventListener('change', updateDueDate);
  updateDueDate();
}

function addPayment(record, reload) {
  const day = today();
  const view = editView('Новый платёж', `
    <p class="full-width subheading">Доступный остаток по начислению № ${record.id} — ${money(record.payment_limit)}.</p>
    ${field('amount', 'Сумма платежа, ₽', '', `type="number" min="0.01" step="0.01" max="${escape(record.payment_limit)}" required autofocus`)}
    ${field('payment_date', 'Дата платежа', day, `type="date" min="${escape(record.accrual_date)}" max="${day}" required`)}`);
  bindForm(view, async (values) => {
    await api('payments', { method: 'POST', data: { accrual_id: record.id, ...Object.fromEntries(values) } });
    notify('Платёж зарегистрирован.');
    reload();
  });
}

export async function accrualDetail(id, reload) {
  const view = openDialog('Карточка начисления', '<p role="status">Загрузка…</p>');
  try {
    const record = await api(`${endpoint}/${id}`);
    const futureAccrual = record.accrual_date > today();
    const hasFuturePayments = Number(record.payment_limit) < Number(record.remaining);
    const html = `
      ${fieldsTable([
        ['Договор', record.contract_number], ['Объект', record.property_name],
        ['Дата начисления', date(record.accrual_date)], ['Срок оплаты', date(record.due_date)],
        ['Начислено', money(record.amount)], ['Оплачено на сегодня', money(record.paid)],
        ['Остаток на сегодня', money(record.remaining)], ['Просрочено на сегодня', money(record.overdue)],
        ['Описание', record.description, true],
      ])}
      <section class="related"><h3>История платежей</h3><div data-related></div></section>
      ${futureAccrual ? '<p class="subheading">Платёж доступен с даты начисления.</p>' : ''}
      ${hasFuturePayments ? '<p class="subheading">Платежи с будущими датами сохранены в истории и ещё не входят в сумму оплаты на сегодня.</p>' : ''}
      <div class="detail-actions">
        ${record.has_payments ? '' : '<button type="button" class="button danger" data-delete>Удалить</button><button type="button" class="button" data-edit>Редактировать</button>'}
        ${!futureAccrual && Number(record.payment_limit) > 0 ? '<button type="button" class="button primary" data-pay>Зарегистрировать платёж</button>' : ''}
      </div>`;
    if (!view.set(`Начисление № ${id}`, html)) return;
    relatedTable(view.content.querySelector('[data-related]'), {
      endpoint: 'payments', params: { accrual_id: id }, columns: ['ID', 'Дата платежа', 'Сумма'],
      renderRow: (payment) => `<tr><td>${payment.id}</td><td class="number">${date(payment.payment_date)}</td><td class="number">${money(payment.amount)}</td></tr>`,
      empty: 'По начислению пока нет платежей.',
    });
    view.content.querySelector('[data-pay]')?.addEventListener('click', () => addPayment(record, reload));
    view.content.querySelector('[data-edit]')?.addEventListener('click', () => edit(record, reload));
    view.content.querySelector('[data-delete]')?.addEventListener('click', () => {
      confirmDelete(`Удалить начисление № ${id} по договору «${record.contract_number}»?`, async () => {
        await api(`${endpoint}/${id}`, { method: 'DELETE' });
        notify('Начисление удалено.');
        reload();
      });
    });
  } catch (error) {
    dialogError(view, error);
  }
}

export const finance = {
  title: 'Финансы', subtitle: 'Начисления, оплаты и просрочка по договорам',
  addLabel: 'Добавить начисление', searchHint: 'Номер договора или объект', endpoint,
  filters: [['status', 'Состояние оплаты', { paid: 'Оплачено', open: 'Есть остаток', overdue: 'Просрочено' }, 'Все начисления']],
  columns: ['Начисление', 'Срок оплаты', 'Начислено', 'Оплачено', 'Остаток', 'Просрочено'],
  renderRow(item) {
    return `<tr><td>${recordButton(item, `№ ${item.id} · ${item.contract_number}`)}<span class="secondary">${escape(item.property_name)}</span></td>
      <td class="number">${date(item.due_date)}</td><td class="number">${money(item.amount)}</td>
      <td class="number">${money(item.paid)}</td><td class="number">${money(item.remaining)}</td>
      <td class="number">${Number(item.overdue) > 0 ? badge(money(item.overdue), 'negative') : '—'}</td></tr>`;
  },
  edit, detail: accrualDetail,
};
