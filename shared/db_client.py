import os
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg2.extras import Json
from psycopg2.pool import SimpleConnectionPool

from .logger import get_logger

log = get_logger(__name__)

_pool: Optional[SimpleConnectionPool] = None


def _get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is not None:
        return _pool

    conn_str = os.environ.get("PG_CONN_STRING", "")
    if not conn_str:
        host = os.environ.get("PG_HOST", "")
        user = os.environ.get("PG_USER", "")
        password = os.environ.get("PG_PASSWORD", "")
        database = os.environ.get("PG_DATABASE", "")
        port = os.environ.get("PG_PORT", "5432")

        if not all([host, user, password, database]):
            raise RuntimeError(
                "Faltan variables Postgres: PG_HOST, PG_USER, PG_PASSWORD, PG_DATABASE"
            )

        conn_str = (
            f"host={host} port={port} user={user} password={password} "
            f"dbname={database} sslmode=require"
        )

    _pool = SimpleConnectionPool(minconn=1, maxconn=10, dsn=conn_str)
    log.info("postgres pool inicializado")
    return _pool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert(table: str, row: dict[str, Any]) -> None:
    safe_row = {
        k: Json(v) if isinstance(v, (dict, list)) else v
        for k, v in row.items()
    }
    cols = list(safe_row.keys())
    placeholders = [f"%({c})s" for c in cols]
    query = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)})"
    )

    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, safe_row)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def document_results(
    radicado: str,
    cedula: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
) -> None:
    _insert("document_results", {
        "radicado": radicado,
        "cedula": cedula or None,
        "request_json": request_payload,
        "response_json": response_payload,
        "updated_at": _now_iso(),
    })
    log.info("document_results guardado", extra={"radicado": radicado})
