/* i18n runtime: catalogs in public/i18n/<locale>.json, English and the active locale fetched. A
   switch reloads the page; strings bound for innerHTML may carry markup, so callers esc() values. */

'use strict';

/* registry of available locales — shown in the language selector */
const LOCALES = {
  en: 'English',
  'pt-BR': 'Português (BR)',
};

const STORAGE_KEY = 'memai.locale';
let stored = null;
try { stored = localStorage.getItem(STORAGE_KEY); } catch { /* storage may be blocked */ }
const locale = LOCALES[stored] ? stored : 'en';   /* default is English — no auto-detect */

const loadCatalog = async code => {
  const res = await fetch(`/static/i18n/${code}.json`);
  if (!res.ok) throw new Error(`i18n: HTTP ${res.status} for ${code}`);
  return res.json();
};

const en = await loadCatalog('en');
let active = en;
if (locale !== 'en') {
  try { active = await loadCatalog(locale); }
  catch (err) { console.error(err); /* fall back to English rather than break the UI */ }
}

/* `{count?one:many}`: the branch follows the NUMBER in `vars` with group separators stripped;
   anything but exactly one, a missing count included, takes `many`. */
const PLURAL = /\{(\w+)\?([^{}:]*):([^{}]*)\}/g;
const DIGITS = /[^0-9-]/g;

const t = (key, vars) => {
  let s = active.strings[key] ?? en.strings[key] ?? key;
  if (vars) {
    s = s.replace(PLURAL, (_, k, one, many) =>
      (Number(String(vars[k]).replace(DIGITS, '')) === 1 ? one : many));
    for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  }
  return s;
};

const set = code => {
  if (!LOCALES[code] || code === locale) return;
  try { localStorage.setItem(STORAGE_KEY, code); } catch { /* best effort */ }
  location.reload();   /* rebuild everything in the new language */
};

/* translate the static shell (index.html) in place */
const applyStatic = () => {
  document.documentElement.lang = locale;
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  document.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => el.setAttribute('aria-label', t(el.dataset.i18nAria)));
  /* The language picker lives in app.js: switching reloads the page and needs the modal machinery,
     which imports this module. */
};

/* months, weekdays and the number locale fall back per key like strings; `weekdays` is
   Monday-first. */
const I18N = {
  t, set, applyStatic, locale, locales: LOCALES,
  months: active.months || en.months,
  weekdays: active.weekdays || en.weekdays,
  numberLocale: active.numberLocale || en.numberLocale,
};

applyStatic();

export { I18N, t };
