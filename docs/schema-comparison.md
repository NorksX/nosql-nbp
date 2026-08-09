# L1 vs L2 — schema design and measured performance

**Неструктурирани бази на податоци, 2025/2026 · Тема 1 — Key-value бази**
Oracle NoSQL Database CE 25.3.21 (kvlite) · FoundationDB 7.3.79 · TMDB 2000–2020, 109,222 movies

This document describes the two data models the project stores the dataset in, and
reports what they actually cost. Every number here was measured on the running
databases; none is estimated. The full schema specification is
[`common/schema.md`](../common/schema.md), and `common/keyspec.py` is its
executable form.

---

## 1. In one paragraph

The same 109,222 movies are stored twice: **L1** keys every movie individually
and adds five secondary indexes (730,414 keys); **L2** packs movies into ~90 KB
per-year chunks and precomputes every aggregate the query set needs (1,867 keys).
Across the ten queries the spread between the two models reaches **12,307×** on
the same database — far larger than the spread between the two databases running
the same query on the same model, which is usually under 5×. **Schema choice
dominates database choice.** Neither model wins outright: L1 is faster on four
queries, L2 on five, and one is a tie. A real deployment would carry both.

---

## 2. The two schemas

| | **L1 — fine-grained** | **L2 — coarse-grained** |
|---|---|---|
| Unit of storage | one movie per key | ~135 movies per ~90 KB chunk |
| Keys | 730,414 | 1,867 |
| Secondary indexes | 5 | none, deliberately |
| Denormalization | none | full — aggregates precomputed at load |
| Logical size | 90.35 MB | 69.13 MB |
| Optimized for | point lookups, selective filters | reports, whole-year reads |
| Degenerate case | aggregates → full scan | point lookup → full scan |

**L1** is what a key-value store looks like when you keep the relational instinct:
`("movie", id)` holds the record, and five index families — IMDb id, year,
(language, vote_count), genre, and (genre, popularity) — map query predicates back
to ids. Oracle NoSQL expresses this as one `l1_movies(id, doc JSON)` table with
five native secondary indexes on JSON paths; FoundationDB writes the index keys by
hand in the same transaction as the record.

**L2** is what a key-value store looks like when you take it literally: group the
data the way it will be read, and compute the answers at write time. Movies live in
`("year", year, chunk)` blobs, and four families of precomputed statistics
(`genre_year`, `lang`, `year`, and a per-genre top-20 leaderboard) answer the
report queries directly. There is deliberately **no path from a movie id to a
movie** — adding one would turn L2 back into L1 and erase the contrast.

Two key designs in L1 are doing real work, and both show up in the results:

- The language index is `(lang, vote_count, id)`, not `(lang, id)`. Query 4 then
  starts its range read at `("idx","lang","en",501)` — 2,611 keys read instead of
  58,015.
- The genre-popularity index stores **negated, scaled** popularity, so
  FoundationDB's ascending key order *is* descending popularity. Query 6 reads 20
  consecutive keys and does no sorting at all. This turns out to matter enormously
  (§5.2).

Both models store byte-identical record values (`common.dataset.encode` — compact
UTF-8 JSON, sorted keys), in both databases. Only the keying and grouping differ,
which is what makes the differences below attributable to the schema.

---

## 3. How this was measured

```bash
docker exec kv-client  python -m common.apply_schema   # DDL (FoundationDB needs none)
docker exec kv-client  python -m common.live_check     # load both models, verify
docker exec kv-client  python -m bench.harness         # benchmark, writes results CSV
docker exec fdb-client python -m common.live_check
docker exec fdb-client python -m bench.harness
python -m bench.report                                 # regenerate the tables below
```

**Correctness gates the timing.** For each query the harness runs the L1 and the L2
implementation, compares the results structurally, and refuses to benchmark a query
whose two models disagree. All ten agree on both databases, and all four
implementations of each query return the same answer — so the latencies below are
latencies for the *same* answer.

That gate earned its keep: it caught a real defect. L2 stored aggregate means
rounded to 6 decimals, and the query layer rounded again to 4 — double rounding
that moved 3 of 399 genre-year averages in the fourth decimal. Two correct-looking
implementations returned provably different numbers. Means are now stored at full
precision and rounded once, at display.

**Protocol.** Warm-up runs discarded; p50/p95/p99 over 200 iterations for point
queries, 50 for indexed range queries, 20 for the complex ones, 5 for aggregates.
Any path whose first run exceeds 250 ms is treated as a full scan and capped at 3
iterations — 200 iterations of a query that parses 68 MB would take an hour and
tell us nothing the 3 runs do not. Both databases are driven from equivalent
containerized Python 3.11 clients on the same host, so the comparison is
client-symmetric.

Query parameters are fixed in `common/keyspec.py` so both databases and both models
are asked literally the same question: year 2017, language `en`, `vote_count > 500`,
genres 18 ∩ 27, probe movie 419704 (*Ad Astra*), report range 2000–2020.

---

## 4. Results

Environment: Fedora Linux x86-64, Docker 29.6.x, 8 GB RAM available to the engine,
kvlite single node (10 partitions), FoundationDB single process, `ssd-2` engine,
`single` redundancy. One run per configuration.

### 4.1 Load and footprint

| | Oracle NoSQL | FoundationDB |
|---|---|---|
| L1 load | 109,222 rows, **83.3 s** (1,311 rows/s) | 730,414 keys, **16.0 s** (45,775 keys/s) |
| L2 load | 1,867 rows, **6.9 s** | 1,867 keys, **0.5 s** |
| Store, both models | 228.9 MB (`/kvroot/kvstore`) | 152 MB accounted KV, 345 MB disk |

Not like-for-like: Oracle NoSQL does one HTTP `put` per row and maintains five
indexes server-side, while the FoundationDB loader batches ~1,000 pairs per
transaction and writes its index keys itself. That FoundationDB is only 5× faster
despite writing 6.7× more keys is the interesting part — batching buys more than
the index maintenance costs.

### 4.2 Latency by query, model and database (p50, ms)

| # | Query | Oracle L1 | Oracle L2 | FDB L1 | FDB L2 | Faster model |
|---|---|--:|--:|--:|--:|---|
| 1 | Point lookup by TMDB id | **0.77** | **9,525** ᶠ | **0.16** | **300.28** ᶠ | Oracle L1 12,307× · FDB L1 1,854× |
| 2 | Lookup by IMDb id (alternate key) | **2.03** | **9,324** ᶠ | **0.16** | **310.11** ᶠ | Oracle L1 4,584× · FDB L1 1,963× |
| 3 | All movies of year 2017 | **12.36** | **29.72** | **26.80** | **24.07** | Oracle L1 2.4× · FDB L2 1.1× |
| 4 | Language `en` with vote_count > 500 | **5.13** | **9,602** ᶠ | **3.32** | **344.93** ᶠ | Oracle L1 1,870× · FDB L1 104× |
| 5 | Movies in both Drama and Horror | **226.52** | **9,507** ᶠ | **160.93** | **353.86** ᶠ | Oracle L1 42× · FDB L1 2.2× |
| 6 | Top 20 of Drama by popularity | **875.40** ᶠ | **2.48** | **0.74** | **0.42** | Oracle L2 353× · FDB L2 1.8× |
| 7 | Same language, ±1 year of *Ad Astra* | **394.24** ᶠ | **1,549** ᶠ | **332.78** ᶠ | **54.45** | Oracle L1 3.9× · FDB L2 6.1× |
| 8 | Avg rating & count per genre per year | **1,136** ᶠ | **10.24** | **832.39** ᶠ | **3.86** | Oracle L2 111× · FDB L2 216× |
| 9 | Top 10 languages by count, mean popularity | **686.37** ᶠ | **3.95** | **774.74** ᶠ | **1.33** | Oracle L2 174× · FDB L2 583× |
| 10 | Yearly trend, vote_count ≥ 50 | **1,265** ᶠ | **2.38** | **778.83** ᶠ | **0.63** | Oracle L2 531× · FDB L2 1,232× |

ᶠ = no index path for this model; the query degrades to a full scan.

### 4.3 Same query, same model, different database

| # | Model | Oracle NoSQL | FoundationDB | Ratio |
|---|---|--:|--:|---|
| 1 | L1 | 0.77 | 0.16 | FDB 4.8× |
| 1 | L2 | 9,525 ᶠ | 300.28 ᶠ | FDB 32× |
| 2 | L1 | 2.03 | 0.16 | FDB 13× |
| 2 | L2 | 9,324 ᶠ | 310.11 ᶠ | FDB 30× |
| 3 | L1 | 12.36 | 26.80 | **Oracle 2.2×** |
| 3 | L2 | 29.72 | 24.07 | FDB 1.2× |
| 4 | L1 | 5.13 | 3.32 | FDB 1.5× |
| 4 | L2 | 9,602 ᶠ | 344.93 ᶠ | FDB 28× |
| 5 | L1 | 226.52 | 160.93 | FDB 1.4× |
| 5 | L2 | 9,507 ᶠ | 353.86 ᶠ | FDB 27× |
| 6 | L1 | 875.40 ᶠ | 0.74 | **FDB 1,177×** |
| 6 | L2 | 2.48 | 0.42 | FDB 5.9× |
| 7 | L1 | 394.24 ᶠ | 332.78 ᶠ | FDB 1.2× |
| 7 | L2 | 1,549 ᶠ | 54.45 | FDB 28× |
| 8 | L1 | 1,136 ᶠ | 832.39 ᶠ | FDB 1.4× |
| 8 | L2 | 10.24 | 3.86 | FDB 2.7× |
| 9 | L1 | 686.37 ᶠ | 774.74 ᶠ | **Oracle 1.1×** |
| 9 | L2 | 3.95 | 1.33 | FDB 3.0× |
| 10 | L1 | 1,265 ᶠ | 778.83 ᶠ | FDB 1.6× |
| 10 | L2 | 2.38 | 0.63 | FDB 3.8× |

### 4.4 Tail behaviour

The six widest p95/p50 ratios in the whole run:

| Database | # | Model | p50 ms | p95 ms | p95/p50 |
|---|---|---|--:|--:|--:|
| FoundationDB | 1 | L1 | 0.16 | 0.36 | 2.20 |
| FoundationDB | 2 | L1 | 0.16 | 0.30 | 1.91 |
| FoundationDB | 4 | L1 | 3.32 | 5.21 | 1.57 |
| Oracle NoSQL | 8 | L2 | 10.24 | 13.92 | 1.36 |
| FoundationDB | 6 | L1 | 0.74 | 1.00 | 1.35 |
| Oracle NoSQL | 2 | L1 | 2.03 | 2.70 | 1.33 |

Nothing exceeds 2.2×, and the widest ratios are all on sub-millisecond operations
where scheduler noise dominates. The medians above are trustworthy.

---

## 5. What the numbers say

### 5.1 Schema choice dominates database choice

The largest within-database, between-model gap is **12,307×** (query 1 on Oracle
NoSQL: 0.77 ms against 9.5 s). The typical between-database, same-model gap is
**1.2–5×**. Choosing the wrong model for a workload costs three to four orders of
magnitude; choosing the "wrong" database of these two usually costs less than one.

The single exception is query 6, where the databases differ by 1,177× — and that
gap is itself a schema effect, described next.

### 5.2 The ordered key space is the sharpest difference between the two databases

Query 6 — the 20 most popular Drama movies — takes **0.74 ms on FoundationDB L1**
and **875 ms on Oracle NoSQL L1**, for an identical answer.

FoundationDB stores the negated scaled popularity inside the key, so the 20 rows
are 20 physically consecutive keys and the query is a bounded range read with no
sorting. Oracle NoSQL has the same information in `idx_genre_pop(doc.genre_ids[],
doc.popularity)`, but `ORDER BY t.doc.popularity DESC LIMIT 20` costs scan-like
time, suggesting the index is used for the genre predicate and not for the
ordering. **This inference is not confirmed** — we have not captured a query plan,
and doing so is the obvious next step. What is confirmed is the cost.

The practical consequence is already visible in the same table: Oracle NoSQL's own
L2 answers query 6 in 2.48 ms from the precomputed leaderboard, 353× faster than
its L1 path. When ordering cannot come from an index, precompute it.

### 5.3 The prediction that server-side `GROUP BY` would dominate was wrong

`common/schema.md` predicted that Oracle NoSQL's server-side aggregation would be
the largest gap in the benchmark, since FoundationDB has none and must scan
client-side. Measured, on the L1 model:

| Aggregate | Oracle (SQL `GROUP BY`) | FoundationDB (client-side scan) | |
|---|--:|--:|---|
| Q8 genre × year | 1,136 ms | 832 ms | FDB 1.4× faster |
| Q9 languages | 686 ms | 775 ms | Oracle 1.1× faster |
| Q10 yearly trend | 1,265 ms | 779 ms | FDB 1.6× faster |

FoundationDB's "handicap" wins two of three. Reading 68 MB out of a local
FoundationDB process and parsing it in Python is simply competitive with running a
grouped query inside a single-node JVM store and streaming the results back over
HTTP. Query 8 also flatters FoundationDB structurally: Oracle NoSQL cannot group
by an array element in one statement, so its Q8 is 19 separate `GROUP BY` queries,
one per genre, while FoundationDB makes a single pass.

The honest conclusion is not "FoundationDB aggregates better" — it is that on a
single-node store at this data size, **server-side aggregation is not the
advantage it looks like on paper**. It would very likely matter at a data size
where shipping the corpus to the client stops being viable, and that is the
experiment to run next.

### 5.4 L2's downside is far worse on Oracle NoSQL

When L2 has no access path it must scan every chunk. That costs **~330 ms on
FoundationDB and ~9.5 s on Oracle NoSQL** — a 28–32× difference, consistent across
queries 1, 2, 4 and 5.

Both stores hold the same 68 MB in the same 806 chunks, so this is not a storage
difference. It is the read path: FoundationDB hands back raw bytes that Python's
`json` parses directly, while Oracle NoSQL streams query results through the HTTP
proxy and borneo materializes each chunk into Python objects on the way. The cost
of L2's worst case is therefore a property of the *client protocol*, not of the
schema — and it means the penalty for a missing access path is an order of
magnitude harsher on Oracle NoSQL.

### 5.5 Query 7 changes its answer depending on the database

Query 7 — same language, within ±1 year, excluding the movie itself — is the one
query where the two databases disagree about which model to use:

- **FoundationDB:** L2 wins 6.1× (54 ms vs 333 ms). L1 has no `(lang, year)`
  index, so it reads three year buckets from the index, then fetches ~21,000
  records individually to check their language.
- **Oracle NoSQL:** L1 wins 3.9× (394 ms vs 1,549 ms). The same predicate is one
  SQL statement, and the filtering happens server-side; L2 meanwhile has to pull
  three years of chunks through the proxy.

The general lesson: where a model lacks an index, the cost of the fallback depends
on whether the database can filter for you. Oracle NoSQL can, so its L1 fallback
stays cheap. FoundationDB cannot, so its L1 fallback means round-tripping every
candidate record.

### 5.6 Query 3 is the only genuine tie

Reading all 7,871 movies of 2017 costs 12–30 ms in every combination, and the
winner flips by database (Oracle L1 2.4× ahead, FoundationDB L2 1.1× ahead). At
this selectivity — 7% of the corpus — the index-then-fetch path and the read-56-
chunks path cost about the same. Somewhere near this fraction is the crossover
point between the two models, which is a nice thing to be able to point at.

### 5.7 Scorecard against the predictions

`common/schema.md §5` predicted a winner for each query before any measurement.

| Predicted winner | Held on FoundationDB | Held on Oracle NoSQL |
|---|---|---|
| Q1, Q2, Q4, Q5 → L1 | ✅ ✅ ✅ ✅ | ✅ ✅ ✅ ✅ |
| Q6, Q8, Q9, Q10 → L2 | ✅ ✅ ✅ ✅ | ✅ ✅ ✅ ✅ |
| Q3 → L2 | ✅ (1.1×, effectively a tie) | ❌ L1 by 2.4× |
| Q7 → L2 | ✅ (6.1×) | ❌ L1 by 3.9× |

**18 of 20 predictions held.** Both misses are on Oracle NoSQL, and both have the
same cause: server-side filtering makes L1's non-indexed fallback much cheaper
than the access-path analysis assumed, because that analysis counted round trips
rather than bytes moved.

---

## 6. Limitations

Stated plainly, because the assignment asks for the problems encountered and
because several of these bound how far the conclusions can be pushed.

1. **One run per configuration.** `PLAN.md §Phase 4` asks for three repetitions;
   this is one. The tight p95/p50 ratios (§4.4) suggest the medians are stable,
   but the numbers are not yet replicated.
2. **Scan-heavy paths are measured at n=3.** Their p95 and p99 are not meaningful.
3. **Both models share a store.** L1 and L2 are loaded into the same database, so
   a query may benefit from cache warmed by a different model, and the footprint
   figures in §4.1 are combined rather than per-model. Separating them needs one
   model loaded at a time.
4. **No concurrency and no CPU-limit runs.** Requirement 4 (single vs multiple
   processors) and the 1/4/16-thread sweep are not done. Everything here is single
   threaded.
5. **kvlite is a single-node development store** by design, and FoundationDB runs
   as a single process with `single` redundancy. Neither is a tuned deployment,
   and §5.3's conclusion in particular may not survive a real cluster.
6. **Client libraries are part of what is measured.** borneo materializes rows into
   Python objects; the FoundationDB binding returns bytes. §5.4 is substantially a
   client-protocol result. Both are the idiomatic Python client for their database,
   which is the fair comparison to make, but it is not a pure storage-engine
   comparison.
7. **Query 6's cause is inferred, not confirmed** — no query plan was captured.
8. **L2's queries 1 and 2 stop scanning early** once the movie is found. The probe
   movie is from 2019, second-to-last in key order, so the measurement is close to
   the worst case but not exactly it.
9. **Query 7 takes the probe movie's year and language as constants** rather than
   looking them up first, so that both models measure the same work.

---

## 7. Conclusions

1. **Keep both models.** L1 is faster on queries 1, 2, 4 and 5 by up to four
   orders of magnitude; L2 is faster on 6, 8, 9 and 10 by up to three. No single
   model is defensible for the whole query set, and L2's 1,867 keys cost so little
   that carrying it alongside L1 is close to free.
2. **Design the key, not just the index.** The single largest cross-database
   difference in the whole study (1,177×, query 6) comes from putting a negated
   sort component *inside* the key rather than relying on a secondary index plus
   `ORDER BY`. That technique is available in any ordered key-value store and cost
   nothing to implement.
3. **Precompute what you cannot order.** Oracle NoSQL could not serve query 6
   quickly from an index, but serves it in 2.48 ms from L2's leaderboard. Where the
   engine cannot provide an access path, the schema can.
4. **A missing access path costs more on Oracle NoSQL than on FoundationDB**
   (~9.5 s vs ~330 ms for a full scan), so the penalty for schema/workload mismatch
   is asymmetric between the two.
5. **Server-side SQL is not automatically an advantage at this scale.** Oracle
   NoSQL's `GROUP BY` lost to FoundationDB's client-side scan on two of three
   aggregates.

### Next steps

- Capture Oracle NoSQL's query plan for query 6 to confirm or refute §5.2.
- Add `idx_lang_year` / `("idx","lang_year", lang, year, id)` and re-measure query
  7 — the one query where L1's index set demonstrably does not fit.
- Load each model alone to get per-model footprints.
- Run the concurrency and CPU-limit sweeps (`PLAN.md §Phase 4`), and three
  repetitions of everything.

---

## 8. Reproducing this

Raw measurements are committed as `bench/results/oracle-nosql.csv` and
`bench/results/foundationdb.csv`. `python -m bench.report` regenerates every table
in §4 from those files, so the document cannot drift from the data. The full
sequence is in §3.
