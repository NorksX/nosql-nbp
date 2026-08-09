"""Throughput probe: how long would a full load actually take?

Writes a small slice into the live L1 schema and reports rows/s, so we can
project the cost of all 109,222 records before committing to it. Throwaway —
the real loaders live in oracle/ and fdb/.

Run with -m from /work so `common` is importable:

    docker exec kv-client  python -m sandbox.probe_write
    docker exec fdb-client python -m sandbox.probe_write
"""

import itertools
import os
import time

from common.dataset import encode, iter_movies
from common.keyspec import FDB_BATCH_SIZE, l1_index_keys, l1_movie_key

N = 2_000
TOTAL = 109_222

sample = list(itertools.islice(iter_movies(), N))
keys_per_movie = 1 + sum(len(list(l1_index_keys(m))) for m in sample) / len(sample)
print(f"probe: {N:,} movies, {keys_per_movie:.1f} keys each")


def report(label: str, seconds: float, units: int, unit_name: str) -> None:
    rate = units / seconds
    print(f"\n{label}")
    print(f"   {units:,} {unit_name} in {seconds:.2f} s  =  {rate:,.0f} {unit_name}/s")
    print(f"   projected full L1 load: {TOTAL / (units / seconds) * (units / N) / 60:.1f} min")


if os.environ.get("NOSQL_ENDPOINT"):
    from borneo import NoSQLHandle, NoSQLHandleConfig, PutRequest
    from borneo.kv import StoreAccessTokenProvider

    handle = NoSQLHandle(
        NoSQLHandleConfig(os.environ["NOSQL_ENDPOINT"]).set_authorization_provider(
            StoreAccessTokenProvider()
        )
    )
    request = PutRequest().set_table_name("l1_movies")
    start = time.perf_counter()
    for movie in sample:
        handle.put(request.set_value({"id": movie["id"], "doc": movie}))
    elapsed = time.perf_counter() - start
    handle.close()
    report("Oracle NoSQL — one put() per row, indexes maintained server-side",
           elapsed, N, "rows")
else:
    import fdb

    fdb.api_version(730)
    db = fdb.open(os.environ.get("FDB_CLUSTER_FILE", "/etc/foundationdb/fdb.cluster"))

    pairs = []
    for movie in sample:
        pairs.append((fdb.tuple.pack(l1_movie_key(movie["id"])), encode(movie)))
        for key in l1_index_keys(movie):
            pairs.append((fdb.tuple.pack(key), b""))

    @fdb.transactional
    def write_batch(tr, batch):
        for key, value in batch:
            tr[key] = value

    start = time.perf_counter()
    for i in range(0, len(pairs), FDB_BATCH_SIZE):
        write_batch(db, pairs[i : i + FDB_BATCH_SIZE])
    elapsed = time.perf_counter() - start
    report(f"FoundationDB — {FDB_BATCH_SIZE}-pair transactions, indexes written by hand",
           elapsed, len(pairs), "keys")
    print(f"   ({N / elapsed:,.0f} movies/s)")
