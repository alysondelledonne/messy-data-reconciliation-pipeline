"""Entity resolution: group the spellings of an organization's name into canonical
entities (D-033). Pure logic; LLM adjudication and human decisions are passed in."""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from pipeline.reconcile.names import (
    ABBREVIATIONS,
    lookalike_guard,
    normalize,
    similarity,
    tokens_raw,
)
from pipeline.reconcile.types import Candidate, ReconcileSpec


@dataclass
class NameGroup:
    """All mentions whose names normalize to the same string."""

    norm: str
    spellings: Counter[str] = field(default_factory=Counter)
    same_keys: set[str] = field(default_factory=set)
    family_keys: set[str] = field(default_factory=set)
    from_documents: int = 0  # mentions on the organization's own documents (letterhead)

    @property
    def count(self) -> int:
        return sum(self.spellings.values())

    def display(self) -> str:
        return self.spellings.most_common(1)[0][0]


@dataclass(frozen=True)
class PairVerdict:
    a: str
    b: str
    decision: str  # merge | separate | gray
    score: float
    reason: str
    guard: tuple[str, ...] = ()


@dataclass
class EntityCluster:
    groups: list[NameGroup]

    @property
    def canonical(self) -> str:
        """The spelling to show: well-formed (mixed case), of the most-mentioned name (not
        a typo of it), without abbreviations, with its legal suffix, seen on the
        organization's own documents, then the most common."""
        dominant = max(self.groups, key=lambda g: (g.count, g.from_documents)).norm

        def score(item: tuple[str, int, int, str]) -> tuple[int, ...]:
            spelling, n, docs, norm = item
            words = tokens_raw(spelling)
            return (
                int(not spelling.isupper() and not spelling.islower()),
                int(norm == dominant),
                int(not any(w in ABBREVIATIONS for w in words)),
                int(len(words) > len(normalize(spelling).split())),
                int(docs > 0),
                n,
                len(spelling),
            )

        items = [
            (sp, n, g.from_documents if sp == g.display() else 0, g.norm)
            for g in self.groups
            for sp, n in g.spellings.items()
        ]
        return max(items, key=score)[0]

    def aliases(self) -> Counter[str]:
        out: Counter[str] = Counter()
        for g in self.groups:
            out.update(g.spellings)
        return out


def name_groups(spec: ReconcileSpec, candidates: list[Candidate]) -> dict[str, NameGroup]:
    groups: dict[str, NameGroup] = {}
    for c in candidates:
        name = c.values.get(spec.entity_field)
        if not name or not str(name).strip():
            continue
        spelling = " ".join(str(name).split())
        norm = normalize(spelling)
        if not norm:
            continue
        g = groups.setdefault(norm, NameGroup(norm))
        g.spellings[spelling] += 1
        same, family = spec.evidence(c)
        g.same_keys |= same
        g.family_keys |= family
        g.from_documents += c.format in ("pdf", "pdf_scanned")
    return groups


def judge_pair(a: NameGroup, b: NameGroup, auto: float, review: float) -> PairVerdict:
    score = similarity(a.norm, b.norm)
    guard = lookalike_guard(a.norm, b.norm)
    shared = a.same_keys & b.same_keys
    disjoint = bool(a.family_keys and b.family_keys and not (a.family_keys & b.family_keys))
    if disjoint:
        if score >= review:
            return PairVerdict(
                a.norm,
                b.norm,
                "gray",
                score,
                "similar names but no evidence in common",
                guard.distinct,
            )
        return PairVerdict(a.norm, b.norm, "separate", score, "different evidence")
    if shared and score >= 70 and not guard:
        return PairVerdict(a.norm, b.norm, "merge", score, f"shared evidence ({sorted(shared)[0]})")
    if score >= auto and not guard:
        return PairVerdict(a.norm, b.norm, "merge", score, f"names match ({score:.0f})")
    if score >= review:
        why = f"words differ: {', '.join(guard.distinct)}" if guard else f"score {score:.0f}"
        return PairVerdict(a.norm, b.norm, "gray", score, why, guard.distinct)
    return PairVerdict(a.norm, b.norm, "separate", score, f"score {score:.0f}")


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def resolve(
    groups: dict[str, NameGroup],
    auto: float,
    review: float,
    decide_gray: Callable[[PairVerdict, NameGroup, NameGroup], str],
) -> tuple[list[EntityCluster], list[PairVerdict]]:
    """Cluster name groups. `decide_gray` returns merge / separate / pending for gray pairs
    (from a human decision, the LLM, or nothing). Returns clusters and every non-trivial
    pair verdict (merged and gray), for the record."""
    norms = sorted(groups, key=lambda n: (-groups[n].count, n))
    verdicts: list[PairVerdict] = []
    merges: list[PairVerdict] = []
    decided: set[frozenset[str]] = set()  # gray pairs explicitly decided "merge"
    for i, na in enumerate(norms):
        for nb in norms[i + 1 :]:
            v = judge_pair(groups[na], groups[nb], auto, review)
            if v.decision == "gray":
                outcome = decide_gray(v, groups[na], groups[nb])
                if outcome == "merge":
                    decided.add(frozenset((v.a, v.b)))
                v = PairVerdict(
                    v.a,
                    v.b,
                    "merge"
                    if outcome == "merge"
                    else ("separate" if outcome == "separate" else "gray"),
                    v.score,
                    v.reason,
                    v.guard,
                )
            if v.decision != "separate":
                verdicts.append(v)
            if v.decision == "merge":
                merges.append(v)

    uf = _UnionFind(norms)
    members: dict[str, set[str]] = {n: {n} for n in norms}
    blocked = {(v.a, v.b) for v in verdicts if v.decision == "gray"} | {
        (v.b, v.a) for v in verdicts if v.decision == "gray"
    }
    for v in sorted(merges, key=lambda v: -v.score):
        ra, rb = uf.find(v.a), uf.find(v.b)
        if ra == rb:
            continue
        # Don't let a chain of merges join two names that were judged separately or are
        # still undecided (an alias bridging two lookalike vendors).
        explicit = frozenset((v.a, v.b)) in decided
        if not explicit and any(
            (x, y) in blocked
            or (frozenset((x, y)) not in decided and _judged_separate(groups[x], groups[y]))
            for x in members[ra]
            for y in members[rb]
        ):
            continue
        uf.union(v.a, v.b)
        root = uf.find(v.a)
        members[root] = members.pop(ra, set()) | members.pop(rb, set())
    clusters: dict[str, list[NameGroup]] = defaultdict(list)
    for n in norms:
        clusters[uf.find(n)].append(groups[n])
    return [EntityCluster(gs) for gs in clusters.values()], verdicts


def _judged_separate(a: NameGroup, b: NameGroup) -> bool:
    """Two members that, compared directly, carry conflicting evidence or guarded words."""
    disjoint = bool(a.family_keys and b.family_keys and not (a.family_keys & b.family_keys))
    return disjoint or bool(lookalike_guard(a.norm, b.norm))
