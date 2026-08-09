"""Installation smoke test for FoundationDB.

Run from the client container:
    docker compose exec client python docker/foundationdb/smoke_test.py

Verifies the cluster file is readable, the client library matches the server,
and that keys survive a transaction. Also exercises the tuple layer and a range
read, since the ordered keyspace is what the Phase 2 index design relies on.
"""

import os

import fdb

fdb.api_version(730)

CLUSTER_FILE = os.environ.get("FDB_CLUSTER_FILE", "/etc/foundationdb/fdb.cluster")


@fdb.transactional
def _write_batch(tr, pairs):
    for key, value in pairs:
        tr[key] = value


@fdb.transactional
def _range_read(tr, prefix):
    return list(tr[prefix.range()])


def main() -> None:
    print(f"cluster file: {CLUSTER_FILE}")
    with open(CLUSTER_FILE) as fh:
        print(f"   contents: {fh.read().strip()}")

    db = fdb.open(CLUSTER_FILE)

    print("1. simple set/get")
    db[b"smoke"] = b"ok"
    assert db[b"smoke"] == b"ok"
    print("   -> smoke =", bytes(db[b"smoke"]).decode())

    print("2. tuple layer + batched transaction")
    smoke = fdb.Subspace(("smoke_movies",))
    pairs = [
        (smoke.pack(("movie", i)), fdb.tuple.pack((f"title-{i}", 2000 + i)))
        for i in range(5)
    ]
    _write_batch(db, pairs)

    print("3. ordered range read")
    for key, value in _range_read(db, smoke):
        print("   ->", smoke.unpack(key), fdb.tuple.unpack(value))

    print("4. cleanup")
    del db[b"smoke"]
    db.clear_range_startswith(smoke.key())

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
