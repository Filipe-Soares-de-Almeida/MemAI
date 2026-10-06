/* i18n runtime: catalogs in public/i18n/<locale>.json, English and the active locale fetched. A
   switch reloads the page; strings bound for innerHTML may carry markup, so callers esc() values. */

import type english from './public/i18n/en.json';

type Catalog = typeof english;
export type I18nKey = keyof Catalog['strings'];
export type I18nVars = Record<string, string | number>;

interface LocaleCatalog {
  strings: Partial<Record<string, string>>;
  months?: string[];
  weekdays?: string[];
  numberLocale?: string;
}

/* registry of available locales — shown in the language selector */
const LOCALES: Record<string, string> = {
  en: 'English',
  'pt-BR': 'Português (BR)',
};

const STORAGE_KEY = 'memai.locale';
let stored: string | null = null;
try { stored = localStorage.getItem(STORAGE_KEY); } catch { /* storage may be blocked */ }
const locale = stored && LOCALES[stored] ? stored : 'en';   /* default is English — no auto-detect */

const loadCatalog = async <C extends LocaleCatalog>(code: string): Promise<C> => {
  const res = await fetch(`/static/i18n/${code}.json`);
  if (!res.ok) throw new Error(`i18n: HTTP ${res.status} for ${code}`);
  return res.json();
};

const en = await loadCatalog<Catalog>('en');
const enStrings: LocaleCatalog['strings'] = en.strings;
let active: LocaleCatalog = en;
if (locale !== 'en') {
  try { active = await loadCatalog(locale); }
  catch (err) { console.error(err); /* fall back to English rather than break the UI */ }
}

/* `{count?one:many}`: the branch follows the NUMBER in `vars` with group separators stripped;
   anything but exactly one, a missing count included, takes `many`. */
const PLURAL = /\{(\w+)\?([^{}:]*):([^{}]*)\}/g;
const DIGITS = /[^0-9-]/g;

/* A key read off the page or built at runtime, which the catalog type cannot vouch for. */
const lookup = (key: string, vars?: I18nVars): string => {
  let s = active.strings[key] ?? enStrings[key] ?? key;
  if (vars) {
    s = s.replace(PLURAL, (_, k: string, one: string, many: string) =>
      (Number(String(vars[k]).replace(DIGITS, '')) === 1 ? one : many));
    for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  }
  return s;
};

const t = (key: I18nKey, vars?: I18nVars): string => lookup(key, vars);

const set = (code: string): void => {
  if (!LOCALES[code] || code === locale) return;
  try { localStorage.setItem(STORAGE_KEY, code); } catch { /* best effort */ }
  location.reload();   /* rebuild everything in the new language */
};

/* translate the static shell (index.html) in place */
const applyStatic = (): void => {
  document.documentElement.lang = locale;
  document.querySelectorAll<HTMLElement>('[data-i18n]').forEach(el => {
    el.textContent = lookup(el.dataset.i18n ?? '');
  });
  document.querySelectorAll<HTMLInputElement>('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = lookup(el.dataset.i18nPlaceholder ?? '');
  });
  document.querySelectorAll<HTMLElement>('[data-i18n-title]').forEach(el => {
    el.title = lookup(el.dataset.i18nTitle ?? '');
  });
  document.querySelectorAll<HTMLElement>('[data-i18n-aria]').forEach(el =>
    el.setAttribute('aria-label', lookup(el.dataset.i18nAria ?? '')));
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
