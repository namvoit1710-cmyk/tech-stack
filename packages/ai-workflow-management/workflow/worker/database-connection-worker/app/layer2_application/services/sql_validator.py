"""SQL Validator - Security layer for validating and sanitizing SQL queries.

This module provides critical security checks to prevent destructive operations
when only SELECT queries are expected, and enforces resource limits.
"""

import re
from dataclasses import dataclass
from typing import Optional

from app.layer1_domain.value_objects.operation_type import OperationType


# Patterns for detecting destructive SQL keywords
_DESTRUCTIVE_KEYWORDS = [
    r"\bDROP\b",
    r"\bDELETE\b",
    r"\bTRUNCATE\b",
    r"\bALTER\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
    r"\bCREATE\b",
    r"\bINSERT\b",
    r"\bUPDATE\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
    r"\bCALL\b",
    r"\bSET\b",
    r"\bMERGE\b",
    r"\bUPSERT\b",
]

_DESTRUCTIVE_PATTERN = re.compile(
    "|".join(_DESTRUCTIVE_KEYWORDS),
    re.IGNORECASE | re.MULTILINE,
)

# Pattern to detect existing LIMIT clause
_LIMIT_PATTERN = re.compile(
    r"\bLIMIT\s+\d+",
    re.IGNORECASE | re.MULTILINE,
)

# Pattern to detect operation type from SQL
_OPERATION_PATTERNS = {
    OperationType.SELECT: re.compile(r"^\s*SELECT\b", re.IGNORECASE),
    OperationType.INSERT: re.compile(r"^\s*INSERT\b", re.IGNORECASE),
    OperationType.UPDATE: re.compile(r"^\s*UPDATE\b", re.IGNORECASE),
    OperationType.DELETE: re.compile(r"^\s*DELETE\b", re.IGNORECASE),
    OperationType.DDL: re.compile(
        r"^\s*(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b",
        re.IGNORECASE,
    ),
}

# Pattern to detect SQL comments that might hide malicious code
_COMMENT_PATTERN = re.compile(
    r"(/\*.*?\*/|--.*?$)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

# Pattern to detect multiple statements (;)
_MULTI_STATEMENT_PATTERN = re.compile(r";.*\S", re.MULTILINE | re.DOTALL)


@dataclass
class ValidationResult:
    """Result of SQL validation."""
    
    is_valid: bool
    error: Optional[str] = None
    sanitized_sql: Optional[str] = None
    detected_operation: Optional[OperationType] = None


class SQLValidator:
    """Validates and sanitizes SQL queries for security and resource limits."""
    
    def __init__(
        self,
        max_rows: int = 1000,
        allow_multi_statement: bool = False,
        allow_comments: bool = True,
    ) -> None:
        """Initialize validator.
        
        Args:
            max_rows: Maximum rows to return for SELECT queries.
            allow_multi_statement: Whether to allow multiple SQL statements.
            allow_comments: Whether to allow SQL comments.
        """
        self.max_rows = max_rows
        self.allow_multi_statement = allow_multi_statement
        self.allow_comments = allow_comments
    
    def detect_operation_type(self, sql: str) -> Optional[OperationType]:
        """Detect the operation type from SQL statement.
        
        Args:
            sql: The SQL statement to analyze.
            
        Returns:
            The detected OperationType, or None if unknown.
        """
        normalized = sql.strip()
        
        # Remove leading comments to get actual statement
        if not self.allow_comments:
            normalized = _COMMENT_PATTERN.sub("", normalized).strip()
        
        for op_type, pattern in _OPERATION_PATTERNS.items():
            if pattern.match(normalized):
                return op_type
        
        return None
    
    def validate_select_only(self, sql: str) -> ValidationResult:
        """Validate that SQL is a safe SELECT statement with no destructive keywords.
        
        This is used when operation_type is explicitly "select" to enforce
        read-only queries.
        
        Args:
            sql: The SQL statement to validate.
            
        Returns:
            ValidationResult with validation status and sanitized SQL.
        """
        if not sql or not sql.strip():
            return ValidationResult(
                is_valid=False,
                error="SQL statement cannot be empty",
            )
        
        normalized = sql.strip()
        
        # Check for multiple statements
        if not self.allow_multi_statement and _MULTI_STATEMENT_PATTERN.search(normalized):
            return ValidationResult(
                is_valid=False,
                error="Multiple SQL statements are not allowed. Use single SELECT statement.",
            )
        
        # Detect operation type
        detected_op = self.detect_operation_type(normalized)
        if detected_op != OperationType.SELECT:
            return ValidationResult(
                is_valid=False,
                error=f"Expected SELECT statement but detected '{detected_op.value if detected_op else 'unknown'}' operation",
                detected_operation=detected_op,
            )
        
        # Check for destructive keywords (even in subqueries or CTEs)
        match = _DESTRUCTIVE_PATTERN.search(normalized)
        if match:
            keyword = match.group(0).upper()
            return ValidationResult(
                is_valid=False,
                error=f"Destructive keyword '{keyword}' is not allowed in SELECT queries",
                detected_operation=detected_op,
            )
        
        return ValidationResult(
            is_valid=True,
            sanitized_sql=normalized,
            detected_operation=OperationType.SELECT,
        )
    
    def validate_operation(
        self,
        sql: str,
        expected_operation: OperationType,
    ) -> ValidationResult:
        """Validate SQL matches expected operation type.
        
        Args:
            sql: The SQL statement to validate.
            expected_operation: The expected operation type.
            
        Returns:
            ValidationResult with validation status.
        """
        if not sql or not sql.strip():
            return ValidationResult(
                is_valid=False,
                error="SQL statement cannot be empty",
            )
        
        normalized = sql.strip()
        
        # Check for multiple statements
        if not self.allow_multi_statement and _MULTI_STATEMENT_PATTERN.search(normalized):
            return ValidationResult(
                is_valid=False,
                error="Multiple SQL statements are not allowed",
            )
        
        # Detect actual operation
        detected_op = self.detect_operation_type(normalized)
        
        if detected_op is None:
            return ValidationResult(
                is_valid=False,
                error="Unable to determine SQL operation type",
            )
        
        # For SELECT, do strict validation
        if expected_operation == OperationType.SELECT:
            return self.validate_select_only(normalized)
        
        # For other operations, just verify it matches expected
        if detected_op != expected_operation:
            return ValidationResult(
                is_valid=False,
                error=f"Expected '{expected_operation.value}' but detected '{detected_op.value}'",
                detected_operation=detected_op,
            )
        
        return ValidationResult(
            is_valid=True,
            sanitized_sql=normalized,
            detected_operation=detected_op,
        )
    
    def inject_limit(self, sql: str, max_rows: Optional[int] = None) -> str:
        """Inject LIMIT clause into SELECT query if not present.
        
        Args:
            sql: The SELECT SQL statement.
            max_rows: Override for maximum rows (uses self.max_rows if None).
            
        Returns:
            SQL with LIMIT clause injected.
        """
        limit = max_rows if max_rows is not None else self.max_rows
        normalized = sql.strip().rstrip(";")
        
        # Check if LIMIT already exists
        if _LIMIT_PATTERN.search(normalized):
            return normalized
        
        # Inject LIMIT at the end
        return f"{normalized} LIMIT {limit}"
    
    def sanitize_for_logging(self, sql: str, max_length: int = 500) -> str:
        """Truncate and sanitize SQL for safe logging.
        
        Args:
            sql: The SQL statement.
            max_length: Maximum length of output.
            
        Returns:
            Sanitized SQL suitable for logging.
        """
        if len(sql) > max_length:
            return sql[:max_length] + "... [TRUNCATED]"
        return sql
