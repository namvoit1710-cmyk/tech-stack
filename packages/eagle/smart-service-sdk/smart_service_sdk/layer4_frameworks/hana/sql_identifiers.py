import re

_SCHEMA_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_]*$")


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def validate_schema_identifier(identifier: str) -> str:
    if not _SCHEMA_IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(
            "HANA schema identifiers must start with a letter or underscore and contain only letters, digits, and underscores"
        )
    return identifier
