"""A path written for someone to run or copy, spelled so that a shell reads it back as one word."""

from __future__ import annotations

import re

# What both sh and cmd.exe read as part of a bare word: whitespace, a backslash and the
# punctuation either shell gives a meaning to all fall outside it.
_BARE = re.compile(r"[\w@:./-]+")


def quote(word: str) -> str:
    """`word` bare when sh and cmd.exe both read it as one word, else in double quotes.

    Nothing inside the quotes is escaped, since the two shells escape differently: a word holding
    a double quote, a `$`, a backtick or a `%` has no one spelling both read back unchanged.
    """
    return word if _BARE.fullmatch(word) else f'"{word}"'
