import { api } from './api.js';
import { debounce, escape, options, reveal } from './ui.js';

const dialog = document.querySelector('#dialog');
const title = dialog.querySelector('#dialog-title');
const content = dialog.querySelector('#dialog-content');
let panel = 0;
let opener;
let dismissOnBackdrop = false;
let backdropPress = false;

function isBackdrop(event) {
  if (event.target !== dialog) return false;
  const { left, right, top, bottom } = dialog.getBoundingClientRect();
  return event.clientX < left || event.clientX >= right || event.clientY < top || event.clientY >= bottom;
}

dialog.querySelector('#dialog-close').addEventListener('click', closeDialog);
dialog.addEventListener('pointerdown', (event) => {
  backdropPress = event.isPrimary && event.button === 0 && isBackdrop(event);
});
dialog.addEventListener('pointercancel', () => { backdropPress = false; });
dialog.addEventListener('click', (event) => {
  const shouldDismiss = dismissOnBackdrop && backdropPress && event.detail > 0 && isBackdrop(event);
  backdropPress = false;
  if (shouldDismiss) closeDialog();
});
dialog.addEventListener('close', () => {
  if (dialog.open) return;
  panel++;
  if (opener?.isConnected) opener.focus();
});

export function closeDialog() {
  if (dialog.open) dialog.close();
}

export function openDialog(heading, html, { backdrop = true } = {}) {
  const id = ++panel;
  dismissOnBackdrop = backdrop;
  backdropPress = false;
  title.textContent = heading;
  content.innerHTML = html;
  if (!dialog.open) {
    opener = document.activeElement;
    dialog.showModal();
  } else {
    reveal(content);
  }
  function focusContent() {
    const initial = content.querySelector('[autofocus]') || dialog.querySelector('#dialog-close');
    initial.focus();
  }
  focusContent();
  const current = () => id === panel && dialog.open;
  return {
    content, current,
    set(newHeading, newHtml) {
      if (!current()) return false;
      title.textContent = newHeading;
      content.innerHTML = newHtml;
      focusContent();
      reveal(content);
      return true;
    },
    close() {
      if (current()) closeDialog();
    },
  };
}

export function dialogError(view, error, heading = 'Запись недоступна') {
  view.set(heading, `<p class="form-error" role="alert">${escape(error.message)}</p>`);
}

export function fieldsTable(fields) {
  return `<dl class="detail-fields">${fields.map(([label, value, wide]) => `
    <div${wide ? ' class="full-width"' : ''}><dt>${escape(label)}</dt><dd>${escape(value ?? '—')}</dd></div>`).join('')}</dl>`;
}

export function field(name, label, value, input = '', wide = false) {
  return `<label${wide ? ' class="full-width"' : ''}><span>${escape(label)}</span>
    <input name="${name}" value="${escape(value)}" ${input}></label>`;
}

export function selectField(name, label, values, current) {
  return `<label><span>${escape(label)}</span><select name="${name}" required>${options(values, current)}</select></label>`;
}

export function noteField(name, label, value) {
  return `<label class="full-width"><span>${escape(label)}</span><textarea name="${name}" maxlength="3000">${escape(value)}</textarea></label>`;
}

export function editView(heading, fields, submitLabel = 'Сохранить') {
  return openDialog(heading, `<form><div class="form-grid">${fields}</div>
    <p class="form-error" role="alert" hidden></p>
    <div class="form-actions"><button type="button" class="button" data-cancel>Отмена</button>
    <button type="submit" class="button primary">${submitLabel}</button></div></form>`, { backdrop: false });
}

export function formError(form, message) {
  const error = form.querySelector(':scope > .form-error');
  error.textContent = message;
  error.hidden = !message;
}

export function bindForm(view, onSubmit) {
  const form = view.content.querySelector('form');
  const submitButton = form.querySelector('[type="submit"]');
  form.querySelector('[data-cancel]').addEventListener('click', view.close);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submitButton.disabled) return;
    formError(form, '');
    submitButton.disabled = true;
    try {
      const values = new FormData(form);
      await onSubmit(values);
      view.close();
    } catch (error) {
      formError(form, error.message);
    } finally {
      submitButton.disabled = false;
    }
  });
  return form;
}

export function confirmDelete(message, onDelete) {
  const view = editView('Подтверждение удаления', `
    <div class="confirmation full-width"><p>${escape(message)}</p></div>`, 'Удалить');
  const form = bindForm(view, onDelete);
  form.querySelector('[data-cancel]').focus();
}

export function relatedTable(container, { endpoint, params, columns, renderRow, empty, onClick }) {
  let page = 1;
  async function load() {
    container.setAttribute('aria-busy', 'true');
    container.innerHTML = '<p class="related-message" role="status">Загрузка…</p>';
    try {
      const result = await api(endpoint, { params: { ...params, page, page_size: 8 } });
      if (!container.isConnected) return;
      if (!result.total) {
        container.innerHTML = `<p class="related-message">${escape(empty)}</p>`;
        return;
      }
      const pages = Math.ceil(result.total / result.page_size);
      container.innerHTML = `<div class="table-wrap"><table><thead><tr>${columns.map((label) => `<th scope="col">${escape(label)}</th>`).join('')}</tr></thead>
        <tbody>${result.items.map(renderRow).join('')}</tbody></table>
        ${pages > 1 ? `<div class="pagination"><span>Страница ${page} из ${pages}</span><div class="pagination-actions">
        <button class="button small" type="button" data-step="-1"${page === 1 ? ' disabled' : ''}>Назад</button>
        <button class="button small" type="button" data-step="1"${page === pages ? ' disabled' : ''}>Далее</button></div></div>` : ''}</div>`;
      container.querySelectorAll('[data-step]').forEach((button) => button.addEventListener('click', () => {
        page += Number(button.dataset.step);
        load();
      }));
      container.querySelectorAll('[data-record]').forEach((button) => button.addEventListener('click', () => onClick(Number(button.dataset.record))));
    } catch (error) {
      container.innerHTML = `<p class="related-message" role="alert">${escape(error.message)} <button type="button" class="button small">Повторить</button></p>`;
      container.querySelector('button').addEventListener('click', load);
    } finally {
      container.setAttribute('aria-busy', 'false');
    }
  }
  load();
}

export function lookup(name, label, searchHint) {
  return `<div class="lookup${name === 'contract_id' ? ' full-width' : ''}" data-lookup="${name}">
    <label><span>Поиск · ${escape(label.toLowerCase())}</span><input type="search" maxlength="200" placeholder="${escape(searchHint)}"></label>
    <label><span>${escape(label)}</span><select name="${name}" required aria-describedby="${name}-hint"></select></label>
    <span class="lookup-hint" id="${name}-hint" aria-live="polite"></span>
  </div>`;
}

export function bindLookup(form, name, endpoint, label, current, filters = () => ({})) {
  const group = form.querySelector(`[data-lookup="${name}"]`);
  const search = group.querySelector('input');
  const select = group.querySelector('select');
  const hint = group.querySelector('.lookup-hint');
  let retained = current;
  let request;

  function populate(items, selected) {
    const choices = Object.fromEntries(items.map((item) => [item.id, label(item)]));
    if (retained && !Object.hasOwn(choices, retained.id)) choices[retained.id] = label(retained);
    select.innerHTML = options(choices, selected, 'Выберите из списка');
    select.value = selected || '';
  }

  async function load() {
    request?.abort();
    request = new AbortController();
    const { signal } = request;
    hint.textContent = 'Поиск…';
    try {
      const { items, total } = await api(endpoint, {
        params: { search: search.value, page_size: 30, ...filters() }, signal,
      });
      if (signal.aborted || !group.isConnected) return;
      populate(items, select.value);
      hint.textContent = total === 0 ? 'Ничего не найдено' : total > items.length ? 'Уточните поиск, чтобы увидеть другие записи' : '';
      select.onchange = () => {
        if (!select.value) {
          retained = null;
        } else {
          retained = items.find((item) => String(item.id) === select.value) ?? retained;
        }
      };
    } catch (error) {
      if (!signal.aborted) hint.textContent = error.message;
    }
  }

  populate([], retained?.id);
  search.addEventListener('input', debounce(load));
  load();
  return (item = null) => {
    retained = item;
    search.value = '';
    populate([], item?.id);
    load();
  };
}
