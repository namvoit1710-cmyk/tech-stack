"""Dynamic functions exposed by the Database Connection Worker.

These functions can be called at design-time (to test connections, list tables)
or at runtime (via _function_call in input_mapping).
"""

from typing import Any

from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction

from app.layer1_domain.value_objects.database_type import DatabaseType
from app.layer2_application.services.database_engine import (
    ConnectionConfig,
    DatabaseEngineFactory,
)


async def test_connection(params: dict[str, Any]) -> dict[str, Any]:
    """Test database connectivity with SELECT 1.
    
    Returns connection status, server version, and latency.
    """
    db_type_str = params.get("db_type", "")
    host = params.get("host", "")
    port = params.get("port")
    database = params.get("database", "")
    username = params.get("username", "")
    password = params.get("password", "")
    timeout = params.get("timeout_seconds", 10)
    ssl_enabled = params.get("ssl_enabled", False)
    
    # Validate required fields
    if not db_type_str:
        return {"success": False, "error": "'db_type' is required"}
    if not host:
        return {"success": False, "error": "'host' is required"}
    if not database:
        return {"success": False, "error": "'database' is required"}
    if not username:
        return {"success": False, "error": "'username' is required"}
    
    try:
        db_type = DatabaseType.from_string(db_type_str)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    
    # Use default port if not provided
    if not port:
        port = DatabaseEngineFactory.DEFAULT_PORTS.get(db_type, 5432)
    
    config = ConnectionConfig(
        db_type=db_type,
        host=host,
        port=int(port),
        database=database,
        username=username,
        password=password,
        statement_timeout=int(timeout),
        ssl_enabled=bool(ssl_enabled),
    )
    
    result = DatabaseEngineFactory.test_connection(config)
    
    return {
        "success": result.success,
        "server_version": result.server_version,
        "latency_ms": result.latency_ms,
        "error": result.error,
    }


async def list_tables(params: dict[str, Any]) -> dict[str, Any]:
    """List all tables in the database.
    
    Returns list of table names for design-time configuration.
    """
    from sqlalchemy import text
    
    db_type_str = params.get("db_type", "")
    host = params.get("host", "")
    port = params.get("port")
    database = params.get("database", "")
    username = params.get("username", "")
    password = params.get("password", "")
    schema = params.get("schema", "")  # Optional schema filter
    
    # Validate required fields
    if not all([db_type_str, host, database, username]):
        return {"success": False, "tables": [], "error": "Missing required connection parameters"}
    
    try:
        db_type = DatabaseType.from_string(db_type_str)
    except ValueError as e:
        return {"success": False, "tables": [], "error": str(e)}
    
    # Use default port if not provided
    if not port:
        port = DatabaseEngineFactory.DEFAULT_PORTS.get(db_type, 5432)
    
    config = ConnectionConfig(
        db_type=db_type,
        host=host,
        port=int(port),
        database=database,
        username=username,
        password=params.get("password", ""),
        statement_timeout=30,
    )
    
    try:
        # Build query based on database type
        if db_type == DatabaseType.POSTGRESQL:
            query = """
                SELECT table_schema, table_name 
                FROM information_schema.tables 
                WHERE table_type = 'BASE TABLE'
                AND table_schema NOT IN ('pg_catalog', 'information_schema')
            """
            if schema:
                query += f" AND table_schema = '{schema}'"
            query += " ORDER BY table_schema, table_name"
        
        elif db_type == DatabaseType.MYSQL:
            query = f"""
                SELECT table_schema, table_name 
                FROM information_schema.tables 
                WHERE table_schema = '{database}'
                ORDER BY table_name
            """
        
        elif db_type == DatabaseType.HANA:
            query = """
                SELECT schema_name, table_name 
                FROM tables 
                WHERE is_system_table = 'FALSE'
            """
            if schema:
                query += f" AND schema_name = '{schema}'"
            query += " ORDER BY schema_name, table_name"
        
        else:
            return {"success": False, "tables": [], "error": f"Unsupported database type: {db_type}"}
        
        engine = DatabaseEngineFactory.create_engine(config)
        
        try:
            with engine.connect() as conn:
                result = conn.execute(text(query))
                rows = result.fetchall()
                
                tables = []
                for row in rows:
                    schema_name = row[0] if len(row) > 1 else ""
                    table_name = row[1] if len(row) > 1 else row[0]
                    tables.append({
                        "schema": schema_name,
                        "table_name": table_name,
                        "full_name": f"{schema_name}.{table_name}" if schema_name else table_name,
                    })
                
                return {
                    "success": True,
                    "tables": tables,
                    "count": len(tables),
                }
        finally:
            engine.dispose()
    
    except Exception as e:
        # Sanitize error
        import re
        error = re.sub(r"://[^:]+:[^@]+@", "://***:***@", str(e))
        return {"success": False, "tables": [], "error": error[:500]}


async def get_table_columns(params: dict[str, Any]) -> dict[str, Any]:
    """Get column information for a specific table.
    
    Returns list of columns with their data types.
    """
    from sqlalchemy import text
    
    db_type_str = params.get("db_type", "")
    host = params.get("host", "")
    port = params.get("port")
    database = params.get("database", "")
    username = params.get("username", "")
    password = params.get("password", "")
    table_name = params.get("table_name", "")
    schema = params.get("schema", "")
    
    if not table_name:
        return {"success": False, "columns": [], "error": "'table_name' is required"}
    
    if not all([db_type_str, host, database, username]):
        return {"success": False, "columns": [], "error": "Missing required connection parameters"}
    
    try:
        db_type = DatabaseType.from_string(db_type_str)
    except ValueError as e:
        return {"success": False, "columns": [], "error": str(e)}
    
    if not port:
        port = DatabaseEngineFactory.DEFAULT_PORTS.get(db_type, 5432)
    
    config = ConnectionConfig(
        db_type=db_type,
        host=host,
        port=int(port),
        database=database,
        username=username,
        password=password,
        statement_timeout=30,
    )
    
    try:
        # Build query based on database type
        if db_type == DatabaseType.POSTGRESQL:
            query = f"""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = '{table_name}'
            """
            if schema:
                query += f" AND table_schema = '{schema}'"
            query += " ORDER BY ordinal_position"
        
        elif db_type == DatabaseType.MYSQL:
            query = f"""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = '{database}'
                AND table_name = '{table_name}'
                ORDER BY ordinal_position
            """
        
        elif db_type == DatabaseType.HANA:
            query = f"""
                SELECT column_name, data_type_name, is_nullable, default_value
                FROM table_columns
                WHERE table_name = '{table_name}'
            """
            if schema:
                query += f" AND schema_name = '{schema}'"
            query += " ORDER BY position"
        
        else:
            return {"success": False, "columns": [], "error": f"Unsupported database type: {db_type}"}
        
        engine = DatabaseEngineFactory.create_engine(config)
        
        try:
            with engine.connect() as conn:
                result = conn.execute(text(query))
                rows = result.fetchall()
                
                columns = []
                for row in rows:
                    columns.append({
                        "name": row[0],
                        "data_type": row[1],
                        "nullable": row[2] in ("YES", "TRUE", True, 1),
                        "default": row[3],
                    })
                
                return {
                    "success": True,
                    "columns": columns,
                    "count": len(columns),
                }
        finally:
            engine.dispose()
    
    except Exception as e:
        import re
        error = re.sub(r"://[^:]+:[^@]+@", "://***:***@", str(e))
        return {"success": False, "columns": [], "error": error[:500]}


# -- Worker Function Definitions ----------------------------------------------

DATABASE_WORKER_FUNCTIONS = [
    WorkerFunction(
        name="test_connection",
        description="Test database connectivity. Returns success status, server version, and latency.",
        input_schema=[
            {"key": "db_type", "type": "string", "required": True, "label": "Database Type",
             "options": [
                 {"value": "postgresql", "label": "PostgreSQL"},
                 {"value": "mysql", "label": "MySQL"},
                 {"value": "hana", "label": "SAP HANA"},
             ]},
            {"key": "host", "type": "string", "required": True, "label": "Host"},
            {"key": "port", "type": "number", "required": False, "label": "Port"},
            {"key": "database", "type": "string", "required": True, "label": "Database Name"},
            {"key": "username", "type": "string", "required": True, "label": "Username"},
            {"key": "password", "type": "string", "required": True, "label": "Password", "inputType": "password"},
            {"key": "timeout_seconds", "type": "number", "required": False, "label": "Timeout (s)", "default": 10},
            {"key": "ssl_enabled", "type": "boolean", "required": False, "label": "SSL Enabled", "default": False},
        ],
        output_schema=[
            {"key": "success", "type": "boolean"},
            {"key": "server_version", "type": "string"},
            {"key": "latency_ms", "type": "number"},
            {"key": "error", "type": "string"},
        ],
        handler=test_connection,
    ),
    WorkerFunction(
        name="list_tables",
        description="List all tables in the database. Useful for design-time configuration.",
        input_schema=[
            {"key": "db_type", "type": "string", "required": True, "label": "Database Type"},
            {"key": "host", "type": "string", "required": True, "label": "Host"},
            {"key": "port", "type": "number", "required": False, "label": "Port"},
            {"key": "database", "type": "string", "required": True, "label": "Database Name"},
            {"key": "username", "type": "string", "required": True, "label": "Username"},
            {"key": "password", "type": "string", "required": True, "label": "Password", "inputType": "password"},
            {"key": "schema", "type": "string", "required": False, "label": "Schema Filter"},
        ],
        output_schema=[
            {"key": "success", "type": "boolean"},
            {"key": "tables", "type": "array"},
            {"key": "count", "type": "number"},
            {"key": "error", "type": "string"},
        ],
        handler=list_tables,
    ),
    WorkerFunction(
        name="get_table_columns",
        description="Get column information for a specific table. Returns column names, types, and constraints.",
        input_schema=[
            {"key": "db_type", "type": "string", "required": True, "label": "Database Type"},
            {"key": "host", "type": "string", "required": True, "label": "Host"},
            {"key": "port", "type": "number", "required": False, "label": "Port"},
            {"key": "database", "type": "string", "required": True, "label": "Database Name"},
            {"key": "username", "type": "string", "required": True, "label": "Username"},
            {"key": "password", "type": "string", "required": True, "label": "Password", "inputType": "password"},
            {"key": "table_name", "type": "string", "required": True, "label": "Table Name"},
            {"key": "schema", "type": "string", "required": False, "label": "Schema"},
        ],
        output_schema=[
            {"key": "success", "type": "boolean"},
            {"key": "columns", "type": "array"},
            {"key": "count", "type": "number"},
            {"key": "error", "type": "string"},
        ],
        handler=get_table_columns,
    ),
]
