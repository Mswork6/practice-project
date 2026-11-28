const paths = {
  building: '<path d="m3 9 9-5 9 5M4 10h16M6 12v7m6-7v7m6-7v7M3 20h18"/>',
  dashboard: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  contract: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8m-8 4h8m-8 4h5"/>',
  wallet: '<path d="M20 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2M4 7a1 1 0 0 0 1 1h15a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-5a4 4 0 0 1 0-8"/><path d="M15 12h1"/>',
  calendar: '<rect x="4" y="6" width="16" height="14" rx="2"/><path d="M8 4v4m8-4v4M4 11h16m-12 4h3"/>',
  income: '<path d="M4 4v16h16M8 15l4-4 4 2 4-6m-4 0h4v4"/>',
  debt: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
  search: '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 4 4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  'chevron-right': '<path d="m9 5 7 7-7 7"/>',
};

export function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
}

export function escape(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

const rub = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2 });
const compactRub = new Intl.NumberFormat('ru-RU', { notation: 'compact', maximumFractionDigits: 2, style: 'currency', currency: 'RUB' });
const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const dateShort = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC' });

export const money = (value) => rub.format(value);
export const compactMoney = (value) => Math.abs(Number(value)) >= 1_000_000 ? compactRub.format(value) : money(value);
export const number = (value) => numbers.format(value);
export const date = (value) => value ? dateShort.format(new Date(`${value}T00:00:00Z`)) : '—';

export function today() {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Yekaterinburg' }).format(new Date());
}

export const propertyTypes = { building: 'Здание', land: 'Земельный участок', premises: 'Нежилое помещение', other: 'Прочее' };
export const propertyStatuses = { available: 'Свободен', in_use: 'Используется', archived: 'Архивирован' };
export const contractTypes = { real_estate_rent: 'Аренда недвижимости', land_rent: 'Аренда земли', sale: 'Продажа имущества', other: 'Прочие' };
export const contractStates = { pending: 'Ожидает начала', active: 'Действует', expired: 'Срок истёк',
  completed: 'Завершён', terminated: 'Расторгнут' };
export const badge = (label, tone = '') => `<span class="badge ${tone}">${escape(label)}</span>`;

export function options(items, selected, placeholder) {
  const empty = placeholder ? `<option value="">${escape(placeholder)}</option>` : '';
  return empty + Object.entries(items).map(([value, label]) => (
    `<option value="${escape(value)}"${String(selected) === value ? ' selected' : ''}>${escape(label)}</option>`
  )).join('');
}

export function setAppDate(value) {
  const element = document.querySelector('#app-date');
  const time = element.querySelector('time');
  time.dateTime = value;
  time.textContent = new Intl.DateTimeFormat('ru-RU', {
    timeZone: 'UTC', day: 'numeric', month: 'long', year: 'numeric',
  }).format(new Date(`${value}T00:00:00Z`));
  element.hidden = false;
}

let noticeTimer;
export function notify(message) {
  const notice = document.querySelector('#notice');
  notice.textContent = message;
  notice.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice.hidden = true; }, 4500);
}

export function debounce(callback, delay = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => callback(...args), delay);
  };
}

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const reveals = new WeakMap();

export function reveal(element, { duration = 190, offset = 5 } = {}) {
  reveals.get(element)?.cancel();
  if (reducedMotion.matches) return;
  const animation = element.animate(
    { opacity: [.55, 1], ...(offset ? { translate: [`0 ${offset}px`, '0 0'] } : {}) },
    { duration, easing: 'cubic-bezier(.2, .7, .25, 1)' },
  );
  reveals.set(element, animation);
}
