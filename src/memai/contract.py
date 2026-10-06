"""The constants Python and the dashboard share, read once from contract.json."""

from __future__ import annotations

import json
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("contract.json").read_text(encoding="utf-8"))
_MEMORY = _DATA["memory"]
_TASK = _DATA["task"]
_ADMIN = _DATA["admin"]
_DIAGRAM = _DATA["diagram"]

MEMORY_TYPES: tuple[str, ...] = tuple(_MEMORY["TYPES"])
CONFIDENCES: tuple[str, ...] = tuple(_MEMORY["CONFIDENCES"])
PINS: tuple[str, ...] = tuple(_MEMORY["PINS"])
TITLE_MAX: int = _MEMORY["TITLE_MAX"]
DOMAIN_SEP: str = _MEMORY["DOMAIN_SEP"]

TASK_STATES: tuple[str, ...] = tuple(_TASK["STATES"])
ITEM_STATES: tuple[str, ...] = tuple(_TASK["ITEM_STATES"])
GOAL_MAX: int = _TASK["GOAL_MAX"]
ITEM_MAX: int = _TASK["ITEM_MAX"]
ITEMS_MAX: int = _TASK["ITEMS_MAX"]
NOTE_MAX: int = _TASK["NOTE_MAX"]

BULK_MAX: int = _ADMIN["BULK_MAX"]
ARCHIVE_LABEL_MAX: int = _ADMIN["ARCHIVE_LABEL_MAX"]

# Floats: the SVG export prints these, and its output is compared byte for byte.
NODE_SHAPES: tuple[str, ...] = tuple(_DIAGRAM["SHAPES"])
NODE_W = float(_DIAGRAM["NODE_W"])
NODE_H = float(_DIAGRAM["NODE_H"])
DECISION_H = float(_DIAGRAM["DECISION_H"])
NODE_MIN_W = float(_DIAGRAM["NODE_MIN_W"])
NODE_MAX_W = float(_DIAGRAM["NODE_MAX_W"])
NODE_MIN_H = float(_DIAGRAM["NODE_MIN_H"])
NODE_MAX_H = float(_DIAGRAM["NODE_MAX_H"])
IO_SKEW = float(_DIAGRAM["IO_SKEW"])
ARROW_GAP = float(_DIAGRAM["ARROW_GAP"])
ARROW_LEN = float(_DIAGRAM["ARROW_LEN"])
ARROW_FLARE = float(_DIAGRAM["ARROW_FLARE"])
ORTH_STUB = float(_DIAGRAM["ORTH_STUB"])
FAN_GAP = float(_DIAGRAM["FAN_GAP"])
FAN_STUB = float(_DIAGRAM["FAN_STUB"])
MERGE_GAP = float(_DIAGRAM["MERGE_GAP"])
MERGE_TAIL = float(_DIAGRAM["MERGE_TAIL"])
ORTH_RADIUS = float(_DIAGRAM["ORTH_RADIUS"])
ORTH_SNAP = float(_DIAGRAM["ORTH_SNAP"])
LABEL_PX = float(_DIAGRAM["LABEL_PX"])
LABEL_LH = float(_DIAGRAM["LABEL_LH"])
# below these zoom scales neither the canvas nor the SVG shell draws the label
LABEL_MIN_SCALE = float(_DIAGRAM["LABEL_MIN_SCALE"])
BADGE_PX = float(_DIAGRAM["BADGE_PX"])
BADGE_WORDS: int = _DIAGRAM["BADGE_WORDS"]
BADGE_CHARS: int = _DIAGRAM["BADGE_CHARS"]
BADGE_MIN_SCALE = float(_DIAGRAM["BADGE_MIN_SCALE"])
RING_GROW = float(_DIAGRAM["RING_GROW"])
BADGE_OUT = float(_DIAGRAM["BADGE_OUT"])
