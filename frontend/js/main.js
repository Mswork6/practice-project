import { mountDashboard } from './dashboard.js';
import { contracts } from './contracts.js';
import { closeDialog } from './dialog.js';
import { finance } from './finance.js';
import { properties } from './properties.js';
import { mountRegistry } from './registry.js';
import { icon, setAppDate, today } from './ui.js';

const routes = { properties, contracts, finance };
const root = document.querySelector('#content');
let cleanup;

document.querySelectorAll('[data-icon]').forEach((element) => { element.innerHTML = icon(element.dataset.icon); });
document.querySelector('.skip').addEventListener('click', (event) => {
  event.preventDefault();
  root.focus();
});
setAppDate(today());

function route(focus = false) {
  cleanup?.();
  closeDialog();
  const hash = window.location.hash.slice(1);
  const selected = Object.hasOwn(routes, hash) ? hash : 'dashboard';
  document.querySelectorAll('[data-route]').forEach((link) => {
    if (link.dataset.route === selected) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  document.title = `${routes[selected]?.title ?? 'Дашборд'} | Муниципальное имущество`;
  cleanup = selected === 'dashboard' ? mountDashboard(root) : mountRegistry(root, routes[selected]);
  if (focus) {
    window.scrollTo(0, 0);
    root.focus({ preventScroll: true });
  }
}
window.addEventListener('hashchange', () => route(true));
route();
