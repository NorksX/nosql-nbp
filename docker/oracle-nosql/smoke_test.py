"""Installation smoke test for Oracle NoSQL Database CE.

Run from the client container:
    docker compose exec client python docker/oracle-nosql/smoke_test.py

Verifies the full round trip through the HTTP proxy: create table, put, get,
query, drop. If this passes, the store is ready for the Phase 2 loaders.
"""

import os

from borneo import (
    DeleteRequest,
    GetRequest,
    NoSQLHandle,
    NoSQLHandleConfig,
    PutRequest,
    QueryRequest,
    TableRequest,
)
from borneo.kv import StoreAccessTokenProvider

ENDPOINT = os.environ.get("NOSQL_ENDPOINT", "http://kvlite:8080")
TABLE = "smoke"


def main() -> None:
    print(f"endpoint: {ENDPOINT}")
    config = NoSQLHandleConfig(ENDPOINT).set_authorization_provider(
        # non-secure store: no credentials
        StoreAccessTokenProvider()
    )
    handle = NoSQLHandle(config)

    try:
        print("1. CREATE TABLE")
        handle.do_table_request(
            TableRequest().set_statement(
                f"CREATE TABLE IF NOT EXISTS {TABLE} "
                "(id INTEGER, doc JSON, PRIMARY KEY(id))"
            ),
            30000,
            1000,
        )

        print("2. PUT")
        handle.put(
            PutRequest()
            .set_table_name(TABLE)
            .set_value({"id": 1, "doc": {"title": "Closed Ward", "year": 2001}})
        )

        print("3. GET")
        res = handle.get(GetRequest().set_table_name(TABLE).set_key({"id": 1}))
        print("   ->", res.get_value())
        assert res.get_value()["doc"]["title"] == "Closed Ward"

        print("4. QUERY (SQL over JSON)")
        qreq = QueryRequest().set_statement(
            f"SELECT t.doc.year AS year FROM {TABLE} t WHERE t.id = 1"
        )
        while True:
            qres = handle.query(qreq)
            for row in qres.get_results():
                print("   ->", row)
            if qreq.is_done():
                break

        print("5. DELETE + DROP")
        handle.delete(DeleteRequest().set_table_name(TABLE).set_key({"id": 1}))
        handle.do_table_request(
            TableRequest().set_statement(f"DROP TABLE IF EXISTS {TABLE}"), 30000, 1000
        )

        print("\nSMOKE TEST PASSED")
    finally:
        handle.close()


if __name__ == "__main__":
    main()
