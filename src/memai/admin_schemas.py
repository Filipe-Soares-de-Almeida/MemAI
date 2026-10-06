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

class MemoryPage(Scoped):
    total: int
    items: list[Row]
    searched: bool


class MemoryCreated(TypedDict):
    uid: str
    also: list[str]


class SectionSpec(TypedDict):
    key: str
    label: str
    max_len: int


class TaskRecord(TypedDict):
    goal: str
    state: str
    completed_at: str
    items: list[Row]
    comments: list[Row]
    notes: list[Row]


class DiagramRecord(TypedDict):
    uid: str
    kind: str
    title: str
    summary: str
    font_scale: float
    nodes: list[Row]
    edges: list[Row]
    links: list[Row]
    jumps: list[Row]
    mermaid: str


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
    edit_history: list[Row]
    spec: list[SectionSpec]
    sections: list[Row]
    section_problem: str
    body_links: dict[str, Row]
    relations: list[Row]
    superseded_by_peer: NotRequired[Row | None]
    task: NotRequired[TaskRecord | None]
    diagram: NotRequired[DiagramRecord | None]
    referenced_by_diagrams: NotRequired[list[Row]]


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


class Graph(Scoped):
    nodes: list[Row]
    edges: list[Row]
    total: int
    truncated: bool


# ------------------------------------------------------------------ diagrams

class DiagramPage(Scoped):
    total: int
    with_issues: int
    items: list[Row]


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


class Changelog(TypedDict):
    current: str
    releases: list[Row]
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
    filed: list[Row]
    filed_total: int
    crossing: list[Row]


class DomainRenamed(TypedDict):
    ok: Literal[True]
    affected: int
    also_affected: int
    domains: int
    merged: Any


class NormalizePlan(TypedDict):
    mode: str
    dry_run: Literal[True]
    plan: list[Row]
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
    backups: list[Row]


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


class Backups(TypedDict):
    project: str
    shelf: list[Row]
    archives: list[Row]


class ArchivePlanEntry(TypedDict):
    name: str
    added: int
    exists: bool


class ArchivePlan(TypedDict):
    ok: Literal[True]
    plan: list[ArchivePlanEntry]


class Archived(TypedDict):
    ok: Literal[True]
    archive: str
    archives: list[Row]
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


class DedupPairs(Scoped):
    pairs: list[Row]
    threshold: float


class Sectionized(TypedDict):
    ok: Literal[True]
    backup: str
    total: int
    conformed: int
    rewritten: int
    needs_review: int


class SectionQueue(TypedDict):
    ok: Literal[True]
    migrated: bool
    unread: int
    queue: list[Row]


# -------------------------------------------------------------- optimization

class OptimizationRun(TypedDict):
    id: int
    created_at: str
    note: str
    status: str
    backup_path: str | None


class OptimizationRuns(TypedDict):
    runs: list[Row]


class Suggestions(TypedDict):
    run: OptimizationRun
    runs: list[OptimizationRun]
    suggestions: list[Row]


class Ledger(TypedDict):
    memories: int
    active: int
    domains: int
    relations: int
    confirmed: int
    archived: int
    chars: int


class OptimizationSummary(TypedDict):
    run: OptimizationRun
    total: int
    pending: int
    verified: int
    ledger: Ledger
    groups: list[Row]


class Applied(TypedDict):
    ok: Literal[True]
    backup: Any


class AppliedAll(TypedDict):
    ok: Literal[True]
    applied: int
    failed: list[Any]
    backup: str | None
    backups: list[str]


class RejectedAll(TypedDict):
    ok: Literal[True]
    rejected: int


# ------------------------------------------------------------- audit, lookup

class AuditLog(TypedDict):
    entries: list[Row]


class Lookup(TypedDict):
    items: list[Row]
    has_more: bool
