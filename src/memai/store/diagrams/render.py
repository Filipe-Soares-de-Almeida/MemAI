"""A diagram's graph rendered as the text body FTS indexes and as Mermaid."""

from __future__ import annotations

from memai.store.diagrams.layout import _flow_order

# Cap on a rendered body returned to an agent; the stored content is never truncated.
DIAGRAM_BODY_BUDGET = 12_000


def _render_text(title: str, summary: str, kind: str, nodes: list[dict], edges: list[dict]) -> str:
    """The prose projection stored in memories.content.

    Generated, never hand-written: this is what FTS indexes, so a diagram
    is findable by what the routine actually does rather than by its title
    alone. One line per node in
    flow order keeps the edit-history diff readable.
    """
    by_key = {n["key"]: n for n in nodes}
    out = [f"DIAGRAM: {title}", f"KIND: {kind}"]
    if summary:
        out.append(f"SUMMARY: {summary}")
    out += ["", "FLOW:"]
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for e in sorted(edges, key=lambda x: x["seq"]):
        outgoing.setdefault(e["from"], []).append((e["to"], e["label"]))
    ordered = _flow_order(nodes, edges)
    for k in ordered:
        n = by_key[k]
        out.append(f"{k} [{n['shape']}]: {n['label']}")
        for to, label in outgoing.get(k, []):
            out.append(f"  -> {to}" + (f" [{label}]" if label else ""))
    notes = [(k, by_key[k]["note"]) for k in ordered if by_key[k]["note"]]
    if notes:
        out += ["", "NOTES:"]
        out += [f"{k}: {note}" for k, note in notes]
    return "\n".join(out)


def _mermaid_escape(text: str) -> str:
    """Quotes and newlines would break out of a mermaid node label."""
    return text.replace('"', "#quot;").replace("\n", " ")


# Mermaid keywords that cannot be bare node ids; `end` closes a subgraph, so a step keyed 'end'
# silently breaks the diagram.
_MERMAID_RESERVED = frozenset({
    "end", "graph", "subgraph", "flowchart", "class", "classdef",
    "click", "style", "linkstyle", "direction",
})


def _mermaid_id(key: str) -> str:
    return f"n_{key}" if key.lower() in _MERMAID_RESERVED else key


def _mermaid_node(key: str, shape: str, label: str) -> str:
    node_id = _mermaid_id(key)
    text = _mermaid_escape(label)
    if shape in ("start", "end"):
        return f'{node_id}(["{text}"])'
    if shape == "decision":
        return f'{node_id}{{"{text}"}}'
    if shape == "io":
        return f'{node_id}[/"{text}"/]'
    return f'{node_id}["{text}"]'


def _render_mermaid(title: str, nodes: list[dict], edges: list[dict]) -> str:
    """Mermaid source, for a host that can render a fenced diagram inline.

    Mermaid always applies its own layout, so this is the one renderer
    that ignores the stored coordinates -- the price of a one-line render
    in a chat client. Consumers that want the exact admin arrangement read
    the coordinates from get_diagram() instead.
    """
    by_key = {n["key"]: n for n in nodes}
    lines = []
    if title:
        lines += ["---", f"title: {_mermaid_escape(title)}", "---"]
    lines.append("flowchart TD")
    for k in _flow_order(nodes, edges):
        lines.append("    " + _mermaid_node(k, by_key[k]["shape"], by_key[k]["label"]))
    for e in sorted(edges, key=lambda x: x["seq"]):
        label = f'|"{_mermaid_escape(e["label"])}"|' if e["label"] else ""
        lines.append(f'    {_mermaid_id(e["from"])} -->{label} {_mermaid_id(e["to"])}')
    return "\n".join(lines)
