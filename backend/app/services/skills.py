"""Canonical skill names and safe matching helpers.

Skill names are user-facing strings, so the value stored as ``name`` is kept
exactly as entered (apart from surrounding whitespace).  Matching and
uniqueness use a conservative normalized key instead.  In particular, the
normalizer retains ``+`` and ``#`` so that ``C++`` and ``C#`` can never
silently collapse into the same skill.
"""

from __future__ import annotations

import re
import unicodedata


class SkillNormalizationError(ValueError):
    """Raised when a skill name or alias is blank or not text."""


def display_skill_name(value: object) -> str:
    """Return a clean user-facing value without changing its spelling."""

    if not isinstance(value, str):
        raise SkillNormalizationError("skill names and aliases must be strings")
    value = unicodedata.normalize("NFKC", value).strip()
    if not value:
        raise SkillNormalizationError("skill names and aliases cannot be blank")
    return value


def normalize_skill_name(value: object) -> str:
    """Return the stable key used for equality and relationship matching.

    Whitespace and ordinary separators are normalized, so ``Node.js`` and
    ``node js`` deliberately share a key.  Programming language markers are
    retained, which keeps ``C++`` and ``C#`` distinct rather than collapsing
    both to ``c``.  This still errs on the side of requiring an explicit alias
    for spellings that are not ordinary separator variants.
    """

    value = display_skill_name(value).casefold()
    value = re.sub(r"[\u2010-\u2015\u2212]", "-", value)
    value = re.sub(r"[._/\\]+", " ", value)
    value = re.sub(r"[^\w+#-]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip(" -")
    if not value:
        raise SkillNormalizationError("skill names and aliases cannot be blank")
    return value


# A short alias is useful to callers and keeps older integrations readable.
normalize_skill = normalize_skill_name
