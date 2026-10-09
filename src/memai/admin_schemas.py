"""The JSON each dashboard API route answers with, one TypedDict per response."""

from typing import Any, Literal, NotRequired, TypedDict

# A store row or a db helper's dict, passed through without a shape of its own yet.
Row = dict[str, Any]


class Ok(TypedDict):
    ok: Literal[True]


class Scoped(TypedDict):
    domain_scope: NotRequired[list[str]]


# ------------------------------------------------------------------ overview

class Totals(TypedDict):
    memories: int
    active: int
    archived: int
    relations: int
    edits: int
    sessions: int
    domains: int


class Symptom(TypedDict):
    key: str
    severity: str
    count: int
    share: float
    params: dict[str, str]
    of: NotRequired[int]


class DayCount(TypedDict):
    day: str
    count: int


class DbFile(TypedDict):
    project: str
    path: str
    size: int
    wal_size: int


class HealthScore(TypedDict):
    score: int
    axes: dict[str, int]
    active: int
    delta: int | None
    delta_days: int
    since: str | None


class Overview(TypedDict):
    totals: Totals
    by_type: dict[str, int]
    by_confidence: dict[str, int]
    by_type_confidence: dict[str, dict[str, int]]
    open_tasks: int
    health: HealthScore
    symptoms: list[Symptom]
    activity: list[DayCount]
    domains: list[Row]
    recent: list[Row]
    db: DbFile


# ------------------------------------------------------------------ projects

class Projects(TypedDict):
    active: str
    projects: list[Row]


class ProjectCreated(Projects):
    ok: Literal[True]
    name: str


class ProjectDeleted(Projects):
    ok: Literal[True]


class ProjectActivated(TypedDict):
    ok: Literal[True]
    active: str


class ProjectMove(TypedDict):
    dry_run: bool
    source: str
    target: str
    creates: bool
    memories: int
    diagrams: int
    tasks: int
    relations: int
    edits: int
    conflicts: list[str]
    unknown: list[str]
    outside: Any
    moved: NotRequired[int]
    backup: NotRequired[str]
    errors: NotRequired[list[Any]]


# ------------------------------------------------------------------ memories

class TaskProgress(TypedDict):
    done: int
    total: int


class MemorySummary(TypedDict):
    """A memory's columns, with the body cut to a snippet."""
    rowid_pk: int
    uid: str
    type: str
    title: str
    content: str
    content_len: int
    domain: str
    also: NotRequired[list[str]]
    tags: str
    session: str
    status: str
    confidence: str
    pin: str
    superseded_by: str | None
    review_after: str
    source_ref: str
    created_at: str
    updated_at: str


class MemoryRow(MemorySummary):
    """A memory as the list shows it: its summary, how often it is recalled, and how a task stands."""
    recalls: int
    last_recall: str | None
    progress: NotRequired[TaskProgress]
    task_state: NotRequired[str]
    fts_rank: NotRequired[float]
    match_source: NotRequired[str]
    succeeded_by: NotRequired[list[str]]


class MemoryPage(Scoped):
    total: int
    items: list[MemoryRow]
    searched: bool


class MemoryCreated(TypedDict):
    uid: str
    also: list[str]


class SectionSpec(TypedDict):
    key: str
    label: str
    max_len: int


class BodyLink(TypedDict):
    """What a [[uid]] written in a body points at; a uid nothing resolves carries only `missing`."""
    uid: str
    type: NotRequired[str]
    domain: NotRequired[str]
    status: NotRequired[str]
    snippet: NotRequired[str]
    linked: NotRequired[bool]
    missing: NotRequired[bool]


class TaskLink(TypedDict):
    uid: str
    title: str
    type: str


class TaskItem(TypedDict):
    id: int
    n: int
    seq: int
    text: str
    state: str
    updated_at: str
    updated_session: str
    links: list[TaskLink]


class TaskComment(TypedDict):
    id: int
    item: int | None
    body: str
    author: str
    session: str
    created_at: str


class TaskDepend(TypedDict):
    item: int | None
    text: str
    reason: str


class ItemRef(TypedDict):
    n: NotRequired[int]
    text: NotRequired[str]
    state: NotRequired[str]
    deleted: NotRequired[bool]


class TaskNote(TypedDict):
    id: int
    title: str
    body: str
    items: list[int]
    updated_at: str
    body_links: dict[str, BodyLink]
    brief: dict[str, str] | None
    depends: list[TaskDepend] | None


class TaskRecord(TypedDict):
    goal: str
    state: str
    completed_at: str
    items: list[TaskItem]
    comments: list[TaskComment]
    notes: list[TaskNote]
    refs: dict[str, ItemRef]
    body_links: dict[str, BodyLink]


class EditEntry(TypedDict):
    id: int
    memory_uid: str
    edited_at: str
    prev_content: str
    new_content: str
    note: str


class RecordSection(TypedDict):
    key: str
    text: str


class PeerCard(TypedDict):
    uid: str
    type: str
    domain: str
    title: str
    status: str
    confidence: str
    snippet: str
    created_at: str


class MissingPeer(TypedDict):
    uid: str
    missing: Literal[True]


class DiagramNodeRow(TypedDict):
    key: str
    label: str
    shape: str
    note: str
    seq: int
    x: float
    y: float
    w: float | None
    h: float | None


# `from` is a keyword, hence the functional form; `loops` says the edge closes a cycle.
DiagramEdgeRow = TypedDict("DiagramEdgeRow", {
    "from": str, "to": str, "label": str, "seq": int, "loops": bool})


class DiagramNodeLink(TypedDict):
    """A step's tie to a memory, with the card the editor shows for it."""
    node_key: str
    target_uid: str
    relation_type: str
    created_at: str
    target_type: str
    target_domain: str
    target_status: str
    target_confidence: str
    peer: PeerCard | MissingPeer


class DiagramJump(TypedDict):
    """A jump seen from this diagram: `node_key` is the step here ('' for the whole diagram)."""
    direction: str
    node_key: str
    peer_uid: str
    peer_node: str
    peer_title: str
    peer_node_label: str
    peer_status: str
    label: str
    created_at: str


class DiagramRecord(TypedDict):
    uid: str
    kind: str
    title: str
    summary: str
    font_scale: float
    nodes: list[DiagramNodeRow]
    edges: list[DiagramEdgeRow]
    links: list[DiagramNodeLink]
    jumps: list[DiagramJump]
    mermaid: str


class RecordRelation(TypedDict):
    """A relation as seen from the record: which end it is on, and the memory at the other."""
    id: int
    from_uid: str
    to_uid: str
    relation_type: str
    note: str
    created_at: str
    direction: Literal["in", "out"]
    peer: PeerCard | MissingPeer


class DiagramRef(TypedDict):
    memory_uid: str
    node_key: str
    relation_type: str
    title: str
    label: str | None


class MemoryRecord(TypedDict):
    rowid_pk: int
    uid: str
    type: str
    title: str
    content: str
    domain: str
    also: NotRequired[list[str]]
    tags: str
    session: str
    status: str
    confidence: str
    pin: str
    superseded_by: str | None
    review_after: str
    source_ref: str
    created_at: str
    updated_at: str
    recalls: int
    last_recall: str | None
    edit_history: list[EditEntry]
    spec: list[SectionSpec]
    sections: list[RecordSection]
    section_problem: str
    body_links: dict[str, BodyLink]
    relations: list[RecordRelation]
    superseded_by_peer: NotRequired[PeerCard | None]
    task: NotRequired[TaskRecord | None]
    diagram: NotRequired[DiagramRecord | None]
    referenced_by_diagrams: NotRequired[list[DiagramRef]]


class MetaSaved(TypedDict):
    ok: Literal[True]
    changed: list[str]
    also: NotRequired[list[str]]


class PinSaved(TypedDict):
    ok: Literal[True]
    pin: str


class Purged(TypedDict):
    ok: Literal[True]
    backup: str
    purged: int
    missing: list[str]


class BulkDone(TypedDict):
    ok: Literal[True]
    affected: int


# --------------------------------------------------------------------- tasks

class TaskAnswer(TypedDict):
    task: TaskRecord | None
    status: str


class TaskCreated(TaskAnswer):
    uid: str


# ----------------------------------------------------------------- relations

class RelationCreated(TypedDict):
    relation_id: int


class GraphNode(TypedDict):
    """A memory as the relations graph draws it; `label` is the body's opening line."""
    uid: str
    type: str
    domain: str
    also: NotRequired[list[str]]
    status: str
    confidence: str
    tags: str
    title: str
    label: str
    degree: int
    created_at: str


class GraphEdge(TypedDict):
    id: int
    from_uid: str
    to_uid: str
    relation_type: str
    note: str


class Graph(Scoped):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    total: int
    truncated: bool


# ------------------------------------------------------------------ diagrams

class DiagramIssue(TypedDict):
    kind: str
    keys: list[str]


class DiagramRow(TypedDict):
    """A diagram as the diagram list shows it: its size, its ties and what is wrong with its shape."""
    uid: str
    kind: str
    title: str
    summary: str
    domain: str
    status: str
    confidence: str
    tags: str
    created_at: str
    updated_at: str
    also: list[str]
    nodes: int
    edges: int
    links: int
    jumps: int
    documented: int
    issues: list[DiagramIssue]
    issue_count: int


class DiagramPage(Scoped):
    total: int
    with_issues: int
    items: list[DiagramRow]


class DiagramCreated(TypedDict):
    uid: str
    also: list[str]


class NodeSaved(TypedDict):
    ok: Literal[True]
    key: str


class LayoutSaved(TypedDict):
    ok: Literal[True]
    moved: int


class Mermaid(TypedDict):
    uid: str
    mermaid: str


# --------------------------------------------------------- changelog, update

class UpdateState(TypedDict):
    current: str
    latest: str
    behind: int
    url: str
    checked_at: str
    failed: bool
    enabled: bool
    interval_hours: int
    commands: list[str]


class ReleaseSection(TypedDict):
    title: str
    entries: list[str]


class Release(TypedDict):
    version: str
    date: str
    url: str
    sections: list[ReleaseSection]
    state: Literal["installed", "ahead", "past"]


class Changelog(TypedDict):
    current: str
    releases: list[Release]
    update: UpdateState
    source: bool


# -------------------------------------------------------------------- config

class ConfigSaved(TypedDict):
    domain_case: str
    svg_retention: str
    warden_enabled: bool
    warden_minutes: int
    task_ask_enabled: bool
    task_ask_minutes: int


class Config(ConfigSaved):
    sections: dict[str, list[SectionSpec]]


# ------------------------------------------------------------------- domains

class DomainEntry(TypedDict):
    domain: str
    active: int
    archived: int
    types: dict[str, int]
    latest_at: str
    parent: str
    depth: int
    children: int
    subtree_active: int
    subtree_archived: int
    also: int
    subtree_also: int
    subtree_latest_at: str
    implicit: bool
    collides_with: NotRequired[list[str]]


class DomainTree(TypedDict):
    domains: list[DomainEntry]


class DomainDetail(TypedDict):
    domain: str
    filed: list[MemorySummary]
    filed_total: int
    crossing: list[MemorySummary]


class DomainRenamed(TypedDict):
    ok: Literal[True]
    affected: int
    also_affected: int
    domains: int
    merged: Any


# "from" is a keyword, so this one is spelled as a call.
NormalizeEntry = TypedDict("NormalizeEntry", {
    "from": str, "to": str, "count": int, "action": Literal["rename", "merge"]})


class NormalizePlan(TypedDict):
    mode: str
    dry_run: Literal[True]
    plan: list[NormalizeEntry]
    renames: int
    merges: int


class NormalizeDone(TypedDict):
    ok: Literal[True]
    mode: str
    moved: int
    affected: int
    also_affected: int


class DomainStatusSaved(TypedDict):
    ok: Literal[True]
    affected: int
    domains: int
    uids: list[str]


class DomainDeleted(TypedDict):
    ok: Literal[True]
    purged: int
    unlinked: int
    domains: int


# --------------------------------------------------------------- maintenance

class Check(TypedDict):
    ok: bool
    detail: str


class FtsCheck(Check):
    rows: int
    expected: int


class OrphanCount(TypedDict):
    orphans: int


class TagCount(TypedDict):
    untagged: int
    active: int


class TitleCount(TypedDict):
    untitled: int
    active: int


class RendersUsage(TypedDict):
    files: int
    bytes: int


class Renders(RendersUsage):
    retention: str
    path: str


class BackupFile(TypedDict):
    name: str
    size: int
    mtime: str


class StoreFile(TypedDict):
    path: str
    size: int
    wal_size: int
    reclaimable: int
    compact_reason: str


class Health(TypedDict):
    project: str
    integrity: Check
    fts: FtsCheck
    relations: OrphanCount
    tags: TagCount
    title: TitleCount
    renders: Renders
    file: StoreFile
    backups: list[BackupFile]


class FtsRebuilt(TypedDict):
    ok: Literal[True]
    rows: int


class OrphansCleaned(TypedDict):
    ok: Literal[True]
    relations_removed: int
    suggestions_removed: int
    node_links_removed: int
    jumps_removed: int
    task_links_removed: int


class RendersPruned(TypedDict):
    ok: Literal[True]
    pruned: int
    bytes: int
    mode: str
    before: RendersUsage
    after: RendersUsage


class Vacuumed(TypedDict):
    ok: Literal[True]
    before: int
    after: int


class BackupTaken(TypedDict):
    ok: Literal[True]
    project: str
    path: str
    size: int


class ShelfFile(BackupFile):
    """A backup on the shelf, with what has been written about it."""
    label: NotRequired[str]
    pinned: NotRequired[bool]


class ArchiveMember(BackupFile):
    label: NotRequired[str]


class ArchiveFile(BackupFile):
    """A zip of backups: what it costs on disk, and `raw`, what it holds uncompressed."""
    count: int
    raw: int
    members: list[ArchiveMember]


class Backups(TypedDict):
    project: str
    shelf: list[ShelfFile]
    archives: list[ArchiveFile]


class ArchivePlanEntry(TypedDict):
    name: str
    added: int
    exists: bool


class ArchivePlan(TypedDict):
    ok: Literal[True]
    plan: list[ArchivePlanEntry]


class ArchiveWritten(TypedDict):
    name: str
    added: int
    size: int


class Archived(TypedDict):
    ok: Literal[True]
    archive: str
    archives: list[ArchiveWritten]
    added: int
    raw: int
    size: int


class Unarchived(TypedDict):
    ok: Literal[True]
    name: str
    restored: Any


class ArchiveDeleted(TypedDict):
    ok: Literal[True]
    name: str
    count: int


class Renamed(TypedDict):
    ok: Literal[True]
    name: str


class BackupNamed(TypedDict):
    ok: Literal[True]
    name: str
    label: str


class BackupPinned(TypedDict):
    ok: Literal[True]
    name: str
    pinned: bool


class BackupsDeleted(TypedDict):
    ok: Literal[True]
    deleted: int
    freed: int


class BackupRestored(TypedDict):
    ok: Literal[True]
    name: str
    kept: str


class DedupPair(TypedDict):
    a: MemorySummary
    b: MemorySummary
    ratio: float
    method: str


class DedupPairs(Scoped):
    pairs: list[DedupPair]
    threshold: float


class Sectionized(TypedDict):
    ok: Literal[True]
    backup: str
    total: int
    conformed: int
    rewritten: int
    needs_review: int


class SectionQueueEntry(TypedDict):
    uid: str
    type: str
    domain: str
    status: str
    detail: str
    snippet: str
    created_at: str


class SectionQueue(TypedDict):
    ok: Literal[True]
    migrated: bool
    unread: int
    queue: list[SectionQueueEntry]


# -------------------------------------------------------------- optimization

class OptimizationRun(TypedDict):
    id: int
    created_at: str
    note: str
    status: str
    backup_path: str | None


class RunKindCount(TypedDict):
    kind: str
    total: int
    pending: int
    rejected: int


class RunRow(OptimizationRun):
    """A run as the calendar lists it: its counts, overall and per kind."""
    total: int
    pending: int
    applied: int
    rejected: int
    kinds: list[RunKindCount]


class OptimizationRuns(TypedDict):
    runs: list[RunRow]


class SuggestionTarget(PeerCard):
    """The memory a suggestion edits, with the fields its Before pane reads."""
    tags: str
    review_after: str
    also: list[str]


class Suggestion(TypedDict):
    """A staged suggestion, with the cards and bodies its evidence pane draws."""
    id: int
    run_id: int
    kind: str
    target_uid: str | None
    rationale: str
    verified: str
    status: str
    decided_at: str | None
    created_at: str
    payload: dict[str, Any]
    target: NotRequired[SuggestionTarget]
    content_before: NotRequired[str]
    text_before: NotRequired[str]
    chars_before: NotRequired[int]
    chars_after: NotRequired[int]
    peers: NotRequired[dict[str, PeerCard | None]]
    sources: NotRequired[list[PeerCard | MissingPeer]]
    new_uid: NotRequired[str | None]
    body_links: NotRequired[dict[str, BodyLink]]


class Suggestions(TypedDict):
    run: OptimizationRun
    runs: list[OptimizationRun]
    suggestions: list[Suggestion]


class Ledger(TypedDict):
    memories: int
    active: int
    domains: int
    relations: int
    confirmed: int
    archived: int
    chars: int


class KindGroup(TypedDict):
    """One kind of a run: its counts, and the facts its sentence is worded from."""
    kind: str
    total: int
    pending: int
    applied: int
    rejected: int
    verified: int
    facts: dict[str, int | str]


class OptimizationSummary(TypedDict):
    run: OptimizationRun
    total: int
    pending: int
    verified: int
    ledger: Ledger
    groups: list[KindGroup]


class Applied(TypedDict):
    ok: Literal[True]
    backup: Any


class FailedApply(TypedDict):
    id: int
    error: str


class AppliedAll(TypedDict):
    ok: Literal[True]
    applied: int
    failed: list[FailedApply]
    backup: str | None
    backups: list[str]


class RejectedAll(TypedDict):
    ok: Literal[True]
    rejected: int


# ------------------------------------------------------------- audit, lookup

class AuditEntry(TypedDict):
    id: int
    memory_uid: str
    edited_at: str
    note: str
    prev_len: int | None
    new_len: int | None
    content_changed: int | None
    type: str
    domain: str
    status: str


class AuditLog(TypedDict):
    entries: list[AuditEntry]


class Lookup(TypedDict):
    items: list[Row]
    has_more: bool
