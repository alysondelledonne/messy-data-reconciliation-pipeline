"""In-memory corpus builder shared by all packs.

Packs add files as bytes. Writing to disk and hashing happen in one place, so
determinism can be checked without touching the filesystem.
"""

import hashlib
import random
from dataclasses import dataclass, field

from data_gen.ground_truth import GroundTruth


@dataclass
class PackOutput:
    files: dict[str, bytes] = field(default_factory=dict)
    ground_truth: GroundTruth | None = None

    def add(self, path: str, data: bytes) -> None:
        if path in self.files:
            raise ValueError(f"duplicate output path: {path}")
        self.files[path] = data


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sub_rng(seed: int, *scope: str) -> random.Random:
    """An independent, reproducible RNG for one part of the generator.

    Seeding from a string is stable across Python processes (it doesn't go through
    hash(), which is randomized). Scoping means that changing one pack or stage
    leaves every other one's output unchanged.
    """
    return random.Random(":".join([str(seed), *scope]))
