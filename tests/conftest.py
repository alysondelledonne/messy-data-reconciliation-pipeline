import pytest

from data_gen.build import build
from data_gen.ground_truth import GroundTruth

SEED = 42


@pytest.fixture(scope="session")
def corpus() -> dict[str, bytes]:
    """The full default corpus, built once per test session (in memory)."""
    return build(SEED)


def load_gt(corpus: dict[str, bytes], pack: str) -> GroundTruth:
    return GroundTruth.model_validate_json(corpus[f"{pack}/ground_truth.json"])


@pytest.fixture(scope="session")
def invoices_gt(corpus: dict[str, bytes]) -> GroundTruth:
    return load_gt(corpus, "invoices")


@pytest.fixture(scope="session")
def contacts_gt(corpus: dict[str, bytes]) -> GroundTruth:
    return load_gt(corpus, "contacts")
