import { escape, icon, options } from './ui.js';
import { pagedTable } from './paged-table.js';

export const recordButton = (record, label) => `<button type="button" class="text-button" data-record="${record.id}">${escape(label)}</button>`;

export function mountRegistry(root, config, initial) {
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
        ${config.filterControls ?? ''}
      </form>
      <div class="registry-results"></div>
    </section>`;
  const filters = root.querySelector('.filters');
  const results = root.querySelector('.registry-results');
  config.setupFilters?.(filters, initial, () => reset());
  const table = pagedTable(results, {
    ...config,
    params: () => Object.fromEntries(new FormData(filters)),
    empty: 'Записи не найдены.',
    onClick: (id) => config.detail(id, table.load),
    sortValue: () => filters.elements.sort?.value ?? '',
    onSort: (value) => { filters.elements.sort.value = value; reset(); },
  });
  let searchTimer;

  function reset() {
    clearTimeout(searchTimer);
    table.reset();
  }
  filters.addEventListener('submit', (event) => { event.preventDefault(); reset(); });
  filters.elements.search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    table.cancel();
    searchTimer = setTimeout(reset, 250);
  });
  filters.querySelectorAll('select').forEach((select) => select.addEventListener('change', reset));
  root.querySelector('#add-record').addEventListener('click', () => config.edit(null, table.load));
  return () => { clearTimeout(searchTimer); table.cancel(); };
}
