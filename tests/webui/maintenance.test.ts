import { afterEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import MaintenanceView from '../../src/memai/webui/views/maintenance/MaintenanceView.vue';
import { backupKind, dateBucket, fmtDayKey, reasonLabel, segmentWidths, zipLabel }
  from '../../src/memai/webui/views/maintenance/maintenance.ts';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import { I18N } from '../../src/memai/webui/i18n.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');

async function until<T>(check: () => T, ms = 2000): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    const got = check();
    if (got) return got;
    if (Date.now() > end) throw new Error('condition never held');
    await new Promise(done => setTimeout(done, 5));
  }
}
const settle = async () => { for (let i = 0; i < 4; i++) { await nextTick(); await new Promise(r => setTimeout(r, 0)); } };
const press = async (el: Element | null) => { (el as HTMLElement).click(); await settle(); };
const answer = async (sel: '[data-ok]' | '[data-x]') => {
  (await until(() => document.querySelector<HTMLElement>(`.modal ${sel}`))).click();
  await settle();
};
const tick = async (box: Element | null, on = true) => {
  (box as HTMLInputElement).checked = on;
  box?.dispatchEvent(new Event('change'));
  await settle();
};
const posts = (path: string) => calls.filter(c => c.method === 'POST' && c.path === path);

afterEach(teardownView);

const DAY = 86400000;
const ago = (days: number) => new Date(Date.now() - days * DAY).toISOString();

const SHELF = [
  { name: 'Acme-20261006-101010.db', size: 1000, mtime: ago(0), label: 'before the refill' },
  { name: 'Acme-pre-restore-20261003-101010.db', size: 900, mtime: ago(3), pinned: true },
  { name: 'Acme-optimize-run4-20260820-101010.db', size: 800, mtime: ago(47) },
];
const health = (extra: Record<string, unknown> = {}) => ({
  project: 'Acme', integrity: { ok: true, detail: '' }, fts: { ok: true, detail: '', rows: 4, expected: 4 },
  relations: { orphans: 0 }, tags: { untagged: 0, active: 4 }, title: { untitled: 0, active: 4 },
  renders: { files: 0, bytes: 0, retention: '7d', path: 'renders' },
  file: { path: 'C:\\path\\to\\Acme.db', size: 5000, wal_size: 0, reclaimable: 0, compact_reason: '' },
  backups: SHELF.map(({ name, size, mtime }) => ({ name, size, mtime })), ...extra });
const archives = [{ name: 'Acme-2026-W30.zip', size: 300, mtime: ago(60), count: 1, raw: 800,
                    members: [{ name: 'Acme-20260721-101010.db', size: 800, mtime: ago(70) }] }];
const config = { domain_case: '', svg_retention: '7d', warden_enabled: true, warden_minutes: 20,
                 task_ask_enabled: false, task_ask_minutes: 60, sections: {} };

type Call = { method: string; body?: Record<string, unknown> };
type Answer = (path: string, call: Call) => unknown;

/* an invented store; `extra` answers a path first */
function store(extra: Answer = () => undefined): Answer {
  return (path, call) => {
    const got = extra(path, call);
    if (got !== undefined) return got;
    if (path === '/api/maintenance/health') return health();
    if (path === '/api/maintenance/backups') return { project: 'Acme', shelf: SHELF, archives };
    if (path === '/api/maintenance/sections-queue') return { ok: true, migrated: true, unread: 0, queue: [] };
    if (path === '/api/config') return config;
    if (path.startsWith('/api/audit')) return { entries: [] };
    if (path === '/api/domains') return { domains: [{ domain: 'acme/lamps' }] };
    if (call.method === 'POST') return { ok: true };
    throw new Error(`unexpected ${path}`);
  };
}

async function show(tab: string, answerWith: Answer = store()) {
  serveApi(answerWith);
  const view = document.getElementById('view') as HTMLElement;
  await mountView(MaintenanceView, view, new URLSearchParams(tab ? { tab } : {}), { stale: () => false });
  await settle();
  return view;
}

describe('the shelf helpers', () => {
  it('reads what a backup was taken for out of its filename', () => {
    expect(backupKind('Acme-pre-restore-20261003-101010.db', 'Acme')).toBe('pre-restore');
    expect(backupKind('acme-20261003-101010.db', 'Acme')).toBe('');
    expect(reasonLabel('optimize-run4')).toBe(en['mn.bk.reason.optimize'].replace('{n}', '4'));
    expect(reasonLabel('')).toBe(en['mn.bk.reason.hand']);
    expect(reasonLabel('mystery')).toBe('mystery');
  });

  it('names a week zip in the interface language and any other by its own name', () => {
    expect(zipLabel('Acme-2026-W07.zip', 'Acme')).toBe(en['mn.zip.week'].replace('{week}', '7').replace('{year}', '2026'));
    expect(zipLabel('Acme-lamp checks.zip', 'Acme')).toBe('lamp checks');
  });

  it('buckets by today, this week, then month with the year once it is not this one', () => {
    const now = new Date(2026, 9, 6, 12);
    expect(dateBucket(new Date(2026, 9, 6, 1).toISOString(), now)).toBe(en['mn.bk.today']);
    expect(dateBucket(new Date(2026, 9, 2).toISOString(), now)).toBe(en['mn.bk.thisWeek']);
    expect(dateBucket(new Date(2026, 7, 2).toISOString(), now)).toBe(I18N.months[7]);
    expect(dateBucket(new Date(2025, 7, 2).toISOString(), now)).toBe(`${I18N.months[7]} 2025`);
    expect(fmtDayKey('2025-08-02', now)).toBe(`02 ${I18N.months[7]} 2025`);
  });

  it('sizes a bar from shares of its own total, a zero segment as zero', () => {
    expect(segmentWidths([{ value: 3, fill: '' }, { value: 0, fill: '' }, { value: 1, fill: '' }]))
      .toEqual(['75.00%', '0.00%', '25.00%']);
  });
});

describe('the tab strip', () => {
  it('opens the tab the address names, walks with the arrows and keeps a visited tab built', async () => {
    const view = await show('log');
    expect(view.querySelector('#mntTab-log')?.getAttribute('aria-selected')).toBe('true');
    expect(view.querySelector('#mntPanel-log')?.hidden).toBe(false);
    expect(view.querySelector('#mntPanel-backups')?.children.length).toBe(0);

    view.querySelector('#mntTab-log')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    await settle();
    expect(location.hash).toBe('#/maintenance?tab=warden');
    expect(document.activeElement?.id).toBe('mntTab-warden');
    expect(view.querySelector('#mntPanel-log')?.hidden).toBe(true);
    expect(view.querySelector('#logBody')).not.toBeNull();
  });

  it('names the store it acts on and counts the sections queue on a closed tab', async () => {
    const view = await show('', store(path => path === '/api/maintenance/sections-queue'
      ? { ok: true, migrated: true, unread: 0, queue: [{ uid: 'a1', type: 'note', domain: '', status: 'active',
                                                          detail: '', snippet: '', created_at: '' }] } : undefined));
    expect(view.querySelector('#mntStore')?.textContent).toContain('Acme');
    const badge = view.querySelector<HTMLElement>('[data-badge="sections"]');
    expect(badge?.hidden).toBe(false);
    expect(badge?.textContent).toBe('1');
  });
});

describe('the backups tab', () => {
  it('deletes the ticked backups only once the dialog is confirmed', async () => {
    const view = await show('backups');
    const del = view.querySelector<HTMLButtonElement>('#bkDelete');
    expect(del?.disabled).toBe(true);
    await tick(view.querySelector('[data-pick="Acme-20261006-101010.db"]'));
    await tick(view.querySelector('[data-pick="Acme-optimize-run4-20260820-101010.db"]'));
    expect(view.querySelector('#bkRestore')?.hasAttribute('disabled')).toBe(true);

    await press(del);
    expect(document.querySelector('.modal [data-ok]')?.classList.contains('btn-danger')).toBe(true);
    await answer('[data-x]');
    expect(posts('/api/maintenance/backup-delete')).toHaveLength(0);

    await press(del);
    await answer('[data-ok]');
    expect(posts('/api/maintenance/backup-delete')[0].body).toEqual(
      { names: ['Acme-20261006-101010.db', 'Acme-optimize-run4-20260820-101010.db'] });
  });

  it('restores only a single tick, and escapes its name in the question', async () => {
    const shelf = [{ ...SHELF[0], label: '<img src=x>' }];
    const view = await show('backups', store(path =>
      path === '/api/maintenance/backups' ? { project: 'Acme', shelf, archives: [] } : undefined));
    await tick(view.querySelector('#bkAll'));
    await press(view.querySelector('#bkRestore'));
    const body = document.querySelector('.modal-body') as HTMLElement;
    expect(body.querySelector('img')).toBeNull();
    expect(body.textContent).toContain('<img src=x>');
    await answer('[data-ok]');
    expect(posts('/api/maintenance/backup-restore')[0].body).toEqual({ name: 'Acme-20261006-101010.db' });
  });

  it('writes a typed name on Enter and leaves it on Escape', async () => {
    const view = await show('backups');
    await press(view.querySelector('[data-rename="Acme-optimize-run4-20260820-101010.db"]'));
    let field = view.querySelector<HTMLInputElement>('[data-renaming]') as HTMLInputElement;
    expect(document.activeElement).toBe(field);
    field.value = 'last good run';
    field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await settle();
    expect(view.querySelector('[data-renaming]')).toBeNull();
    expect(posts('/api/maintenance/backup-name')).toHaveLength(0);

    await press(view.querySelector('[data-rename="Acme-optimize-run4-20260820-101010.db"]'));
    field = view.querySelector<HTMLInputElement>('[data-renaming]') as HTMLInputElement;
    field.value = ' last good run ';
    field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await settle();
    expect(posts('/api/maintenance/backup-name')[0].body).toEqual(
      { name: 'Acme-optimize-run4-20260820-101010.db', label: 'last good run' });
  });

  it('archives the ticks by month after showing the plan the server returns', async () => {
    const view = await show('backups', store((path, call) => path === '/api/maintenance/archive' && call.body?.dry_run
      ? { ok: true, plan: [{ name: 'Acme-2026-10.zip', added: 1, exists: false }] } : path === '/api/maintenance/archive'
      ? { ok: true, archive: 'Acme-2026-10.zip', archives: [{ name: 'Acme-2026-10.zip', added: 1, size: 10 }],
          added: 1, raw: 1000, size: 10 } : undefined));
    await tick(view.querySelector('[data-pick="Acme-20261006-101010.db"]'));
    await press(view.querySelector('#bkArchive'));
    await until(() => document.querySelector('.arch-plan-row'));
    expect(document.querySelector('.arch-plan-name')?.textContent).toBe('2026-10');

    const name = document.querySelector('input[name=archWay][value=name]') as HTMLInputElement;
    name.checked = true;
    name.dispatchEvent(new Event('change'));
    await settle();
    const box = document.querySelector('#archName') as HTMLInputElement;
    box.value = '-bad';
    box.dispatchEvent(new Event('input'));
    await settle();
    expect(document.querySelector('#archPlan')?.classList.contains('is-bad')).toBe(true);
    expect(document.querySelector<HTMLButtonElement>('.modal [data-ok]')?.disabled).toBe(true);

    const month = document.querySelector('input[name=archWay][value=month]') as HTMLInputElement;
    month.checked = true;
    month.dispatchEvent(new Event('change'));
    await until(() => !document.querySelector<HTMLButtonElement>('.modal [data-ok]')?.disabled);
    await answer('[data-ok]');
    const sent = posts('/api/maintenance/archive').filter(c => !c.body?.dry_run);
    expect(sent[0].body).toEqual({ names: ['Acme-20261006-101010.db'], group: 'month' });
  });

  it('reads a zip without ticks and deletes it only once confirmed', async () => {
    const view = await show('backups');
    await press(view.querySelector('[data-shelf="Acme-2026-W30.zip"]'));
    expect(view.querySelector('[data-pick]')).toBeNull();
    expect(view.querySelector('.mnt-sel-text')?.textContent).toBe(en['mn.bk.zipReadOnly']);
    await press(view.querySelector('#bkDropZip'));
    await answer('[data-ok]');
    expect(posts('/api/maintenance/archive-delete')[0].body).toEqual({ name: 'Acme-2026-W30.zip' });
  });

  it('shows a backup taken from its own button on the shelf', async () => {
    let taken = false;
    const view = await show('backups', store(path => {
      if (path === '/api/maintenance/backup') { taken = true; return { ok: true, project: 'Acme', path: 'x/Acme-new.db', size: 1 }; }
      if (path === '/api/maintenance/backups') {
        return { project: 'Acme', archives,
                 shelf: taken ? [{ name: 'Acme-new.db', size: 1, mtime: ago(0) }, ...SHELF] : SHELF };
      }
      return undefined;
    }));
    await press(view.querySelector('[data-op="backup"]'));
    await until(() => view.querySelector('[data-pick="Acme-new.db"]'));
  });
});

describe('the storage tab', () => {
  it('keeps compacting shut until a backup exists, and asks before it runs', async () => {
    const bare = await show('storage', store(path => path === '/api/maintenance/health' ? health({ backups: [] }) : undefined));
    expect(bare.querySelector<HTMLButtonElement>('[data-op="vacuum"]')?.disabled).toBe(true);
    teardownView();

    const view = await show('storage');
    const vacuum = view.querySelector<HTMLButtonElement>('[data-op="vacuum"]');
    expect(vacuum?.disabled).toBe(false);
    expect(view.querySelector('[data-op="orphans"]')).toBeNull();
    await press(vacuum);
    await answer('[data-x]');
    expect(posts('/api/maintenance/vacuum')).toHaveLength(0);
  });

  it('offers to clean references only when there are some, and reflects the stored render window', async () => {
    const view = await show('storage', store(path => path === '/api/maintenance/health'
      ? health({ relations: { orphans: 2 }, renders: { files: 1, bytes: 10, retention: '30d', path: '' } }) : undefined));
    expect(view.querySelector('[data-op="orphans"]')).not.toBeNull();
    expect(view.querySelector('#rnKeep')?.getAttribute('data-v')).toBe('30d');
  });
});

describe('the sections tab', () => {
  it('lists the bodies to settle and opens one', async () => {
    const view = await show('sections', store(path => path === '/api/maintenance/sections-queue'
      ? { ok: true, migrated: true, unread: 0, queue: [
          { uid: 'a1b2c3d4e5f60001', type: 'anti_pattern', domain: 'acme/lamps', status: 'active',
            detail: 'no line opens with TEMPTATION', snippet: 'Filling a lamp to the brim.', created_at: '' }] }
      : undefined));
    expect(view.querySelector('#scBody .intro')?.textContent).toBe(en['mn.sc.pending'].replace('{n}', '1'));
    await press(view.querySelector('.sc-row'));
    expect(location.hash).toBe('#/memory?uid=a1b2c3d4e5f60001');
  });
});

describe('the duplicates tab', () => {
  const card = (uid: string) => ({
    rowid_pk: 1, uid, type: 'note', title: '', content: 'The lamp takes grade B oil.', content_len: 27,
    domain: 'acme/lamps', tags: '', session: '', status: 'active', confidence: 'unverified', pin: '',
    superseded_by: null, review_after: '', source_ref: '', created_at: '', updated_at: '' });

  it('scans with the chosen bounds, links a pair once and archives one side', async () => {
    const view = await show('dupes', store(path => path.startsWith('/api/maintenance/dedup')
      ? { pairs: [{ a: card('a1b2c3d4e5f60006'), b: card('a1b2c3d4e5f60007'), ratio: 0.8, method: 'text' }],
          threshold: 0.7 } : path === '/api/relations' ? { ok: true, id: 1 } : undefined));
    const thr = view.querySelector('#ddThr') as HTMLInputElement;
    thr.value = '0.7';
    thr.dispatchEvent(new Event('input'));
    const domain = view.querySelector('#ddDomain') as HTMLInputElement;
    domain.value = ' acme/lamps ';
    domain.dispatchEvent(new Event('input'));
    await settle();
    expect(view.querySelector('#ddThrVal')?.textContent).toBe('0.70');
    await press(view.querySelector('#ddRun'));
    expect(calls.find(c => c.path.startsWith('/api/maintenance/dedup'))?.path)
      .toBe('/api/maintenance/dedup?threshold=0.7&domain=acme%2Flamps');
    expect(view.querySelector('[data-badge="dupes"]')?.textContent).toBe('1');

    await press(view.querySelector('[data-linkdup="0"]'));
    expect(posts('/api/relations')[0].body).toMatchObject(
      { from_uid: 'a1b2c3d4e5f60006', to_uid: 'a1b2c3d4e5f60007', relation_type: 'duplicates' });
    expect(view.querySelector<HTMLButtonElement>('[data-linkdup="0"]')?.disabled).toBe(true);

    await press(view.querySelector('[data-archm="a1b2c3d4e5f60007"]'));
    await answer('[data-ok]');
    expect(posts('/api/memories/a1b2c3d4e5f60007/status')[0].body).toEqual(
      { status: 'archived', reason: en['mn.dd.dupReason'] });
    expect(view.querySelectorAll('.pair-card')[1].classList.contains('decided')).toBe(true);
  });
});

describe('the log tab', () => {
  it('heads today with its name and opens the memory an entry changed', async () => {
    const view = await show('log', store(path => path.startsWith('/api/audit') ? { entries: [
      { id: 2, memory_uid: 'a1b2c3d4e5f60003', edited_at: ago(0), note: '', prev_len: 30, new_len: 1056,
        content_changed: 1, type: 'note', domain: 'acme', status: 'active' },
      { id: 1, memory_uid: 'a1b2c3d4e5f60004', edited_at: '2025-08-02T12:00:00', note: 'retag', prev_len: 5,
        new_len: 5, content_changed: 0, type: 'note', domain: '', status: 'active' }] } : undefined));
    const days = [...view.querySelectorAll('.mnt-day-name')].map(d => d.textContent);
    expect(days).toEqual([en['mn.log.today'], `02 ${I18N.months[7]} 2025`]);
    expect(view.querySelector('.mnt-ev-title')?.textContent).toBe(en['mn.au.contentEdit']);
    expect(view.querySelectorAll('.mnt-ev-delta')[1].textContent).toBe('');
    await press(view.querySelector('.mnt-ev'));
    expect(location.hash).toBe('#/memory?uid=a1b2c3d4e5f60003');
  });
});

describe('the warden tab', () => {
  it('shows the stored settings and writes only what is picked', async () => {
    const view = await show('warden', store((path, call) => path === '/api/config' && call.method === 'POST'
      ? { ...config, warden_enabled: false } : undefined));
    expect(view.querySelector('#wdOn')?.getAttribute('data-v')).toBe('on');
    expect(view.querySelector('#wdEvery')?.getAttribute('data-v')).toBe('20');
    expect(view.querySelector('#taOn')?.getAttribute('data-v')).toBe('off');
    expect(posts('/api/config')).toHaveLength(0);

    await press(view.querySelector('#wdOn'));
    await press(document.querySelector('.pick-pop [role="option"][data-v="off"]'));
    expect(posts('/api/config')[0].body).toEqual({ warden_enabled: false });
  });
});

describe('the tab strip', () => {
  it('offers no interface tab, and an address naming one opens the first tab', async () => {
    const view = await show('interface', () => { throw new Error('offline'); });
    expect(view.querySelector('#mntTab-interface')).toBeNull();
    expect(view.querySelector('#mntTab-backups')?.getAttribute('aria-selected')).toBe('true');
    expect(Object.keys(en).filter(k => k.startsWith('mn.ui.') || k === 'mn.tab.interface')).toEqual([]);
  });
});
