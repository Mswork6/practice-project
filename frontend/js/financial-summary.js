import { money } from './ui.js';

export function financialSummary(finance, compact = false) {
  if (!finance) return '';
  const items = [
    ...(!compact ? [['Всего начислено', finance.billed, '']] : []),
    ['Всего оплачено', finance.paid, 'positive-text'],
    ...(!compact ? [['Остаток к оплате', finance.remaining, 'accent-text']] : []),
    ['Просроченная задолженность', finance.overdue, Number(finance.overdue) > 0 ? 'danger-text' : ''],
  ];
  return `<section class="related"><h3>Финансовая информация</h3><dl class="finance-summary">${items.map(
    ([label, value, tone]) => `<div><dt>${label}</dt><dd class="${tone}">${money(value)}</dd></div>`,
  ).join('')}</dl></section>`;
}
