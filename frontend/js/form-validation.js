let sequence = 0;

const requiredMessages = {
  name: 'Укажите наименование.',
  address: 'Укажите адрес объекта.',
  type: 'Выберите значение из списка.',
  number: 'Укажите номер договора.',
  tax_id: 'Укажите ИНН контрагента.',
  start_date: 'Укажите дату начала договора.',
  end_date: 'Укажите дату окончания договора.',
  accrual_date: 'Укажите дату начисления.',
  due_date: 'Укажите срок оплаты.',
  payment_date: 'Укажите дату платежа.',
  property_id: 'Выберите объект из списка.',
  counterparty_id: 'Выберите контрагента из списка.',
  contract_id: 'Выберите договор из списка.',
};

function taxError(value) {
  const tax = value.replace(/[\s-]/gu, '');
  if (!tax) return requiredMessages.tax_id;
  if (!/^(?:[0-9]{10}|[0-9]{12})$/.test(tax)) return 'ИНН должен содержать 10 или 12 цифр.';
  const digit = (weights) => weights.reduce((sum, weight, index) => sum + weight * Number(tax[index]), 0) % 11 % 10;
  const legal = tax.length === 10;
  const first = legal ? [2, 4, 10, 3, 5, 9, 4, 6, 8] : [7, 2, 4, 10, 3, 5, 9, 4, 6, 8];
  if (!/[1-9]/.test(tax) || digit(first) !== Number(tax[first.length]) ||
      (!legal && digit([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) !== Number(tax[11]))) {
    return 'Некорректный ИНН. Проверьте введённые цифры.';
  }
}

function emailError(value) {
  const [local, domain, extra] = value.split('@');
  const atom = "[A-Za-z0-9!#$%&'*+/=?^_\x60{|}~-]+";
  const localPattern = new RegExp(`^${atom}(?:\\.${atom})*$`);
  const labels = domain?.replace(/[。．｡]/gu, '.').split('.') ?? [];
  const labelPattern = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
  if (extra !== undefined || local.length > 64 || !localPattern.test(local) || labels.length < 2 ||
      /^[0-9]+$/.test(labels.at(-1)) || labels.some(label => /[^\x00-\x7F]/u.test(label) ?
        /[\s/@#:%?\\]/u.test(label) : !labelPattern.test(label))) {
    return 'Укажите корректный адрес электронной почты.';
  }
}

function cents(value) {
  const [whole, fraction = ''] = value.split('.');
  return BigInt(whole || '0') * 100n + BigInt(fraction.padEnd(2, '0'));
}

function decimalError(value, limit) {
  if (!/^\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/.test(value) || !/[1-9]/.test(value)) {
    return 'Введите положительное число.';
  }
  const [whole, fraction = ''] = value.replace(/^\+/, '').split('.');
  if (fraction.length > 2) return 'Укажите не более двух знаков после десятичного разделителя.';
  if (whole.replace(/^0+/, '').length > 12) return 'Допустимо не более 12 цифр до десятичного разделителя.';
  if (limit != null && cents(value.replace(/^\+/, '')) > cents(limit)) {
    return 'Сумма превышает доступный остаток начисления.';
  }
}

function check(control, name, value) {
  if (control.validity.badInput) return 'Укажите корректную календарную дату.';
  if (!value || control.validity.customError) {
    return control.required ? requiredMessages[name] ??
      (control.hasAttribute('data-decimal') ? 'Введите положительное число.' : 'Заполните обязательное поле.') : '';
  }
  // maxlength counts UTF-16 units natively, while the API measures Unicode code points.
  if (control.maxLength >= 0 && [...value].length > control.maxLength) {
    return `Допустимо не более ${control.maxLength} символов.`;
  }
  if (control.hasAttribute('data-decimal')) return decimalError(value, control.dataset.limit);
  if (name === 'tax_id') return taxError(value);
  if (name === 'email') return emailError(value);
  if (name === 'cadastral_number' && !/^[0-9]+:[0-9]+:[0-9]+:[0-9]+$/.test(value)) {
    return 'Некорректный формат кадастрового номера.';
  }
  if (control.type === 'date') {
    if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)) return 'Укажите корректную календарную дату.';
    if (control.validity.rangeUnderflow) return control.dataset.minMessage ?? 'Укажите более позднюю дату.';
    if (control.validity.rangeOverflow) return control.dataset.maxMessage ?? 'Укажите более раннюю дату.';
  }
  if (control instanceof HTMLSelectElement && ![...control.options].some(option => option.value === value)) {
    return 'Выберите значение из списка.';
  }
}

export function updateRequiredMark(control) {
  const mark = control.closest('label')?.querySelector('[data-required-mark]');
  if (mark) mark.hidden = !control.required;
}

export function bindValidation(form) {
  const fields = new Map();
  form.querySelectorAll('.form-grid input:not([type=hidden]), .form-grid select, .form-grid textarea').forEach(control => {
    const lookup = control.closest('[data-lookup]');
    const name = lookup?.dataset.lookup ?? control.name;
    const container = lookup ?? control.closest('label');
    const id = `form-error-${++sequence}`;
    const message = document.createElement('span');
    message.id = id;
    message.className = 'field-error';
    message.hidden = true;
    message.setAttribute('role', 'alert');
    container.append(message);
    if (!lookup) {
      const label = container.firstElementChild;
      label.id = `${id}-label`;
      control.setAttribute('aria-labelledby', label.id);
      updateRequiredMark(control);
    }
    const description = control.getAttribute('aria-describedby');
    function show(text = '') {
      message.hidden = !text;
      message.textContent = text;
      if (text) {
        control.setAttribute('aria-invalid', 'true');
        control.setAttribute('aria-describedby', [description, id].filter(Boolean).join(' '));
      } else {
        control.removeAttribute('aria-invalid');
        if (description) control.setAttribute('aria-describedby', description);
        else control.removeAttribute('aria-describedby');
      }
    }
    control.addEventListener('input', () => show());
    control.addEventListener('change', () => show());
    fields.set(name, { control, show });
  });

  return {
    check(values) {
      let first;
      for (const [name, { control, show }] of fields) {
        let value = values.get(name).trim();
        if (control.hasAttribute('data-decimal')) value = value.replace(',', '.');
        values.set(name, value);
        const message = check(control, name, value);
        show(message);
        if (message) first ??= control;
      }
      first?.focus();
      return !first;
    },
    showServer(error) {
      if (!error.errors?.length) return error.message;
      const general = [];
      for (const { loc, msg } of error.errors) {
        const field = loc[0] === 'body' && fields.get(loc[1]);
        if (field) field.show(msg);
        else general.push(msg);
      }
      return general.join(' ');
    },
  };
}
