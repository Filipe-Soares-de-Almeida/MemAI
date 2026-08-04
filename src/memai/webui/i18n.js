/* MemAI admin i18n runtime (ES module, no build step).
   String catalogs live in i18n/<locale>.json — one file per language.
   Adding a language = drop i18n/<code>.json + add one LOCALES entry.
   Only English (the fallback) and the active locale are fetched.
   The user's choice persists in localStorage and a switch reloads the
   page, so module-level constants in app.js can bake translations at
   load time. Convention: strings that end up in innerHTML may carry
   markup; callers esc() any user-provided value BEFORE interpolating. */

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
  const res = await fetch(new URL(`./i18n/${code}.json`, import.meta.url));
  if (!res.ok) throw new Error(`i18n: HTTP ${res.status} for ${code}`);
  return res.json();
};

const en = await loadCatalog('en');
let active = en;
if (locale !== 'en') {
  try { active = await loadCatalog(locale); }
  catch (err) { console.error(err); /* fall back to English rather than break the UI */ }
}

const t = (key, vars) => {
  let s = active.strings[key] ?? en.strings[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
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
  /* The language control is built and wired in app.js, NOT here: it is a
     picker now (core/pick.js, which imports this module for its own strings)
     and switching reloads the page, so it has to ask first -- which means the
     modal machinery, which imports this module too. This layer owns the
     catalogs and the registry below; who draws the switch is not its call. */
};

/* month names and the number locale fall back per-key like strings do: a
   new catalog that ships `strings` and forgets `months` degrades to
   English month names instead of rendering every date as undefined */
const I18N = {
  t, set, applyStatic, locale, locales: LOCALES,
  months: active.months || en.months,
  numberLocale: active.numberLocale || en.numberLocale,
};

applyStatic();

export { I18N, t };
