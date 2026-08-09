"""Create (or drop) the L1/L2 schemas in whichever database is reachable.

Run from inside a client container, from /work:

    docker compose exec client python -m common.apply_schema --model l1
    docker compose exec client python -m common.apply_schema --model l1 --model l2

The database is picked from the environment the container already sets —
``NOSQL_ENDPOINT`` for Oracle NoSQL, ``FDB_CLUSTER_FILE`` for FoundationDB —
so the same command works in both stacks.

Oracle NoSQL has real DDL, so this creates the tables and indexes. FoundationDB
has no schema to declare: the key layout only exists in ``common/keyspec.py``,
and there this script just reports the prefixes and, with ``--drop``, clears
them. Dropping is what makes the loaders restartable from a clean state.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or drop the L1/L2 schemas in the reachable database."
    )
    parser.add_argument(
        "--model",
        action="append",
        choices=("l1", "l2"),
        help="which schema to apply; repeat for both (default: both)",
    )
    parser.add_argument(
        "--db",
        choices=("oracle", "fdb"),
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
    database = args.db or ("oracle" if os.environ.get("NOSQL_ENDPOINT") else "fdb")

    if args.drop and not args.yes:
        print("--drop would DELETE the following, and all data in it:\n")
        for model in models:
            targets = (
                ORACLE_TABLES[model] if database == "oracle" else FDB_PREFIXES[model]
            )
            for target in targets:
                print(f"   {model.upper()}  {target!r}")
        print("\nre-run with --yes to actually do it")
        return 1

    if database == "oracle":
        apply_oracle(models, args.drop)
    else:
        apply_fdb(models, args.drop)

    print("dropped" if args.drop else "applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
