"""Create (or drop) the L1/L2 schemas in whichever database is reachable.

Run from inside a client container, from /work:

    docker compose exec client python -m common.apply_schema --model l1
    docker compose exec client python -m common.apply_schema --model l1 --model l2

The database is picked from the environment the container already sets —
``NOSQL_ENDPOINT`` for Oracle NoSQL, ``PG_DSN`` for PostgreSQL, and otherwise
``FDB_CLUSTER_FILE`` for FoundationDB — so the same command works in all three
stacks.

The three behave differently here, and the difference is itself a result worth
recording in the report:

- **PostgreSQL** has the fullest DDL of the three: typed columns, declared
  constraints, and index types the planner chooses between. Applying the schema
  is a transaction, and a failed statement rolls back.
- **Oracle NoSQL** has real DDL too, but one table request at a time, polled
  until the store reports the table ready.
- **FoundationDB** has no schema to declare at all: the key layout exists only
  in ``common/keyspec.py``, so here this script just reports the prefixes and,
  with ``--drop``, clears them.

Dropping is what makes the loaders restartable from a clean state.
"""

from __future__ import annotations

import argparse
import os
import sys

from common import keyspec

# borneo waits on a table request in (timeout_ms, poll_interval_ms). Index
# builds over 109,222 rows are the slow case, hence the generous timeout.
TABLE_REQUEST_TIMEOUT_MS = 120_000
TABLE_REQUEST_POLL_MS = 1_000

ORACLE_DDL = {
    "l1": keyspec.ORACLE_L1_DDL + keyspec.ORACLE_L1_INDEX_DDL,
    "l2": keyspec.ORACLE_L2_DDL + keyspec.ORACLE_L2_INDEX_DDL,
}
ORACLE_TABLES = {
    "l1": (keyspec.ORACLE_L1_TABLE,),
    "l2": keyspec.ORACLE_L2_TABLES,
}
FDB_PREFIXES = {
    "l1": (keyspec.L1_MOVIE_PREFIX, keyspec.L1_INDEX_PREFIX),
    "l2": (keyspec.L2_YEAR_PREFIX, keyspec.L2_STATS_PREFIX, keyspec.L2_TOP_PREFIX),
}
POSTGRES_DDL = {
    "l1": keyspec.POSTGRES_L1_DDL + keyspec.POSTGRES_L1_INDEX_DDL,
    "l2": keyspec.POSTGRES_L2_DDL + keyspec.POSTGRES_L2_INDEX_DDL,
    "r": keyspec.POSTGRES_R_DDL + keyspec.POSTGRES_R_INDEX_DDL,
}
POSTGRES_TABLES = {
    "l1": (keyspec.POSTGRES_L1_TABLE,),
    "l2": keyspec.POSTGRES_L2_TABLES,
    # Dropped in this order: children before parents, or the foreign keys
    # refuse. POSTGRES_R_TABLES is already stored that way.
    "r": keyspec.POSTGRES_R_TABLES,
}


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def detect_database(override: str | None = None) -> str:
    """Which database this container is wired to.

    Checked in a fixed order so a container that happens to carry two of the
    variables still resolves deterministically; ``--db`` overrides it.
    """
    if override:
        return override
    if os.environ.get("NOSQL_ENDPOINT"):
        return "oracle"
    if os.environ.get("PG_DSN"):
        return "postgres"
    return "fdb"


def _one_line(statement: str) -> str:
    return " ".join(statement.split())


def apply_oracle(models: list[str], drop: bool) -> None:
    from borneo import NoSQLHandle, NoSQLHandleConfig, TableRequest
    from borneo.kv import StoreAccessTokenProvider

    endpoint = os.environ["NOSQL_ENDPOINT"]
    print(f"Oracle NoSQL at {endpoint}\n")
    handle = NoSQLHandle(
        NoSQLHandleConfig(endpoint).set_authorization_provider(StoreAccessTokenProvider())
    )

    def run(statement: str) -> None:
        print(f"   {_one_line(statement)}")
        handle.do_table_request(
            TableRequest().set_statement(statement),
            TABLE_REQUEST_TIMEOUT_MS,
            TABLE_REQUEST_POLL_MS,
        )

    try:
        for model in models:
            print(f"{model.upper()}:")
            if drop:
                # Indexes go with the table; dropping the table is enough.
                for table in ORACLE_TABLES[model]:
                    run(f"DROP TABLE IF EXISTS {table}")
            else:
                for statement in ORACLE_DDL[model]:
                    run(statement)
            print()
    finally:
        handle.close()


def apply_fdb(models: list[str], drop: bool) -> None:
    import fdb

    fdb.api_version(730)
    cluster_file = os.environ.get("FDB_CLUSTER_FILE", "/etc/foundationdb/fdb.cluster")
    print(f"FoundationDB via {cluster_file}\n")
    db = fdb.open(cluster_file)

    for model in models:
        print(f"{model.upper()}:")
        for prefix in FDB_PREFIXES[model]:
            packed = fdb.tuple.pack(prefix)
            if drop:
                db.clear_range_startswith(packed)
                print(f"   cleared  {prefix!r}  ({packed!r})")
            else:
                print(f"   prefix   {prefix!r}  ({packed!r}) — no DDL needed")
        print()


def apply_postgres(models: list[str], drop: bool) -> None:
    import psycopg

    dsn = os.environ["PG_DSN"]
    # Never print the DSN itself — it carries the password.
    print(f"PostgreSQL at {dsn.rsplit('@', 1)[-1]}\n")

    with psycopg.connect(dsn, autocommit=True) as conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
        print(f"   server_version {version}\n")
        for model in models:
            print(f"{model.upper()}:")
            with conn.cursor() as cur:
                if drop:
                    # CASCADE is not needed — nothing references these tables —
                    # but indexes do go with the table, as in Oracle NoSQL.
                    for table in POSTGRES_TABLES[model]:
                        statement = f"DROP TABLE IF EXISTS {table}"
                        print(f"   {statement}")
                        cur.execute(statement)
                else:
                    for statement in POSTGRES_DDL[model]:
                        print(f"   {_one_line(statement)}")
                        cur.execute(statement)
            print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or drop the L1/L2 schemas in the reachable database."
    )
    parser.add_argument(
        "--model",
        action="append",
        choices=("l1", "l2", "r"),
        help="which schema to apply; repeat to combine (default: l1 and l2). "
             "`r` is the normalized relational model and exists only on "
             "PostgreSQL — the two key-value stores have no equivalent.",
    )
    parser.add_argument(
        "--db",
        choices=("oracle", "fdb", "postgres"),
        help="override the database detected from the environment",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="delete the schema and all its data instead of creating it",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm --drop; without it, --drop only reports what it would delete",
    )
    args = parser.parse_args(argv)

    models = args.model or ["l1", "l2"]
    database = detect_database(args.db)

    if "r" in models and database != "postgres":
        return _fail("model `r` is relational and only exists on PostgreSQL")

    targets_for = {
        "oracle": ORACLE_TABLES,
        "postgres": POSTGRES_TABLES,
        "fdb": FDB_PREFIXES,
    }[database]

    if args.drop and not args.yes:
        print("--drop would DELETE the following, and all data in it:\n")
        for model in models:
            for target in targets_for[model]:
                print(f"   {model.upper()}  {target!r}")
        print("\nre-run with --yes to actually do it")
        return 1

    {"oracle": apply_oracle, "postgres": apply_postgres, "fdb": apply_fdb}[database](
        models, args.drop
    )

    print("dropped" if args.drop else "applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
