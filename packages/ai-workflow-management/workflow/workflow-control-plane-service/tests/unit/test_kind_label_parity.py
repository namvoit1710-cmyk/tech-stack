"""SA-1737 (C): the palette kind-label vocabulary must equal the canonical VALID_NODE_KINDS.

Guards drift between the catalog's display labels/order and the kind vocabulary that
worker-sdk / control-plane validate against (the SA-1558 case-drift lesson, kind edition).
"""

from app.layer1_domain.value_objects.node_kind import VALID_NODE_KINDS
from app.layer2_application.providers.category_metadata import _KIND_LABEL, _KIND_ORDER


def test_kind_label_covers_exactly_the_vocab():
    assert set(_KIND_LABEL) == set(VALID_NODE_KINDS)
    assert set(_KIND_ORDER) == set(VALID_NODE_KINDS)
