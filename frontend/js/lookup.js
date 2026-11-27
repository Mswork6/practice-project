import { api } from './api.js';
import { debounce, escape } from './ui.js';

let sequence = 0;
let active;

export function closeLookup() {
  if (!active) return false;
  active();
  return true;
}

export function lookup(name, label, searchHint) {
  const id = `lookup-${++sequence}`;
  return `<div class="lookup${name === 'contract_id' ? ' full-width' : ''}" data-lookup="${name}">
    <label for="${id}" class="filter-label">${escape(label)}</label>
    <div class="lookup-control">
      <input id="${id}" type="text" role="combobox" autocomplete="off" maxlength="200" required
        aria-autocomplete="list" aria-expanded="false" aria-controls="${id}-list" placeholder="${escape(searchHint)}">
      <input type="hidden" name="${name}">
      <button type="button" class="lookup-clear" aria-label="Очистить выбор: ${escape(label)}" hidden>×</button>
    </div>
    <div class="lookup-popup" popover="manual" hidden>
      <ul id="${id}-list" role="listbox" aria-label="${escape(label)}"></ul>
      <p class="lookup-feedback" role="status" hidden></p>
    </div>
  </div>`;
}

export function bindLookup(form, name, endpoint, describe, initial, filters = () => ({})) {
  const group = form.querySelector(`[data-lookup="${name}"]`);
  const input = group.querySelector('[role=combobox]');
  const value = group.querySelector('input[type=hidden]');
  const clear = group.querySelector('.lookup-clear');
  const popup = group.querySelector('.lookup-popup');
  const list = popup.querySelector('ul');
  const feedback = popup.querySelector('.lookup-feedback');
  let selected = initial;
  let lifetime;
  let request;
  let page = 0;
  let cursor = -1;
  let hasMore = true;
  let highlightOnLoad = false;
  const items = new Map();

  function displaySelection() {
    input.value = selected ? describe(selected) : '';
    value.value = selected?.id ?? '';
    clear.hidden = !selected;
    input.setCustomValidity('');
  }

  function position() {
    const anchor = group.querySelector('.lookup-control').getBoundingClientRect();
    const dialog = group.closest('dialog')?.getBoundingClientRect();
    const visual = window.visualViewport;
    const left = Math.max(visual?.offsetLeft ?? 0, dialog?.left ?? 0) + 4;
    const top = Math.max(visual?.offsetTop ?? 0, dialog?.top ?? 0) + 4;
    const right = Math.min((visual?.offsetLeft ?? 0) + (visual?.width ?? innerWidth), dialog?.right ?? innerWidth) - 4;
    const bottom = Math.min((visual?.offsetTop ?? 0) + (visual?.height ?? innerHeight), dialog?.bottom ?? innerHeight) - 4;
    if (anchor.bottom < top || anchor.top > bottom) return close();
    const below = Math.max(0, bottom - anchor.bottom - 4);
    const above = Math.max(0, anchor.top - top - 4);
    const upward = below < 180 && above > below;
    const height = Math.min(250, upward ? above : below);
    if (height < 46) return close();
    const x = Math.max(left, Math.min(anchor.left, right - 30));
    popup.style.width = `${Math.min(anchor.width, right - x)}px`;
    popup.style.maxHeight = `${height}px`;
    popup.style.left = `${x}px`;
    popup.style.top = upward ? 'auto' : `${anchor.bottom + 4}px`;
    popup.style.bottom = upward ? `${innerHeight - anchor.top + 4}px` : 'auto';
  }

  function showFeedback(message) {
    feedback.textContent = message;
    feedback.hidden = !message;
  }

  function focusOption(index) {
    const old = list.children[cursor];
    if (old) old.dataset.highlighted = 'false';
    cursor = index;
    const target = list.children[cursor];
    if (!target) {
      input.removeAttribute('aria-activedescendant');
      return;
    }
    target.dataset.highlighted = 'true';
    input.setAttribute('aria-activedescendant', target.id);
    const offset = target.offsetTop;
    if (offset < popup.scrollTop) popup.scrollTop = offset;
    if (offset + target.offsetHeight > popup.scrollTop + popup.clientHeight) {
      popup.scrollTop = offset + target.offsetHeight - popup.clientHeight;
    }
  }

  async function load() {
    if (!lifetime || request || !hasMore) return;
    const controller = new AbortController();
    request = controller;
    const { signal } = controller;
    showFeedback('Загрузка…');
    list.setAttribute('aria-busy', 'true');
    try {
      const result = await api(endpoint, {
        params: { search: input.dataset.query, page: page + 1, page_size: 30, ...filters() }, signal,
      });
      if (signal.aborted || !lifetime || !group.isConnected) return;
      page = result.page;
      hasMore = page * result.page_size < result.total;
      for (const item of result.items) {
        if (items.has(item.id)) continue;
        items.set(item.id, item);
        const option = document.createElement('li');
        option.id = `${list.id}-${item.id}`;
        option.dataset.id = item.id;
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(item.id === selected?.id));
        option.textContent = describe(item);
        list.append(option);
      }
      showFeedback(items.size ? '' : 'Ничего не найдено');
      if (highlightOnLoad && items.size) focusOption(0);
      highlightOnLoad = false;
    } catch (error) {
      if (!signal.aborted) showFeedback(error.message);
    } finally {
      if (request === controller) {
        request = null;
        list.setAttribute('aria-busy', 'false');
      }
    }
  }

  function reset(query) {
    request?.abort();
    request = null;
    input.dataset.query = query;
    items.clear();
    list.replaceChildren();
    popup.scrollTop = 0;
    page = 0;
    hasMore = true;
    highlightOnLoad = false;
    focusOption(-1);
  }

  function close() {
    if (!lifetime) return;
    lifetime.abort();
    lifetime = null;
    request?.abort();
    request = null;
    active = null;
    if (popup.hidePopover && popup.matches(':popover-open')) popup.hidePopover();
    popup.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    displaySelection();
  }

  function open({ search = '' } = {}) {
    if (lifetime) return;
    closeLookup();
    lifetime = new AbortController();
    active = close;
    const { signal } = lifetime;
    popup.hidden = false;
    popup.showPopover?.();
    input.setAttribute('aria-expanded', 'true');
    position();
    if (!lifetime) return;
    document.addEventListener('pointerdown', (event) => {
      if (!group.contains(event.target)) close();
    }, { signal, capture: true });
    document.addEventListener('scroll', (event) => {
      if (event.target !== popup) position();
    }, { signal, capture: true });
    window.addEventListener('resize', position, { signal });
    window.visualViewport?.addEventListener('resize', position, { signal });
    window.visualViewport?.addEventListener('scroll', position, { signal });
    reset(search);
    load();
  }

  function choose(item) {
    selected = item;
    close();
    displaySelection();
  }

  const delayedLoad = debounce(load);
  input.addEventListener('click', () => {
    if (!lifetime) {
      open();
      input.select();
    }
  });
  input.addEventListener('input', () => {
    if (selected || input.value) input.setCustomValidity('Выберите запись из списка.');
    if (!lifetime) {
      open({ search: input.value });
      return;
    }
    reset(input.value);
    showFeedback('Поиск…');
    delayedLoad();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && lifetime) {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      open();
      if (!items.size) highlightOnLoad = true;
      const index = Math.max(0, Math.min(items.size - 1, cursor + (event.key === 'ArrowDown' ? 1 : -1)));
      focusOption(index);
      if (index >= items.size - 2) load();
    } else if (event.key === 'Enter' && lifetime) {
      event.preventDefault();
      const id = list.children[cursor]?.dataset.id;
      if (id) choose(items.get(Number(id)));
    }
  });
  group.addEventListener('focusout', (event) => {
    if (event.relatedTarget && !group.contains(event.relatedTarget)) close();
  });
  popup.addEventListener('scroll', () => {
    if (popup.scrollHeight - popup.scrollTop - popup.clientHeight < 44) load();
  });
  list.addEventListener('pointerdown', (event) => {
    if (event.pointerType === 'mouse' && event.target.closest('[data-id]')) event.preventDefault();
  });
  list.addEventListener('click', (event) => {
    const option = event.target.closest('[data-id]');
    if (option) {
      choose(items.get(Number(option.dataset.id)));
      input.focus({ preventScroll: true });
    }
  });
  clear.addEventListener('click', () => {
    choose(null);
    input.focus({ preventScroll: true });
    open();
  });

  displaySelection();
  return (item = null) => choose(item);
}
