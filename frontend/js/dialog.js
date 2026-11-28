import { api } from './api.js';
import { escape, notify, options, reveal } from './ui.js';
import { closeLookup } from './lookup.js';
import { bindValidation } from './form-validation.js';
import { relatedPage } from './paged-table.js';

export { bindLookup, lookup } from './lookup.js';
export { relatedTable } from './paged-table.js';

const dialog = document.querySelector('#dialog');
const title = dialog.querySelector('#dialog-title');
const host = dialog.querySelector('#dialog-content');
const closeButton = dialog.querySelector('#dialog-close');
const loading = document.querySelector('#detail-loading');
const feedback = dialog.querySelector('#dialog-feedback');
const frames = [];
let backdropPress = false;
let retiring;
let pending;

const top = () => frames.at(-1);

function cancelDetail(abort = true) {
  if (!pending) return;
  if (abort) pending.controller.abort();
  clearTimeout(pending.timer);
  if (pending.opener instanceof HTMLElement) pending.opener.removeAttribute('aria-busy');
  loading.hidden = true;
  feedback.hidden = true;
  pending = null;
}

export async function loadDetail(path, related, render, view) {
  const parent = top();
  if (view && !view.current()) return;
  if (pending?.path === path && pending.parent === parent && pending.view === view) return;
  cancelDetail();
  const controller = new AbortController();
  const { signal } = controller;
  const opener = document.activeElement;
  const sourceTable = opener?.closest('.table-body');
  feedback.hidden = true;
  const timer = setTimeout(() => {
    const status = parent ? feedback : loading;
    status.textContent = 'Загрузка карточки…';
    status.hidden = false;
  }, 180);
  pending = { path, parent, view, controller, opener, timer };
  if (opener instanceof HTMLElement) opener.setAttribute('aria-busy', 'true');
  const hash = location.hash;
  try {
    const [record, page] = await Promise.all([
      api(path, { signal }),
      related ? relatedPage(related.endpoint, related.params, signal) : null,
    ]);
    const liveOpener = opener?.isConnected ? opener :
      sourceTable?.querySelector(`[data-record="${CSS.escape(opener.dataset.record)}"]`);
    if (signal.aborted || hash !== location.hash || top() !== parent || !liveOpener?.isConnected) return;
    cancelDetail(false);
    const scrollTop = host.scrollTop;
    const focusKey = view?.content.contains(opener) && opener.getAttributeNames().find(name => name.startsWith('data-'));
    render(record, page, (heading, html) => {
      const card = view ?? openDialog(heading, html, liveOpener);
      if (view) view.set(heading, html);
      card.refresh(() => loadDetail(path, related, render, card));
      return card;
    });
    if (view) {
      if (focusKey) {
        [...view.content.querySelectorAll(`[${CSS.escape(focusKey)}]`)]
          .find(element => element.getAttribute(focusKey) === opener.getAttribute(focusKey))
          ?.focus({ preventScroll: true });
      }
      host.scrollTop = scrollTop;
    }
  } catch (error) {
    if (signal.aborted || hash !== location.hash || top() !== parent) return;
    cancelDetail();
    if (parent) {
      feedback.textContent = error.message;
      feedback.hidden = false;
    } else {
      notify(error.message);
    }
  } finally {
    if (pending?.controller === controller) cancelDetail();
  }
}

function isBackdrop(event) {
  if (event.target !== dialog) return false;
  const { left, right, top, bottom } = dialog.getBoundingClientRect();
  return event.clientX < left || event.clientX >= right || event.clientY < top || event.clientY >= bottom;
}

closeButton.addEventListener('click', dismiss);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && pending && !dialog.open) cancelDetail();
});
dialog.addEventListener('pointerdown', (event) => {
  backdropPress = event.isPrimary && event.button === 0 && isBackdrop(event);
});
dialog.addEventListener('pointercancel', () => { backdropPress = false; });
dialog.addEventListener('click', (event) => {
  const outside = backdropPress && event.detail > 0 && isBackdrop(event);
  backdropPress = false;
  if (outside) dismiss();
});
dialog.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  if (event.target instanceof HTMLSelectElement && CSS.supports('selector(select:open)') && event.target.matches(':open')) return;
  event.preventDefault();
  if (!event.repeat && !closeLookup()) dismiss();
});
dialog.addEventListener('cancel', (event) => {
  event.preventDefault();
  if (!closeLookup()) dismiss();
});

function activate(frame, focus, scrollTop = 0) {
  feedback.hidden = true;
  title.textContent = frame.heading;
  closeButton.disabled = frame.busy;
  frame.content.hidden = false;
  if (!dialog.open) dialog.showModal();
  (focus?.isConnected ? focus : frame.content.querySelector('[autofocus]') ?? closeButton).focus({ preventScroll: true });
  host.scrollTop = scrollTop;
}

function pop(count = 1, changed = false) {
  closeLookup();
  cancelDetail();
  const outgoing = top();
  let removed;
  for (let i = 0; i < count && frames.length; i++) {
    removed = frames.pop();
    if (removed !== outgoing) removed.content.remove();
  }
  if (changed) frames.forEach((frame) => { frame.stale = true; });

  const parent = top();
  if (!parent) {
    retiring = outgoing.content;
    const node = retiring;
    node.inert = true;
    dialog.close();
    requestAnimationFrame(() => {
      Promise.allSettled(dialog.getAnimations().map(animation => animation.finished)).then(() => {
        node.remove();
        if (retiring === node) retiring = null;
      });
    });
    if (removed.opener?.isConnected) removed.opener.focus({ preventScroll: true });
    return;
  }
  outgoing.content.remove();
  activate(parent, removed.opener, parent.scrollTop);
  reveal(parent.content, { offset: 0 });
  if (parent.stale && parent.refresh) {
    parent.stale = false;
    parent.refresh();
  }
}

function dismiss() {
  const frame = top();
  if (!frame || frame.busy) return;
  if (frame.dirty?.()) {
    confirmAction(
      'Закрыть форму',
      'Внесённые изменения не сохранены. Вы действительно хотите закрыть окно?',
      'Выйти без сохранения',
      () => {},
      { cancelLabel: 'Продолжить редактирование', closeParent: true, changed: false },
    );
    return;
  }
  pop();
}

export function closeDialog() {
  cancelDetail();
  if (frames.some((frame) => frame.busy)) return false;
  if (frames.some((frame) => frame.dirty?.())) {
    if (top()?.dirty?.()) dismiss();
    return false;
  }
  if (frames.length) pop(frames.length);
  return true;
}

export function openDialog(heading, html, opener = document.activeElement) {
  closeLookup();
  cancelDetail();
  retiring?.remove();
  retiring = null;
  const parent = top();
  const frame = { heading, opener, content: document.createElement('div'), busy: false };
  frame.content.innerHTML = html;
  if (parent) {
    parent.scrollTop = host.scrollTop;
    parent.content.hidden = true;
  }
  host.append(frame.content);
  frames.push(frame);
  activate(frame);
  if (parent) reveal(frame.content, { offset: 0 });

  return {
    content: frame.content,
    current: () => frames.includes(frame),
    set(newHeading, newHtml) {
      if (!frames.includes(frame)) return false;
      frame.heading = newHeading;
      const active = top() === frame;
      const restore = active && frame.content.contains(document.activeElement);
      frame.content.innerHTML = newHtml;
      if (active) {
        title.textContent = newHeading;
        if (restore) closeButton.focus({ preventScroll: true });
        reveal(frame.content, { offset: 0 });
      }
      return true;
    },
    refresh(callback) { frame.refresh = callback; },
    guard(dirty) { frame.dirty = dirty; },
    setBusy(busy) {
      frame.busy = busy;
      frame.content.inert = busy;
      if (top() === frame) closeButton.disabled = busy;
    },
    busy: () => frame.busy,
    close({ closeParent = false, changed = true } = {}) {
      if (top() === frame) pop(closeParent ? 2 : 1, changed);
    },
  };
}

export function fieldsTable(fields) {
  return `<dl class="detail-fields">${fields.map(([label, value, wide]) => `
    <div${wide ? ' class="full-width"' : ''}><dt>${escape(label)}</dt><dd>${escape(value ?? '–')}</dd></div>`).join('')}</dl>`;
}

export function field(name, label, value, input = '', wide = false) {
  return `<label${wide ? ' class="full-width"' : ''}><span>${escape(label)}<span data-required-mark hidden> *</span></span>
    <input name="${name}" value="${escape(value)}" ${input}></label>`;
}

export function selectField(name, label, values, current) {
  return `<label><span>${escape(label)}<span data-required-mark hidden> *</span></span><select name="${name}" required>${options(values, current)}</select></label>`;
}

export function noteField(name, label, value) {
  return `<label class="full-width"><span>${escape(label)}</span><textarea name="${name}" maxlength="3000">${escape(value)}</textarea></label>`;
}

export function editView(heading, fields, submitLabel = 'Сохранить', cancelLabel = 'Отмена') {
  return openDialog(heading, `<form novalidate><div class="form-grid">${fields}</div>
    <p class="form-error" role="alert" hidden></p>
    <div class="form-actions"><button type="button" class="button" data-cancel>${cancelLabel}</button>
    <button type="submit" class="button primary">${submitLabel}</button></div></form>`);
}

export function formError(form, message) {
  const error = form.querySelector(':scope > .form-error');
  error.textContent = message;
  error.hidden = !message;
}

export function bindForm(view, onSubmit, { guard = true, closeParent = false, changed = true } = {}) {
  const form = view.content.querySelector('form');
  const submitButton = form.querySelector('[type="submit"]');
  const cancel = form.querySelector('[data-cancel]');
  const validation = bindValidation(form);
  const snapshot = () => JSON.stringify([...new FormData(form)]);
  if (guard) queueMicrotask(() => {
    const initial = snapshot();
    view.guard(() => snapshot() !== initial);
  });
  cancel.addEventListener('click', dismiss);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (view.busy()) return;
    formError(form, '');
    const values = new FormData(form);
    if (!validation.check(values)) return;
    view.setBusy(true);
    submitButton.disabled = true;
    cancel.disabled = true;
    try {
      await onSubmit(values);
      view.close({ closeParent, changed });
    } catch (error) {
      formError(form, validation.showServer(error));
    } finally {
      view.setBusy(false);
      submitButton.disabled = false;
      cancel.disabled = false;
      if (view.current() && document.activeElement === document.body) submitButton.focus({ preventScroll: true });
    }
  });
  return form;
}

export function confirmAction(heading, message, label, submit, {
  cancelLabel = 'Отмена', closeParent = false, changed = true,
} = {}) {
  const view = editView(heading, `
    <div class="confirmation full-width"><p>${escape(message)}</p></div>`, label, cancelLabel);
  const form = bindForm(view, submit, { guard: false, closeParent, changed });
  form.querySelector('[data-cancel]').focus();
}

export function confirmDelete(message, onDelete) {
  confirmAction('Подтверждение удаления', message, 'Удалить', onDelete, { closeParent: true });
}
