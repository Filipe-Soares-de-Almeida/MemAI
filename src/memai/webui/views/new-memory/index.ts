/* Opens the New memory dialog once the domain list and the section spec it draws from are in. */

import { createApp } from 'vue';
import { getDomains } from '../../core/shared.js';
import * as client from '../../api/client.ts';
import type { DomainEntry, SectionSpec } from '../../api/types.ts';
import NewMemoryDialog from './NewMemoryDialog.vue';

export async function openNewMemory(): Promise<void> {
  /* both are conveniences: without them the dialog still writes a plain body */
  const [domains, spec] = await Promise.all([
    getDomains().catch((): DomainEntry[] => []),
    client.config.get().then(c => c.sections || {}).catch((): Record<string, SectionSpec[]> => ({})),
  ]);
  const app = createApp(NewMemoryDialog, { domains, spec, onClose: () => app.unmount() });
  app.mount(document.createElement('div'));
}
