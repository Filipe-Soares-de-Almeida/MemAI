/* Opening a memory's record from any view, and the list of uids the record steps through. */

import { go } from './router.ts';

export const openRecord = (uid: string): void => go('memory', { uid });

/* Set by whoever put the record on screen (the memories list hands over its page); a record
   reached from anywhere else is absent from it and shows no stepper. */
let sequence: string[] = [];

export const setRecordSequence = (uids: unknown): void => {
  sequence = Array.isArray(uids) ? [...uids] : [];
};

export const recordSequence = (): readonly string[] => sequence;
