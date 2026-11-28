import { escape, options, reveal } from './ui.js';
import { closeLookup } from './lookup.js';

export { bindLookup, lookup } from './lookup.js';
export { relatedTable } from './paged-table.js';

const dialog = document.querySelector('#dialog');
const title = dialog.querySelector('#dialog-title');
const host = dialog.querySelector('#dialog-content');
const closeButton = dialog.querySelector('#dialog-close');
const frames = [];
let backdropPress = false;
let retiring;
let sizing;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const top = () => frames.at(-1);

function resizeFrom(height) {
  sizing?.cancel();
  const target = dialog.getBoundingClientRect().height;
  if (!height || reducedMotion.matches || Math.abs(target - height) < 1) return;
  sizing = dialog.animate({ height: [`${height}px`, `${target}px`] }, { duration: 160, easing: 'ease-out' });
}

function isBackdrop(event) {
  if (event.target !== dialog) return false;
  const { left, right, top, bottom } = dialog.getBoundingClientRect();
  return event.clientX < left || event.clientX >= right || event.clientY < top || event.clientY >= bottom;
}

closeButton.addEventListener('click', dismiss);
dialog.addEventListener('pointerdown', (event) => {
  backdropPress = event.isPrimary && event.button === 0 && isBackdrop(event);
});
dialog.addEventListener('pointercancel', () => { backdropPress = false; });
dialog.addEventListener('click', (event) => {
  const outside = backdropPress && event.detail > 0 && isBackdrop(event);
  backdropPress = false;
  if (outside) dismiss();
});
dialog.addEventListener('cancel', (event) => {
  event.preventDefault();
  if (!closeLookup()) dismiss();
});

function activate(frame, focus, scrollTop = 0) {
  title.textContent = frame.heading;
  closeButton.disabled = frame.busy;
  frame.content.hidden = false;
  (focus?.isConnected ? focus : frame.content.querySelector('[autofocus]') ?? closeButton).focus({ preventScroll: true });
  dialog.scrollTop = scrollTop;
}

function pop(count = 1, changed = false) {
  closeLookup();
  const height = dialog.getBoundingClientRect().height;
  const outgoing = top();
  let removed;
  for (let i = 0; i < count && frames.length; i++) {
    removed = frames.pop();
    if (removed !== outgoing) removed.content.remove();
  }
  if (changed) frames.forEach((frame) => { frame.stale = true; });

  const parent = top();
  if (!parent) {
    sizing?.cancel();
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
  resizeFrom(height);
  reveal(parent.content);
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
  if (frames.some((frame) => frame.busy)) return false;
  if (frames.some((frame) => frame.dirty?.())) {
    if (top()?.dirty?.()) dismiss();
    return false;
  }
  if (frames.length) pop(frames.length);
  return true;
}

export function openDialog(heading, html) {
  closeLookup();
  retiring?.remove();
  retiring = null;
  const parent = top();
  const height = parent ? dialog.getBoundingClientRect().height : 0;
  const frame = { heading, opener: document.activeElement, content: document.createElement('div'), busy: false };
  frame.content.innerHTML = html;
  if (parent) {
    if (frame.content.querySelector(':scope > [role=status]')) {
      frame.content.style.minHeight = `${Math.min(parent.content.offsetHeight, host.clientHeight, height * .75)}px`;
    }
    parent.scrollTop = dialog.scrollTop;
    parent.content.hidden = true;
  }
  host.append(frame.content);
  frames.push(frame);
  if (!dialog.open) dialog.showModal();
  activate(frame);
  if (parent) { resizeFrom(height); reveal(frame.content); }

  return {
    content: frame.content,
    current: () => frames.includes(frame),
    set(newHeading, newHtml) {
      if (!frames.includes(frame)) return false;
      frame.heading = newHeading;
      const active = top() === frame;
      const height = active ? dialog.getBoundingClientRect().height : 0;
      const restore = active && frame.content.contains(document.activeElement);
      frame.content.innerHTML = newHtml;
      frame.content.style.minHeight = '';
      if (active) {
        title.textContent = newHeading;
        if (restore) closeButton.focus({ preventScroll: true });
        resizeFrom(height);
        reveal(frame.content);
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

export function editView(heading, fields, submitLabel = 'Сохранить', cancelLabel = 'Отмена') {
  return openDialog(heading, `<form><div class="form-grid">${fields}</div>
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
    view.setBusy(true);
    submitButton.disabled = true;
    cancel.disabled = true;
    try {
      await onSubmit(new FormData(form));
      view.close({ closeParent, changed });
    } catch (error) {
      formError(form, error.message);
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
