import { api } from './api.js';
import { escape, number, reveal } from './ui.js';

function controls(data, position) {
  const { page, total, page_size: size } = data;
  const pages = Math.ceil(total / size);
  const button = (label, goto, attrs = '') =>
    `<button type="button" class="button small" data-page="${goto}" ${attrs}>${label}</button>`;
  const numbers = pages <= 7 ? Array.from({ length: pages }, (_, i) => i + 1)
    : page <= 4 ? [1, 2, 3, 4, 5, null, pages]
    : page >= pages - 3 ? [1, null, pages - 4, pages - 3, pages - 2, pages - 1, pages]
    : [1, null, page - 1, page, page + 1, null, pages];
  const links = numbers.map(index => index === null ? '<span aria-hidden="true">…</span>' :
    button(index, index, `aria-label="Страница ${index}"${index === page ? ' aria-current="page"' : ''}`),
  ).join('');
  return `<nav class="pagination" aria-label="Страницы записей, ${position}">
    <span>Всего записей: ${number(total)} · Страница ${page} из ${pages}</span>
    ${pages > 1 ? `<div class="pagination-actions">
      ${button('Назад', page - 1, page === 1 ? 'disabled' : '')}<div class="page-slots" style="--page-digits:${String(pages).length}">${links}</div>
      ${button('Далее', page + 1, page === pages ? 'disabled' : '')}
    </div>` : ''}
  </nav>`;
}

export function pagedTable(container, { endpoint, params, columns, renderRow, empty, onClick, pageSize,
  nested = false, sortable = {}, sortValue, onSort }) {
  container.innerHTML = `<div class="table-feedback" aria-live="polite" hidden></div>
    <div class="table-start" tabindex="-1"></div><div class="table-body"></div><div data-bottom></div>`;
  const status = container.querySelector('.table-feedback');
  const start = container.querySelector('.table-start');
  const body = container.querySelector('.table-body');
  const bottom = container.querySelector('[data-bottom]');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let controller;
  let page = 1;
  let focusAfter;

  function feedback(message, error = false) {
    status.hidden = !message;
    status.classList.toggle('danger-text', error);
    status.textContent = message;
    if (error) {
      const retry = document.createElement('button');
      retry.className = 'button small';
      retry.type = 'button';
      retry.textContent = 'Повторить';
      retry.dataset.retry = '';
      status.append(retry);
    }
  }

  function scrollToStart() {
    const behavior = reducedMotion.matches ? 'instant' : 'smooth';
    const dialog = container.closest('dialog');
    if (dialog) {
      const top = dialog.scrollTop + start.getBoundingClientRect().top - dialog.getBoundingClientRect().top - 12;
      dialog.scrollTo({ top, behavior });
    } else {
      start.scrollIntoView({ block: 'start', behavior });
    }
  }

  function draw(result) {
    const populated = result.total > 0;
    const previous = document.activeElement?.closest('[data-record]');
    const restoreRecord = body.contains(previous) ? previous.dataset.record : null;
    const previousSort = document.activeElement?.dataset.sort?.split('_')[0];
    start.innerHTML = populated && (!nested || result.total > result.page_size) ? controls(result, 'сверху') : '';
    bottom.innerHTML = populated ? controls(result, 'снизу') : '';
    body.innerHTML = populated ? `<div class="table-wrap"><table>
      <thead><tr>${columns.map(label => {
        const key = sortable[label];
        if (!key) return `<th scope="col">${escape(label)}</th>`;
        const direction = sortValue().split('_');
        const current = direction[0] === key ? direction[1] : '';
        const order = current === 'asc' ? 'ascending' : current === 'desc' ? 'descending' : 'none';
        return `<th scope="col" aria-sort="${order}"><button type="button" class="text-button table-sort"
          data-sort="${key}_${current === 'asc' ? 'desc' : 'asc'}">${escape(label)} <span aria-hidden="true">${current === 'asc' ? '↑' : current === 'desc' ? '↓' : '↕'}</span></button></th>`;
      }).join('')}</tr></thead>
      <tbody>${result.items.map(renderRow).join('')}</tbody>
    </table></div>` : `<p class="list-message">${escape(empty)}</p>`;
    reveal(body, { offset: 0, duration: 170 });
    if (focusAfter && (container.contains(document.activeElement) || document.activeElement === document.body)) {
      (start.querySelector('[aria-current]') || start).focus({ preventScroll: true });
    } else if (restoreRecord) {
      (body.querySelector(`[data-record="${restoreRecord}"]`) || start).focus({ preventScroll: true });
    } else if (previousSort) {
      body.querySelector(`[data-sort^="${previousSort}_"]`)?.focus({ preventScroll: true });
    }
    focusAfter = false;
  }

  async function load() {
    if (!container.isConnected) return;
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    feedback('Загрузка…');
    container.setAttribute('aria-busy', 'true');
    try {
      const result = await api(endpoint, { params: { ...params(), page, page_size: pageSize }, signal });
      if (signal.aborted || !container.isConnected) return;
      const pages = Math.max(1, Math.ceil(result.total / result.page_size));
      if (page > pages) {
        page = pages;
        load();
        return;
      }
      draw(result);
      feedback('');
    } catch (error) {
      if (signal.aborted || !container.isConnected) return;
      feedback(error.message, true);
    } finally {
      if (!signal.aborted) container.setAttribute('aria-busy', 'false');
    }
  }

  container.addEventListener('click', event => {
    const sort = event.target.closest('[data-sort]');
    if (sort) onSort(sort.dataset.sort);
    const jump = event.target.closest('[data-page]');
    if (jump && !jump.disabled) {
      const next = Number(jump.dataset.page);
      if (next === page) return;
      page = next;
      focusAfter = true;
      if (bottom.contains(jump)) scrollToStart();
      load();
    }
    const row = event.target.closest('[data-record]');
    if (row) onClick?.(Number(row.dataset.record));
    if (event.target.closest('[data-retry]')) load();
  });

  load();
  return {
    load,
    reset() { page = 1; focusAfter = false; load(); },
    cancel() { controller?.abort(); },
  };
}

export function relatedTable(container, config) {
  return pagedTable(container, { ...config, params: () => config.params, pageSize: 8, nested: true });
}
