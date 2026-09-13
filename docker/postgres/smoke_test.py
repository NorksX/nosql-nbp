"""Installation smoke test for PostgreSQL.

Run from the client container:
    docker compose exec client python docker/postgres/smoke_test.py

Verifies the full round trip the Phase 2 loaders depend on: connect, DDL, a
JSONB insert, a primary-key read, a GIN-indexed array containment predicate,
and server-side aggregation. If this passes, the server is ready to load.

The array-containment step is the one worth watching. It is the operation the
L1 genre index rests on, and it is the place where a relational engine has to
be told, explicitly, that a JSON array is something it may index — which the
two key-value stores never needed to be told, because in their models the genre
is part of the key.
"""

import os

import psycopg

DSN = os.environ.get("PG_DSN", "postgresql://nbp:nbp@pg:5432/tmdb")
TABLE = "smoke"


def main() -> None:
    print(f"dsn: {DSN}")
    with psycopg.connect(DSN, autocommit=True) as conn:
        print("0. SERVER VERSION")
        version = conn.execute("SHOW server_version").fetchone()[0]
        print("   ->", version)

        with conn.cursor() as cur:
            print("1. CREATE TABLE + GIN INDEX")
            cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
            cur.execute(
                f"CREATE TABLE {TABLE} (id INTEGER PRIMARY KEY, doc JSONB NOT NULL)"
            )
            cur.execute(
                f"CREATE INDEX smoke_genre ON {TABLE} "
                "USING GIN ((doc -> 'genre_ids') jsonb_path_ops)"
            )

            print("2. INSERT")
            cur.execute(
                f"INSERT INTO {TABLE} (id, doc) VALUES (%s, %s::jsonb)",
                (
                    1,
                    '{"title":"Closed Ward","year":2001,'
                    '"genre_ids":[18,9648],"popularity":0.6}',
                ),
            )

            print("3. SELECT by primary key")
            row = cur.execute(
                f"SELECT doc ->> 'title' FROM {TABLE} WHERE id = 1"
            ).fetchone()
            print("   ->", row)
            assert row[0] == "Closed Ward"

            print("4. array containment (the L1 genre access path)")
            row = cur.execute(
                f"SELECT id FROM {TABLE} WHERE doc -> 'genre_ids' @> '18'::jsonb"
            ).fetchone()
            print("   ->", row)
            assert row[0] == 1

            print("5. server-side aggregation over a JSON path")
            row = cur.execute(
                "SELECT count(*), avg((doc ->> 'popularity')::float8) "
                f"FROM {TABLE} GROUP BY (doc ->> 'year')::int"
            ).fetchone()
            print("   ->", row)

            print("6. GROUP BY an element of the JSON array")
            # Oracle NoSQL cannot do this in one statement and needs one query
            # per genre; FoundationDB has no server-side aggregation at all.
            rows = cur.execute(
                "SELECT g.value::int AS genre_id, count(*) "
                f"FROM {TABLE}, LATERAL jsonb_array_elements_text(doc -> 'genre_ids') g "
                "GROUP BY 1 ORDER BY 1"
            ).fetchall()
            print("   ->", rows)
            assert len(rows) == 2

            print("7. DROP")
            cur.execute(f"DROP TABLE {TABLE}")

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
