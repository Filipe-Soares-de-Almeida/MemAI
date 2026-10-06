/* The constants the dashboard shares with the server, read from memai/contract.json. */

import contract from '../contract.json' with { type: 'json' };

export const MEMORY = contract.memory;
export const TASK = contract.task;
export const ADMIN = contract.admin;
export const DIAGRAM = contract.diagram;
