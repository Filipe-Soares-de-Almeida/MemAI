/* Every edit the diagram editor offers, wherever it is asked from: the toolbar, the canvas's menu
   or the inspector. Each one writes through act(). */

import { inject } from 'vue';
import type { InjectionKey } from 'vue';
import { esc } from '../../core/dom.ts';
import { pickMemories } from '../../core/link-picker.js';
import { DG_REL_SUGGEST } from '../../core/shared.js';
import { confirmModal, failed, openDialog, openDropMenu, promptModal, toast } from '../../core/ui.js';
import { FONT_SCALES } from '../../engines/diagram-engine.ts';
import type { DiagramEdge, Pt } from '../../engines/diagram-engine.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DiagramJump, DiagramRecord } from '../../api/types.ts';
import type { Editor } from './editor.ts';
import FlowMetaDialog from './FlowMetaDialog.vue';
import JumpTargetDialog from './JumpTargetDialog.vue';
import MermaidDialog from './MermaidDialog.vue';
import NoteDialog from './NoteDialog.vue';
import StepDialog from './StepDialog.vue';
import type { StepFields } from './StepDialog.vue';

type EdgeEnds = Pick<DiagramEdge, 'from' | 'to' | 'label'>;

export function flowActions(ed: Editor) {
  const { uid, data, selected, engine, act } = ed;
  const api = client.diagrams;

  const saveEdgeLabel = (edge: EdgeEnds, label: string) =>
    act(() => api.edge(uid, { from: edge.from, to: edge.to, label }), t('dg.saved'));

  async function editEdgeLabel(edge: EdgeEnds) {
    const label = await promptModal({
      title: t('dg.edgeLabel.editTitle'), body: t('dg.edgeLabel.body', { from: esc(edge.from), to: esc(edge.to) }),
      label: t('dg.edgeLabel.label'), placeholder: t('dg.edgeLabel.ph'), value: edge.label || '', okLabel: t('common.save'),
    });
    if (label !== null) await saveEdgeLabel(edge, label);
  }

  const deleteEdge = (edge: EdgeEnds) =>
    act(() => api.edge(uid, { from: edge.from, to: edge.to, delete: true }), t('dg.disconnected'));

  /* the note is not sent: the API patches only what it receives, and the note has the inspector */
  async function editStep(node: { key: string; label: string; shape: string }) {
    const out = await openDialog(StepDialog, { title: t('dg.editStep.title'), stepKey: node.key, label: node.label,
                                               shape: node.shape, lockKey: true }) as StepFields | null;
    if (out) await act(() => api.node(uid, { key: node.key, label: out.label, shape: out.shape }), t('dg.saved'));
  }

  async function editNote(key: string) {
    const stored = data.value.nodes.find(n => n.key === key);
    const note = await openDialog(NoteDialog, { stepKey: key, note: stored?.note || '' }) as string | null;
    if (note !== null) await act(() => api.node(uid, { key, note }), t('dg.saved'));
  }

  async function saveStep(key: string, fields: { label: string; shape: string; note: string }) {
    await act(() => api.node(uid, { key, ...fields }), t('dg.saved'));
  }

  async function deleteStep(key: string) {
    if (!(await confirmModal({ title: t('dg.confirmDelete.title'), body: t('dg.confirmDelete.body', { key: esc(key) }),
                               okLabel: t('dg.deleteStep'), danger: true }))) return;
    if (selected.value === key) selected.value = null;
    await act(() => api.node(uid, { key, delete: true }), t('dg.deleted'));
  }

  function land(key: string) {
    selected.value = key;
    engine.value?.select(key);
  }

  async function addStep() {
    const step = await openDialog(StepDialog, { title: t('dg.newStep.title') }) as StepFields | null;
    if (!step) return;
    if (await act(() => api.node(uid, { ...step }), t('dg.added'))) land(step.key);
  }

  /* where the reader right-clicked: the server places a new step from the graph, then it is moved */
  async function addStepAt(world: Pt) {
    const step = await openDialog(StepDialog, { title: t('dg.newStep.title') }) as StepFields | null;
    if (!step) return;
    try {
      await api.node(uid, { ...step });
      await api.layout(uid, { positions: { [step.key]: { x: Math.round(world.x), y: Math.round(world.y) } } });
      toast(t('dg.added'), 'ok');
      await ed.reload();
      land(step.key);
    } catch (err) { failed('err.diagram', err); }
  }

  async function arrange() {
    await act(() => api.relayout(uid), t('dg.arranged'));
    engine.value?.fit();
  }

  const resetCardSize = (key: string) => act(() => api.layout(uid, { reset_boxes: [key] }), t('dg.sizeReset'));

  async function editMeta() {
    const meta = await openDialog(FlowMetaDialog, { title: data.value.title, summary: data.value.summary }) as
      { title: string; summary: string } | null;
    if (meta) await act(() => api.meta(uid, meta), t('dg.saved'));
  }

  /* text size is stored on the diagram: card sizes are stored too, and are right at one size only */
  function fontMenu(button: HTMLElement) {
    openDropMenu(button, FONT_SCALES.map(s => ({
      label: `${Math.round(s * 100)}%${s === 1 ? ` · ${t('dg.font.default')}` : ''}`,
      run: () => act(() => api.meta(uid, { font_scale: s }), t('dg.saved')),
    })));
  }

  /* either end can delete, so the payload is always "my side, the other side" */
  const deleteJump = (j: DiagramJump) => act(() => api.jump(uid, { node_key: j.node_key, peer_uid: j.peer_uid,
                                                                   peer_node: j.peer_node, delete: true }),
                                             t('dg.jump.removed'));

  async function addJump(key: string) {
    const chosen = await pickMemories({ title: t('dg.jump.pickTitle', { key }), exclude: uid, multi: false,
                                        type: 'diagram', okLabel: t('common.next') });
    if (!chosen?.uids.length) return;
    const peer = chosen.uids[0];
    let target: DiagramRecord;
    try { target = await api.get(peer); } catch (err) { failed('err.load', err); return; }
    const step = await openDialog(JumpTargetDialog, { target }) as { node: string; label: string } | null;
    if (step) await act(() => api.jump(uid, { node_key: key, peer_uid: peer, peer_node: step.node, label: step.label }),
                        t('dg.jump.added'));
  }

  /* one call per memory: a partial failure keeps the ones that landed */
  async function attach(key: string, linked: string[]) {
    const chosen = await pickMemories({ title: t('dg.link.pickTitle', { key }), exclude: uid, linked,
                                        relOptions: DG_REL_SUGGEST, relValue: 'explains', okLabel: t('dg.link.attach') });
    if (!chosen?.uids.length) return;
    await act(async () => {
      for (const target of chosen.uids) {
        await api.link(uid, { node_key: key, target_uid: target, relation_type: chosen.relation || 'explains' });
      }
    }, t('dg.linkedN', { n: chosen.uids.length }));
  }

  const unlink = (key: string, target: string) =>
    act(() => api.link(uid, { node_key: key, target_uid: target, delete: true }), t('dg.unlinked'));

  async function connect(from: string, to: string) {
    const label = await promptModal({ title: t('dg.edgeLabel.title'), body: t('dg.edgeLabel.body', { from: esc(from), to: esc(to) }),
                                      label: t('dg.edgeLabel.label'), placeholder: t('dg.edgeLabel.ph'),
                                      okLabel: t('dg.connectOk') });
    if (label === null) { ed.paintHint(); return; }
    await act(() => api.edge(uid, { from, to, label }), t('dg.connected'));
  }

  async function mermaid() {
    let source = '';
    try { source = (await api.mermaid(uid)).mermaid; } catch (err) { failed('err.load', err); return; }
    await openDialog(MermaidDialog, { source });
  }

  return { saveEdgeLabel, editEdgeLabel, deleteEdge, editStep, editNote, saveStep, deleteStep, addStep, addStepAt,
           arrange, resetCardSize, editMeta, fontMenu, deleteJump, addJump, attach, unlink, connect, mermaid };
}

export type FlowActions = ReturnType<typeof flowActions>;

export const ACTIONS: InjectionKey<FlowActions> = Symbol('diagram-actions');

export function useActions(): FlowActions {
  const actions = inject(ACTIONS);
  if (!actions) throw new Error('a diagram editor part outside the editor');
  return actions;
}
