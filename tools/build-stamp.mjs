/* The dashboard build stamp: which version and which sources made dist/.

   vite.config.js writes it into dist/build.json; memai/webui_build.py
   recomputes sourceHash() in Python and rebuilds when the two differ, so the
   file set and the hashing here must match source_hash() there byte for
   byte (tests/test_webui_build.py runs both on one tree). */
import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { join, relative, sep } from 'node:path';

export const BUILD_CONFIG = ['package.json', 'package-lock.json', 'vite.config.js', 'tsconfig.json'];
const WEBUI = join('src', 'memai', 'webui');

async function walk(dir, skip) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  const files = [];
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (path !== skip) files.push(...await walk(path, skip));
    } else if (entry.isFile()) {
      files.push(path);
    }
  }
  return files;
}

async function exists(path) {
  try {
    await readFile(path);
    return true;
  } catch {
    return false;
  }
}

/* sha256 over each input's relative path and contents, CRLF read as LF. */
export async function sourceHash(root) {
  const webui = join(root, WEBUI);
  const files = await walk(webui, join(webui, 'dist'));
  for (const name of BUILD_CONFIG) {
    if (await exists(join(root, name))) files.push(join(root, name));
  }
  const named = files.map(path => [relative(root, path).split(sep).join('/'), path]);
  named.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));

  const hash = createHash('sha256');
  for (const [name, path] of named) {
    const bytes = await readFile(path);
    hash.update(name, 'utf8');
    hash.update('\0');
    hash.update(Buffer.from(bytes.toString('latin1').replaceAll('\r\n', '\n'), 'latin1'));
    hash.update('\0');
  }
  return hash.digest('hex');
}

/* The version src/memai/__init__.py declares. */
export async function packageVersion(root) {
  const text = await readFile(join(root, 'src', 'memai', '__init__.py'), 'utf8');
  const found = text.match(/^__version__\s*=\s*"([^"]+)"/m);
  if (!found) throw new Error('no __version__ in src/memai/__init__.py');
  return found[1];
}
