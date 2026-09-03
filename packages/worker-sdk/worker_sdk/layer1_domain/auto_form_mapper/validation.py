"""Validation helper functions."""

from __future__ import annotations

from typing import Optional

from .models import ValidationRule


def required() -> list[ValidationRule]:
    """Simple required field validation."""
    return [ValidationRule(method="required")]


def required_string(min_length: int = 1, max_length: int = 255) -> list[ValidationRule]:
    """Required string with length bounds."""
    return [
        ValidationRule(method="required"),
        ValidationRule(method="min", value=min_length, message=f"Minimum {min_length} characters"),
        ValidationRule(method="max", value=max_length, message=f"Maximum {max_length} characters"),
    ]


def required_email() -> list[ValidationRule]:
    """Required email field."""
    return [
        ValidationRule(method="required"),
        ValidationRule(
            method="pattern",
            value=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
            message="Must be a valid email address",
        ),
    ]


def required_number(
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
    integer: bool = False,
) -> list[ValidationRule]:
    """Required number with optional bounds."""
    rules: list[ValidationRule] = [ValidationRule(method="required")]
    if min_val is not None:
        rules.append(ValidationRule(method="gte", value=min_val, message=f"Must be at least {min_val}"))
    if max_val is not None:
        rules.append(ValidationRule(method="lte", value=max_val, message=f"Must be at most {max_val}"))
    if integer:
        rules.append(ValidationRule(method="int", message="Must be a whole number"))
    return rules


def optional_string(max_length: int = 255) -> list[ValidationRule]:
    """Optional string with max length."""
    return [
        ValidationRule(method="max", value=max_length, message=f"Maximum {max_length} characters"),
    ]


def url_validation() -> list[ValidationRule]:
    """Required URL validation."""
    return [
        ValidationRule(method="required"),
        ValidationRule(
            method="pattern",
            value=r"^https?://[^\s/$.?#].[^\s]*$",
            message="Must be a valid URL",
        ),
    ]
