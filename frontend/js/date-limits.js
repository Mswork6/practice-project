import { date, today } from './ui.js';

export const DATE_FLOOR = '1900-01-01';

// Calendar years: February 29 becomes the last day of February.
export function addYears(day, years) {
  const [year, month, dayOfMonth] = day.split('-').map(Number);
  const last = new Date(Date.UTC(year + years, month, 0)).getUTCDate();
  return `${String(year + years).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(Math.min(dayOfMonth, last)).padStart(2, '0')}`;
}

// Inclusive upper bounds for one form, relative to the application date.
export function dateLimits() {
  const day = today();
  return { day, soon: addYears(day, 10), distant: addYears(day, 100) };
}

export const notEarlier = label => `${label} не может быть ранее ${date(DATE_FLOOR)}.`;
export const notLater = (label, bound) => `${label} не может быть позднее ${date(bound)}.`;
