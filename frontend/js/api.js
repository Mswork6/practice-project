const fields = {
  name: 'Наименование', type: 'Тип', status: 'Статус', address: 'Адрес', area: 'Площадь', amount: 'Сумма',
  start_date: 'Дата начала', end_date: 'Дата окончания', accrual_date: 'Дата начисления', due_date: 'Срок оплаты',
  payment_date: 'Дата платежа', number: 'Номер договора', property_id: 'Объект', counterparty_id: 'Контрагент',
  contract_id: 'Договор', accrual_id: 'Начисление', tax_id: 'ИНН', note: 'Примечание', year: 'Год',
};

function describe(detail) {
  if (typeof detail === 'string') return detail;
  if (!Array.isArray(detail)) return 'Операция не выполнена.';
  return detail.map(({ loc = [], type, msg }) => {
    if (type === 'value_error') return msg.replace(/^Value error, /, '');
    const field = fields[loc.at(-1)];
    const message = {
      missing: 'заполните обязательное поле',
      string_too_short: 'заполните поле',
      string_too_long: 'значение слишком длинное',
      decimal_max_places: 'укажите не более двух знаков после запятой',
      decimal_max_digits: 'число слишком велико',
      greater_than: 'значение должно быть больше нуля',
      date_from_datetime_parsing: 'проверьте дату',
      enum: 'выберите значение из списка',
    }[type] || 'проверьте введённое значение';
    return field ? `${field} — ${message}.` : 'Проверьте введённые данные.';
  }).join(' ');
}

export async function api(path, { params, method = 'GET', data, signal } = {}) {
  const query = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== '' && value != null) query.set(key, value);
  });
  let response;
  try {
    response = await fetch(`/api/${path}${query.size ? `?${query}` : ''}`, {
      method, signal,
      headers: data ? { 'Content-Type': 'application/json' } : undefined,
      body: data ? JSON.stringify(data) : undefined,
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Сервер недоступен. Проверьте подключение и повторите попытку.');
  }
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => null);
  if (!response.ok || payload == null) throw new Error(describe(payload?.detail));
  return payload;
}
