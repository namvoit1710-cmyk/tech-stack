"""Parse v2 node-catalog refs of the form ``<id>`` or ``<id>@<version>``.

Node refs use ``:`` internally to separate provider from type
(``worker:http-request``) and ``@`` to attach an optional spec_version
(``worker:http-request@2.1.0``). The ``@`` delimiter is chosen so the
presence of ``:`` inside ``<id>`` doesn't confuse the parser.
"""

from __future__ import annotations


def parse_node_ref(ref: str) -> tuple[str, str | None]:
    """Split ``ref`` into ``(node_id, spec_version | None)``.

    Examples::

        parse_node_ref("builtin:condition")             -> ("builtin:condition", None)
        parse_node_ref("worker:http@1.2.0")             -> ("worker:http", "1.2.0")
        parse_node_ref("business:abc@0.9.1")            -> ("business:abc", "0.9.1")

    Raises:
        ValueError: if ``ref`` is empty, or has more than one ``@``, or the
            ``<version>`` segment is empty.
    """
    if not ref:
        raise ValueError("node ref cannot be empty")

    parts = ref.split("@")
    if len(parts) == 1:
        return parts[0], None
    if len(parts) != 2:
        raise ValueError(
            f"node ref {ref!r} has too many '@' separators"
        )

    node_id, version = parts
    if not node_id:
        raise ValueError(f"node ref {ref!r}: id cannot be empty")
    if not version:
        raise ValueError(f"node ref {ref!r}: version cannot be empty")
    return node_id, version
