import { api } from './api.js';
import { debounce, escape, icon, number, options, reveal } from './ui.js';

export const recordButton = (record, label) => `<button type="button" class="text-button" data-record="${record.id}">${escape(label)}</button>`;

export function mountRegistry(root, config) {
  root.innerHTML = `
    <header class="page-header">
      <div><h1>${config.title}</h1><p class="subheading">${config.subtitle}</p></div>
      <button type="button" class="button primary" id="add-record">${icon('plus')}${config.addLabel}</button>
    </header>
    <section class="registry" aria-label="${config.title}">
      <form class="filters" role="search">
        <label class="search-control"><span>Поиск</span><span class="input-icon">
          <span>${icon('search')}</span><input name="search" type="search" maxlength="200" placeholder="${config.searchHint}">
        </span></label>
        ${config.filters.map(([field, label, values, placeholder]) => `<label>
          <span>${label}</span><select name="${field}">${options(values, '', placeholder)}</select></label>`).join('')}
      </form>
      <div class="registry-results" aria-live="polite"></div>
    </section>`;
  const filters = root.querySelector('.filters');
  const results = root.querySelector('.registry-results');
  let controller;
  let page = 1;

  async function load() {
    if (!results.isConnected) return;
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    results.setAttribute('aria-busy', 'true');
    results.innerHTML = '<p class="list-message" role="status">Загрузка записей…</p>';
    try {
      const params = { ...Object.fromEntries(new FormData(filters)), page };
      const data = await api(config.endpoint, { params, signal });
      if (signal.aborted) return;
      if (!data.total) {
        results.innerHTML = '<p class="list-message">Записи не найдены.</p>';
        reveal(results);
        return;
      }
      const pages = Math.ceil(data.total / data.page_size);
      if (page > pages) {
        page = pages;
        load();
        return;
      }
      results.innerHTML = `
        <div class="table-wrap"><table>
          <thead><tr>${config.columns.map((label) => `<th scope="col">${label}</th>`).join('')}</tr></thead>
          <tbody>${data.items.map(config.renderRow).join('')}</tbody>
        </table></div>
        <footer class="pagination">
          <span>Всего записей: ${number(data.total)}</span>
          <div class="pagination-actions">${pages > 1 ? `<button class="button small" type="button" data-step="-1"${page === 1 ? ' disabled' : ''}>Назад</button>
            <span>Страница ${page} из ${pages}</span>
            <button class="button small" type="button" data-step="1"${page === pages ? ' disabled' : ''}>Далее</button>` : ''}</div>
        </footer>`;
      results.querySelectorAll('[data-step]').forEach((button) => button.addEventListener('click', () => {
        page += Number(button.dataset.step);
        load();
      }));
      results.querySelectorAll('[data-record]').forEach((button) => button.addEventListener('click', () => {
        config.detail(Number(button.dataset.record), load);
      }));
      reveal(results);
    } catch (error) {
      if (signal.aborted) return;
      results.innerHTML = `<p class="list-message danger-text" role="alert">${escape(error.message)}
        <button type="button" class="button small">Повторить</button></p>`;
      results.querySelector('button').addEventListener('click', load);
      reveal(results);
    } finally {
      if (!signal.aborted) results.setAttribute('aria-busy', 'false');
    }
  }

  function reset() {
    page = 1;
    load();
  }
  filters.addEventListener('submit', (event) => { event.preventDefault(); reset(); });
  filters.elements.search.addEventListener('input', debounce(reset));
  filters.querySelectorAll('select').forEach((select) => select.addEventListener('change', reset));
  root.querySelector('#add-record').addEventListener('click', () => config.edit(null, load));
  load();
  return () => controller?.abort();
}
