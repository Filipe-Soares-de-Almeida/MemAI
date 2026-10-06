/* A new diagram seeded with a start and an end step, for the canvas to grow from. */

import { api } from './api.ts';
import { t } from '../i18n.ts';

export interface NewDiagram {
  title: string;
  domain?: string;
  also?: string;
  tags?: string;
}

export const newDiagramSkeleton = ({ title, domain = '', also = '', tags = '' }: NewDiagram) =>
  api<{ uid: string }>('/api/diagrams', { body: {
    title, domain, also, tags,
    nodes: [
      { key: 'start', shape: 'start', label: t('dg.skeleton.start') },
      { key: 'finish', shape: 'end', label: t('dg.skeleton.end') },
    ],
    edges: [{ from: 'start', to: 'finish' }] } });
