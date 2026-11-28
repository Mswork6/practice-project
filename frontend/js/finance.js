import { api } from './api.js';
import { bindForm, bindLookup, confirmDelete, editView, field, fieldsTable, lookup, noteField, loadDetail, relatedTable } from './dialog.js';
import { recordButton } from './registry.js';
import { badge, date, escape, money, notify, today } from './ui.js';

const endpoint = 'accruals';

function edit(record, reload) {
  const view = editView(record ? 'Редактирование начисления' : 'Новое начисление', `
    ${lookup('contract_id', 'Договор', 'Номер, объект или контрагент')}
    ${field('amount', 'Сумма начисления, ₽', record?.amount, 'inputmode="decimal" data-decimal required')}
    ${field('accrual_date', 'Дата начисления', record?.accrual_date ?? today(), 'type="date" required')}
    ${field('due_date', 'Срок оплаты', record?.due_date, 'type="date" required data-min-message="Срок оплаты не может предшествовать дате начисления."')}
    ${noteField('description', 'Описание', record?.description)}`);
  const form = bindForm(view, async (values) => {
    const data = Object.fromEntries([...values].map(([key, value]) => [key, value.trim() || null]));
    await api(record ? `${endpoint}/${record.id}` : endpoint, { method: record ? 'PATCH' : 'POST', data });
    notify(record ? 'Начисление обновлено.' : 'Начисление добавлено.');
    reload();
  });
  bindLookup(form, 'contract_id', 'contracts', (item) => [item.number, item.property_name, item.counterparty_name].filter(Boolean).join(' · '), record && {
    id: record.contract_id, number: record.contract_number, property_name: record.property_name,
  });
  function updateDueDate() { form.elements.due_date.min = form.elements.accrual_date.value; }
  form.elements.accrual_date.addEventListener('input', updateDueDate);
  updateDueDate();
}

function paymentForm(record, reload, editing = false) {
  const day = today();
  const accrual = editing ? record.accrual_id : record.id;
  const view = editView(editing ? 'Редактирование платежа' : 'Новый платёж', `
    <p class="full-width subheading">Максимальная сумма платежа по начислению № ${accrual} – ${money(record.payment_limit)}.</p>
    ${field('amount', 'Сумма платежа, ₽', editing ? record.amount : '', `inputmode="decimal" data-decimal data-limit="${escape(record.payment_limit)}" required autofocus`)}
    ${field('payment_date', 'Дата платежа', editing ? record.payment_date : day, `type="date" min="${escape(record.accrual_date)}" max="${day}" required
      data-min-message="Дата платежа не может предшествовать дате начисления." data-max-message="Дата платежа не может быть позже сегодняшней даты."`)}`);
  bindForm(view, async (values) => {
    const data = Object.fromEntries(values);
    if (editing) await api(`payments/${record.id}`, { method: 'PATCH', data });
    else await api('payments', { method: 'POST', data: { accrual_id: accrual, ...data } });
    notify(editing ? 'Платёж обновлён.' : 'Платёж зарегистрирован.');
    reload();
  });
}

function paymentDetail(id, reload) {
  return loadDetail(`payments/${id}`, null, (record, _, show) => {
    const html = `${fieldsTable([
      ['ID', id], ['Начисление', `№ ${record.accrual_id}`], ['Договор', record.contract_number],
      ['Дата платежа', date(record.payment_date)], ['Сумма', money(record.amount)],
    ])}
    <div class="detail-actions">
      <button type="button" class="button danger" data-delete>Удалить</button>
      <button type="button" class="button primary" data-edit>Редактировать</button>
    </div>`;
    const view = show(`Платёж № ${id}`, html);
    view.content.querySelector('[data-edit]').addEventListener('click', () => paymentForm(record, reload, true));
    view.content.querySelector('[data-delete]').addEventListener('click', () => {
      confirmDelete(`Удалить платёж № ${id} от ${date(record.payment_date)} на сумму ${money(record.amount)}?`, async () => {
        await api(`payments/${id}`, { method: 'DELETE' });
        notify('Платёж удалён.');
        reload();
      });
    });
  });
}

export function accrualDetail(id, reload) {
  const related = { endpoint: 'payments', params: { accrual_id: id } };
  return loadDetail(`${endpoint}/${id}`, related, (record, initial, show) => {
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
    const view = show(`Начисление № ${id}`, html);
    relatedTable(view.content.querySelector('[data-related]'), {
      ...related, initial, columns: ['ID', 'Дата платежа', 'Сумма'],
      renderRow: (payment) => `<tr><td>${recordButton(payment, `№ ${payment.id}`)}</td><td class="number">${date(payment.payment_date)}</td><td class="number">${money(payment.amount)}</td></tr>`,
      empty: 'По начислению пока нет платежей.',
      onClick: (paymentId) => paymentDetail(paymentId, reload),
    });
    view.content.querySelector('[data-pay]')?.addEventListener('click', () => paymentForm(record, reload));
    view.content.querySelector('[data-edit]')?.addEventListener('click', () => edit(record, reload));
    view.content.querySelector('[data-delete]')?.addEventListener('click', () => {
      confirmDelete(`Удалить начисление № ${id} по договору «${record.contract_number}»?`, async () => {
        await api(`${endpoint}/${id}`, { method: 'DELETE' });
        notify('Начисление удалено.');
        reload();
      });
    });
  });
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
      <td class="number">${Number(item.overdue) > 0 ? badge(money(item.overdue), 'negative') : '–'}</td></tr>`;
  },
  edit, detail: accrualDetail,
};
