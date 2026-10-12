"""Organization-name normalization, similarity and the lookalike guard (D-033)."""

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

# Legal and generic suffixes that don't distinguish one organization from another.
SUFFIXES = frozenset(
    {
        "llc", "inc", "incorporated", "co", "company", "corp", "corporation", "ltd",
        "limited", "gmbh", "sarl", "lda", "plc", "llp", "lp", "ag", "sa", "group",
    }
)  # fmt: skip
ABBREVIATIONS = {
    "svcs": "services", "svc": "service", "mech": "mechanical", "elec": "electrical",
    "sys": "systems", "intl": "international", "mgmt": "management", "props": "properties",
    "assoc": "associates", "sup": "supply", "prot": "protection", "contr": "contractors",
    "bros": "brothers", "dept": "department",
}  # fmt: skip


def fold(s: str) -> str:
    """Lower-case and strip accents: 'Côté Mécanique' -> 'cote mecanique'."""
    norm = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in norm if not unicodedata.combining(ch)).lower()


def tokens_raw(name: str) -> list[str]:
    """Folded words as written: no expansion, suffixes kept."""
    s = fold(name).replace("&", " and ")
    s = re.sub(r"\bl\.?\s?l\.?\s?c\b", "llc", s)
    return [w for w in re.split(r"[^a-z0-9]+", s) if w and w != "and"]


def tokens(name: str) -> list[str]:
    out = [ABBREVIATIONS.get(w, w) for w in tokens_raw(name)]
    while out and (out[-1] in SUFFIXES or len(out[-1]) == 1):  # also cut-off "L" of "LLC"
        out.pop()
    return out


def normalize(name: str) -> str:
    return " ".join(tokens(name))


def similarity(a: str, b: str) -> float:
    """0-100 on normalized names: the better of word-order-free and plain similarity."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return max(fuzz.token_sort_ratio(na, nb), fuzz.ratio(na, nb))


def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):  # truncated export
        return True
    return len(a) >= 5 and len(b) >= 5 and fuzz.ratio(a, b) >= 85  # one-letter typo


@dataclass(frozen=True)
class Guard:
    """Words that make two similar names likely to be different organizations."""

    distinct: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.distinct)


def lookalike_guard(a: str, b: str) -> Guard:
    """Words in one name with no counterpart in the other ('electrical' vs 'elevator',
    'realty' with nothing opposite it). Typos, truncations and abbreviations match."""
    ta, tb = tokens(a), tokens(b)
    unmatched = [w for w in ta if not any(_same_word(w, x) for x in tb)]
    unmatched += [w for w in tb if not any(_same_word(w, x) for x in ta)]
    return Guard(tuple(sorted(set(unmatched))))
