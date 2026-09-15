"""Execute Task Use Case for Database Connection Worker.

This module handles execution of database operations including:
- SELECT queries (read-only with row limits)
- INSERT/UPDATE/DELETE operations (with transaction support)
- Connection testing
"""

import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.interfaces.logger_interface import ILogger
from worker_sdk.layer2_application.interfaces.monitor_interface import IMonitor
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer2_application.services.effective_auth import resolve_effective_auth
from worker_sdk.layer1_domain.auto_form_mapper import (
    AutoFormField,
    ConditionConfig,
    ConditionOperator,
    FieldConfig,
    FieldControl,
    FieldWrapper,
    OutputType,
    ValidationRule,
    number_field,
    select_field,
    switch_field,
    text_field,
    textarea_field,
)
from app.layer1_domain.value_objects.database_type import DatabaseType
from app.layer1_domain.value_objects.operation_type import OperationType
from app.layer2_application.services.sql_validator import SQLValidator
from app.layer2_application.services.database_engine import (
    ConnectionConfig,
    DatabaseEngineFactory,
)


# -- Constants ----------------------------------------------------------------

DEFAULT_MAX_ROWS = 1000
DEFAULT_TIMEOUT = 30
DEFAULT_POOL_SIZE = 5


# -- Input Schema -------------------------------------------------------------

_INPUT_FIELDS: list[AutoFormField] = [
    # Database Type
    select_field(
        key="db_type",
        label="Database Type",
        options=[
            (DatabaseType.POSTGRESQL.value, "PostgreSQL"),
            (DatabaseType.MYSQL.value, "MySQL"),
            (DatabaseType.HANA.value, "SAP HANA"),
        ],
        placeholder="Select database type",
        rules=[ValidationRule(method="required")],
    ),
    # Host — SA-1564: optional override. Derived from the chosen credential when it
    # carries a host (generic-database bundle); only required when the credential is a
    # login-only (BASIC_AUTH) one that has no host.
    text_field(
        key="host",
        label="Host",
        placeholder="optional — from credential if set (e.g. db.example.com)",
    ),
    # Port
    number_field(
        key="port",
        label="Port",
        placeholder="optional — from credential, else db-type default (e.g. 5432)",
        rules=[
            ValidationRule(method="min", value=1, message="Port must be positive"),
            ValidationRule(method="max", value=65535, message="Port must be <= 65535"),
        ],
    ),
    # Database Name — SA-1564: optional override (see Host).
    text_field(
        key="database",
        label="Database Name",
        placeholder="optional — from credential if set (e.g. my_database)",
    ),
    # T5 hard-remove: inline username / password fields are gone — both come only from
    # the vault credential resolved via credential_ref. SA-1564: host / port / database
    # are now derived from the credential config too (inline value overrides), so a
    # generic-database credential fully configures the connection.
    # Operation Type
    select_field(
        key="operation_type",
        label="Operation Type",
        options=[
            (OperationType.SELECT.value, "SELECT (Read-Only)"),
            (OperationType.INSERT.value, "INSERT"),
            (OperationType.UPDATE.value, "UPDATE"),
            (OperationType.DELETE.value, "DELETE"),
        ],
        placeholder="Select operation type",
        rules=[ValidationRule(method="required")],
    ),
    # Raw SQL
    textarea_field(
        key="raw_sql",
        label="SQL Query",
        placeholder="SELECT * FROM users WHERE id = 1",
        rows=5,
        rules=[ValidationRule(method="required")],
    ),
    # Max Rows (only for SELECT)
    AutoFormField(
        key="max_rows",
        output_type=OutputType.NUMBER,
        field_config=FieldConfig(
            field_control=FieldControl.INPUT,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Max Rows (SELECT only)"},
            control_props={"type": "number", "placeholder": "1000", "value": DEFAULT_MAX_ROWS},
            rules=[
                ValidationRule(method="min", value=1, message="Min rows is 1"),
                ValidationRule(method="max", value=10000, message="Max rows is 10000"),
            ],
            condition=ConditionConfig(
                field_key="operation_type",
                operator=ConditionOperator.NOT_EQUALS,
                condition_value=OperationType.SELECT.value,
            ),
        ),
    ),
    # Timeout
    number_field(
        key="timeout_seconds",
        label="Timeout (seconds)",
        placeholder="30",
        default=DEFAULT_TIMEOUT,
        rules=[
            ValidationRule(method="min", value=1, message="Min timeout is 1 second"),
            ValidationRule(method="max", value=300, message="Max timeout is 300 seconds"),
        ],
    ),
    # SSL Enabled
    switch_field(
        key="ssl_enabled",
        label="SSL Enabled",
        default=False,
    ),
]

DATABASE_CONNECTION_INPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _INPUT_FIELDS]


# -- Output Schema ------------------------------------------------------------

_OUTPUT_FIELDS: list[AutoFormField] = [
    switch_field(
        key="success",
        label="Success",
        readonly=True,
    ),
    AutoFormField(
        key="data",
        output_type=OutputType.ARRAY,
        field_config=FieldConfig(
            field_control=FieldControl.TEXTAREA,
            field_wrapper=FieldWrapper.FORM_ITEM,
            wrapper_props={"label": "Result Data"},
            control_props={"readonly": True},
        ),
    ),
    number_field(
        key="row_count",
        label="Row Count",
        readonly=True,
    ),
    number_field(
        key="affected_rows",
        label="Affected Rows",
        readonly=True,
    ),
    text_field(
        key="operation_type",
        label="Operation Type",
        readonly=True,
    ),
    number_field(
        key="execution_time_ms",
        label="Execution Time (ms)",
        readonly=True,
    ),
    text_field(
        key="error",
        label="Error",
        readonly=True,
    ),
    text_field(
        key="error_code",
        label="Error Code",
        readonly=True,
    ),
]

DATABASE_CONNECTION_OUTPUT_SCHEMA: list[dict[str, Any]] = [f.to_dict() for f in _OUTPUT_FIELDS]


# -- Use Case -----------------------------------------------------------------

class ExecuteTaskUseCase:
    """Execute database operations with security validation."""
    
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.monitor = monitor
        self._validator = SQLValidator(max_rows=DEFAULT_MAX_ROWS)
    
    def execute(self, request: ExecuteTaskCommand) -> ExecuteTaskResult:
        """Execute the database operation.
        
        Args:
            request: The task command with inputs.
            
        Returns:
            ExecuteTaskResult with operation results.
        """
        self.logger.info(
            "Executing database operation",
            task_id=request.task_id,
            action=request.action,
        )
        self.monitor.track("db_connection_task_requests", 1)
        
        start = time.monotonic()
        
        try:
            inputs = request.inputs
            
            # Extract and validate inputs
            db_type = self._get_db_type(inputs.get("db_type"))
            operation_type = self._get_operation_type(inputs.get("operation_type"))
            raw_sql = inputs.get("raw_sql", "").strip()
            max_rows = self._to_int(inputs.get("max_rows", DEFAULT_MAX_ROWS), DEFAULT_MAX_ROWS)
            timeout_seconds = self._to_int(inputs.get("timeout_seconds", DEFAULT_TIMEOUT), DEFAULT_TIMEOUT)
            
            if not raw_sql:
                raise ValueError("'raw_sql' is required")
            
            # Validate SQL based on operation type
            self._validator.max_rows = max_rows
            validation = self._validator.validate_operation(raw_sql, operation_type)
            
            if not validation.is_valid:
                raise ValueError(f"SQL validation failed: {validation.error}")
            
            # Build connection config
            config = self._build_connection_config(inputs, db_type, timeout_seconds)
            
            # Execute based on operation type
            if operation_type == OperationType.SELECT:
                # Inject LIMIT if needed
                final_sql = self._validator.inject_limit(raw_sql, max_rows)
                result = self._execute_select(config, final_sql)
            else:
                result = self._execute_write(config, raw_sql, operation_type)
            
            duration_ms = (time.monotonic() - start) * 1000
            result["execution_time_ms"] = round(duration_ms, 2)
            result["operation_type"] = operation_type.value
            
            self.monitor.track("db_connection_task_duration_ms", duration_ms)
            self.monitor.track("db_connection_task_success", 1)
            
            return ExecuteTaskResult(
                task_id=request.task_id,
                status=TaskStatus.SUCCESS,
                outputs=result,
                duration_ms=duration_ms,
            )
        
        except ValueError as e:
            return self._handle_error(request, start, str(e), "VALIDATION_ERROR")
        except SQLAlchemyError as e:
            return self._handle_error(request, start, self._sanitize_error(str(e)), "DATABASE_ERROR")
        except Exception as e:
            return self._handle_error(request, start, self._sanitize_error(str(e)), "UNKNOWN_ERROR")
    
    def _execute_select(
        self,
        config: ConnectionConfig,
        sql: str,
    ) -> dict[str, Any]:
        """Execute SELECT query and return results.
        
        Args:
            config: Database connection config.
            sql: The SELECT SQL statement.
            
        Returns:
            Dictionary with data and row_count.
        """
        engine = DatabaseEngineFactory.create_engine(config)
        
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                columns = list(result.keys())
                rows = result.fetchall()
                
                # Convert rows to list of dictionaries
                data = [dict(zip(columns, row)) for row in rows]
                
                return {
                    "success": True,
                    "data": data,
                    "row_count": len(data),
                    "affected_rows": 0,
                }
        finally:
            engine.dispose()
    
    def _execute_write(
        self,
        config: ConnectionConfig,
        sql: str,
        operation_type: OperationType,
    ) -> dict[str, Any]:
        """Execute INSERT/UPDATE/DELETE with transaction.
        
        Args:
            config: Database connection config.
            sql: The SQL statement.
            operation_type: Type of write operation.
            
        Returns:
            Dictionary with affected_rows.
        """
        engine = DatabaseEngineFactory.create_engine(config)
        
        try:
            with engine.connect() as conn:
                with conn.begin():  # Start transaction
                    result = conn.execute(text(sql))
                    affected_rows = result.rowcount
                    # Transaction commits automatically on __exit__
                
                return {
                    "success": True,
                    "data": [],
                    "row_count": 0,
                    "affected_rows": affected_rows,
                }
        finally:
            engine.dispose()
    
    def _build_connection_config(
        self,
        inputs: dict[str, Any],
        db_type: DatabaseType,
        timeout: int,
    ) -> ConnectionConfig:
        """Build ConnectionConfig from inputs.
        
        Args:
            inputs: Input dictionary.
            db_type: Database type.
            timeout: Statement timeout.
            
        Returns:
            ConnectionConfig instance.
        """
        # T5 hard-remove: username/password come only from the vault credential resolved
        # via credential_ref. No inline fallback — a missing credential fails closed
        # (require=True) rather than silently connecting with empty credentials.
        cred = resolve_effective_auth(inputs, require=True)
        cfg = cred.get("config", {}) or {}
        username = cfg.get("username", "")
        password = cfg.get("password", "")

        # SA-1564: host / port / database are derived from the credential config when it
        # carries them (a generic-database bundle), so the user need not retype what the
        # credential already holds. An inline value OVERRIDES the credential; otherwise
        # fall back to the credential, then the db-type default (port only).
        default_port = DatabaseEngineFactory.DEFAULT_PORTS.get(db_type, 5432)
        host = (str(inputs.get("host") or "").strip()
                or str(cfg.get("host") or "").strip())
        database = (str(inputs.get("database") or "").strip()
                    or str(cfg.get("database") or "").strip())
        raw_port = inputs.get("port")
        if raw_port in (None, ""):
            raw_port = cfg.get("port")
        port = self._to_int(raw_port, default_port)

        if not host:
            raise ValueError(
                "'host' is required: set it on the node or use a database credential "
                "that carries a host"
            )
        if not database:
            raise ValueError(
                "'database' is required: set it on the node or use a database credential "
                "that carries a database name"
            )

        return ConnectionConfig(
            db_type=db_type,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            pool_size=DEFAULT_POOL_SIZE,
            statement_timeout=timeout,
            ssl_enabled=self._to_bool(inputs.get("ssl_enabled", False)),
        )
    
    def _handle_error(
        self,
        request: ExecuteTaskCommand,
        start: float,
        error_msg: str,
        error_code: str,
    ) -> ExecuteTaskResult:
        """Handle error and return error result.
        
        Args:
            request: Original request.
            start: Start time for duration calculation.
            error_msg: Error message.
            error_code: Error code.
            
        Returns:
            ExecuteTaskResult with error.
        """
        duration_ms = (time.monotonic() - start) * 1000
        
        self.logger.error(
            "Database operation failed",
            task_id=request.task_id,
            error=error_msg,
            error_code=error_code,
        )
        self.monitor.track("db_connection_task_error", 1)
        
        return ExecuteTaskResult(
            task_id=request.task_id,
            status=TaskStatus.ERROR,
            error=error_msg,
            outputs={
                "success": False,
                "error": error_msg,
                "error_code": error_code,
                "data": [],
                "row_count": 0,
                "affected_rows": 0,
                "execution_time_ms": round(duration_ms, 2),
            },
            duration_ms=duration_ms,
        )
    
    def _get_db_type(self, value: Any) -> DatabaseType:
        """Parse database type from input."""
        if not value:
            raise ValueError("'db_type' is required")
        return DatabaseType.from_string(str(value))
    
    def _get_operation_type(self, value: Any) -> OperationType:
        """Parse operation type from input."""
        if not value:
            raise ValueError("'operation_type' is required")
        return OperationType.from_string(str(value))
    
    def _to_int(self, value: Any, default: int) -> int:
        """Convert value to int with default."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    
    def _to_bool(self, value: Any) -> bool:
        """Convert value to bool."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    
    def _sanitize_error(self, error: str) -> str:
        """Sanitize error message to hide sensitive info."""
        import re
        
        # Hide passwords in connection strings
        sanitized = re.sub(r"://[^:]+:[^@]+@", "://***:***@", error)
        
        # Truncate long errors
        max_length = 500
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "... [TRUNCATED]"
        
        return sanitized
