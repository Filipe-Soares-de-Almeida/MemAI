/* Copying to the clipboard, confirmed by a toast; no clipboard access says so rather than nothing. */

import { toast } from './toasts.ts';
import { t } from '../i18n.ts';

export function copyText(text: string, message: string): void {
  if (!navigator.clipboard) { toast(t('toast.copyUnavailable'), 'bad'); return; }
  navigator.clipboard.writeText(text)
    .then(() => toast(message, 'ok'))
    .catch(() => toast(t('toast.copyUnavailable'), 'bad'));
}

export const copyUid = (uid: string): void => copyText(uid, t('toast.uidCopied', { uid }));

export const copyCode = (text: string): void => copyText(text, t('toast.codeCopied'));
