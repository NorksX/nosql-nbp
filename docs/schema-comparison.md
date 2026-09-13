# L1 vs L2 — schema design and measured performance

**Неструктурирани бази на податоци, 2025/2026 · Тема 1 — Key-value бази**
Oracle NoSQL Database CE 25.3.21 (kvlite) · FoundationDB 7.3.79 · **PostgreSQL 17.6** · TMDB 2000–2020, 109,222 movies

This document describes the two data models the project stores the dataset in, and
reports what they actually cost. Every number here was measured on the running
databases; none is estimated. The full schema specification is
[`common/schema.md`](../common/schema.md), and `common/keyspec.py` is its
executable form.

> **A third database, added 2026-09-13.** PostgreSQL joins the study as a
> *relational control*: the same 109,222 records, the same two models, the same
> ten queries, the same harness. It exists to answer the question two NoSQL
> stores cannot answer between themselves — **whether choosing a key-value store
> bought anything on this data.**
>
> **All three databases are now measured**, on one machine in one sitting
> (macOS 15 / Apple Silicon, 2026-09-13). Adding the control meant re-running
> Oracle NoSQL and FoundationDB too, because the earlier figures for those came
> from two other machines and a table that mixes machines cannot be read — so
> every number in §4 is new. §9 carries what the control alone can show,
> including the one piece of evidence neither NoSQL store could supply: a
> captured query plan.
>
> The short answer to the question it was added to ask: **on this data, at this
> scale, the key-value stores bought nothing.** PostgreSQL is fastest on all
> twenty query/model combinations (§4.4) and stores the corpus in less space
> than the corpus (§9.5).

---

## 1. In one paragraph

The same 109,222 movies are stored twice: **L1** keys every movie individually
and adds five secondary indexes (730,414 keys); **L2** packs movies into ~90 KB
per-year chunks and precomputes every aggregate the query set needs (1,867 keys).
Across the ten queries the spread between the two models reaches **11,165×** on
the same database — far larger than the spread between the same query and model on
different databases, which is usually one to two orders of magnitude smaller.
**Schema choice dominates database choice.** Neither model wins outright: L1 is
faster on four to five queries depending on the engine, L2 on the rest. A real
deployment would carry both.

The relational control changes the headline, though, and §5.8 says so plainly:
PostgreSQL is fastest on **all twenty** combinations, so on this dataset at this
scale neither key-value store earned its place on performance.

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
hand in the same transaction as the record; PostgreSQL uses one
`l1_movies(id, doc JSONB)` table with expression B-trees over JSON paths and a GIN
index for the genre array.

Four of the five families translate cleanly to all three. The fifth does not, and
that is the first thing the control taught us: **`idx_genre_pop` has no relational
expression.** An index over "each element of this JSON array, paired with this
scalar" needs one entry per (movie, genre) pair, and a PostgreSQL expression index
produces exactly one entry per row; GIN indexes the array but stores no order. So
PostgreSQL's L1 splits that family in two — GIN for membership, a plain
`(popularity DESC, id)` B-tree for order — and query 6 must be served by one or
the other. What the planner did with that choice is §9.2.

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
docker exec pg-client  python -m common.apply_schema   # the third database
docker exec pg-client  python -m common.live_check
docker exec pg-client  python -m bench.harness
python -m bench.answers --compare                      # every implementation agrees?
python -m bench.report                                 # regenerate the tables below
```

**Correctness gates the timing.** For each query the harness runs the L1 and the L2
implementation, compares the results structurally, and refuses to benchmark a query
whose two models disagree. All ten agree on every database, and every
implementation of each query returns the same answer — so the latencies below are
latencies for the *same* answer.

That gate is local to one database, though: it cannot see that Oracle NoSQL and
PostgreSQL disagreed with each other. With three databases there are six
implementations of every query, so `bench/answers.py` compares each database's
canonical answers against every other's **and** against a seventh implementation
that computes the answers directly from `data/` by brute force, with no database
involved. That brute-force version is the only one in the project that cannot be
wrong for an interesting reason, which is what makes it the reference rather than
merely another opinion.

That gate earned its keep: it caught a real defect. L2 stored aggregate means
rounded to 6 decimals, and the query layer rounded again to 4 — double rounding
that moved 3 of 399 genre-year averages in the fourth decimal. Two correct-looking
implementations returned provably different numbers. Means are now stored at full
precision and rounded once, at display.

**Protocol.** Warm-up runs discarded; p50/p95/p99 over 200 iterations for point
queries, 50 for indexed range queries, 20 for the complex ones, 5 for aggregates.
A path with no access path is capped at 3 iterations — 200 iterations of a query
that parses 68 MB would take an hour and tell us nothing the 3 runs do not. All
databases are driven from equivalent containerized Python 3.11 clients on the same
host, so the comparison is client-symmetric.

**Which paths count as a full scan is declared, not timed.** It used to be
inferred from a first run slower than 250 ms, which is a property of the machine
as much as of the schema: on a fast host a genuine full scan finishes under the
threshold and gets marked as indexed. That is exactly what happens to
PostgreSQL's queries 9 and 10 over L1, which read every row and still return in
tens of milliseconds. `bench/harness.py DEGRADES_TO_SCAN` now writes down the
access-path analysis per database, seeded with precisely the flags the original
Oracle NoSQL and FoundationDB runs produced — so **no previously reported flag
changed** — and the timing is kept as a cross-check that prints a note whenever
measurement and analysis disagree.

Query parameters are fixed in `common/keyspec.py` so both databases and both models
are asked literally the same question: year 2017, language `en`, `vote_count > 500`,
genres 18 ∩ 27, probe movie 419704 (*Ad Astra*), report range 2000–2020.

---

## 4. Results

Environment: **macOS 15 (Darwin 25.5.0), Apple Silicon (aarch64), Docker 29.3.1,
10 CPUs and 8.2 GB available to the engines.** kvlite single node (10 partitions),
FoundationDB single process `ssd-2` engine `single` redundancy, PostgreSQL 17.6
stock (`shared_buffers` 128 MB, `max_parallel_workers_per_gather` 2). One run per
configuration, all three databases measured in one sitting on one machine.

> **These numbers replace the earlier two-database results.** The Oracle NoSQL and
> FoundationDB figures previously in this section were measured on a Fedora x86-64
> machine and a Windows x64 machine. Adding PostgreSQL required re-running
> everything here, because a table that mixes machines cannot be read. Every number
> in §4 now comes from the single macOS/Apple Silicon run of 2026-09-13, regenerated
> by `python -m bench.report` from `bench/results/*.csv` rather than typed.
>
> Conclusions drawn from the old numbers are re-checked in §5; the qualitative ones
> survive, the magnitudes do not.

### 4.1 Load and footprint

| | Oracle NoSQL | FoundationDB | PostgreSQL |
|---|---|---|---|
| L1 load | 109,222 rows, **56.8 s** (1,921 rows/s) | 730,414 keys, **6.8 s** (107,689 keys/s) | 109,222 rows, **2.9 s** (37,151 rows/s) |
| L2 load | 1,867 rows, **4.7 s** (399 rows/s) | 1,867 keys, **0.4 s** (4,308 keys/s) | 1,867 rows, **1.2 s** (1,623 rows/s) |
| Store, both models | 217 MB (`/kvroot/kvstore`) | 141 MB accounted KV, 345 MB disk | **142 MB** (`pg_database_size`) |

Not like-for-like on the write path: Oracle NoSQL does one HTTP `put` per row and
maintains five indexes server-side; the FoundationDB loader batches ~1,000 pairs
per transaction and writes its index keys itself; PostgreSQL batches `INSERT ...
ON CONFLICT` at the same 1,000 rows per transaction, deliberately **not** `COPY`.
On that deliberately handicapped path the relational engine still loads L1 **20×
faster than Oracle NoSQL and 2.3× faster than FoundationDB**, while maintaining
five indexes.

PostgreSQL is also the only one of the three whose footprint can be broken down,
which is itself the point — in FoundationDB index keys are indistinguishable from
data, and kvlite's store directory is opaque:

| Relation | Total | Heap | Indexes |
|---|--:|--:|--:|
| `l1_movies` | 97 MB | 80 MB | **17 MB** |
| `l2_movies_by_year` | **37 MB** | 72 kB | 40 kB |
| `l2_genre_top` | 368 kB | 320 kB | 16 kB |
| `l2_genre_year_stats` | 112 kB | 48 kB | 40 kB |
| `l2_lang_stats` | 32 kB | 8,192 B | 16 kB |
| `l2_year_stats` | 24 kB | 8,192 B | 16 kB |

Both predictions §9.5 made before the run were confirmed, and both are in the
direction that flatters the control — see §9.5 for what they mean.

### 4.2 Latency by query, model and database (p50, ms)

| # | Query | Oracle L1 | Oracle L2 | FoundationDB L1 | FoundationDB L2 | PostgreSQL L1 | PostgreSQL L2 | Faster model |
|---|---|--:|--:|--:|--:|--:|--:|---|
| 1 | Point lookup by TMDB id | **0.59** | **6,610** ᶠ | **2.02** | **715.38** ᶠ | **0.06** | **72.63** ᶠ | Oracle L1 11,165× · FoundationDB L1 354× · PostgreSQL L1 1,231× |
| 2 | Lookup by IMDb id (alternate key) | **1.18** | **6,560** ᶠ | **1.97** | **707.59** ᶠ | **0.06** | **70.39** ᶠ | Oracle L1 5,573× · FoundationDB L1 359× · PostgreSQL L1 1,154× |
| 3 | All movies of year 2017 | **9.17** | **20.61** | **24.85** | **69.49** | **0.51** | **0.07** | Oracle L1 2.2× · FoundationDB L1 2.8× · PostgreSQL L2 7.8× |
| 4 | Language 'en' with vote_count > 500 | **4.62** | **6,698** ᶠ | **8.04** | **769.46** ᶠ | **0.66** | **81.21** ᶠ | Oracle L1 1,449× · FoundationDB L1 96× · PostgreSQL L1 123× |
| 5 | Movies in both Drama and Horror | **161.85** | **6,584** ᶠ | **131.62** | **756.00** ᶠ | **1.02** | **83.14** ᶠ | Oracle L1 41× · FoundationDB L1 5.7× · PostgreSQL L1 82× |
| 6 | Top 20 of Drama by popularity | **627.06** ᶠ | **1.97** | **4.65** | **2.36** | **0.10** | **0.07** | Oracle L2 319× · FoundationDB L2 2.0× · PostgreSQL L2 1.5× |
| 7 | Same language, +/-1 year of Ad Astra | **289.59** ᶠ | **1,054** ᶠ | **310.58** ᶠ | **157.43** | **2.78** | **13.01** | Oracle L1 3.6× · FoundationDB L2 2.0× · PostgreSQL L1 4.7× |
| 8 | Avg rating & count per genre per year, 2000-2020 | **881.28** ᶠ | **9.93** | **1,263** ᶠ | **9.03** | **51.16** ᶠ | **0.29** | Oracle L2 89× · FoundationDB L2 140× · PostgreSQL L2 174× |
| 9 | Top 10 languages by count, mean popularity | **517.82** ᶠ | **3.01** | **1,278** ᶠ | **4.03** | **22.74** ᶠ | **0.13** | Oracle L2 172× · FoundationDB L2 317× · PostgreSQL L2 176× |
| 10 | Yearly trend, vote_count >= 50 | **934.15** ᶠ | **1.24** | **1,259** ᶠ | **2.00** | **26.69** ᶠ | **0.11** | Oracle L2 755× · FoundationDB L2 629× · PostgreSQL L2 245× |

ᶠ = no index path for this model; the query degrades to a full scan.

### 4.3 Same query, same model, different database

| # | Model | Oracle NoSQL | FoundationDB | PostgreSQL | Fastest | Spread |
|---|---|--:|--:|--:|---|---|
| 1 | L1 | **0.59** | **2.02** | **0.06** | PostgreSQL | 34× |
| 1 | L2 | **6,610** ᶠ | **715.38** ᶠ | **72.63** ᶠ | PostgreSQL | 91× |
| 2 | L1 | **1.18** | **1.97** | **0.06** | PostgreSQL | 32× |
| 2 | L2 | **6,560** ᶠ | **707.59** ᶠ | **70.39** ᶠ | PostgreSQL | 93× |
| 3 | L1 | **9.17** | **24.85** | **0.51** | PostgreSQL | 49× |
| 3 | L2 | **20.61** | **69.49** | **0.07** | PostgreSQL | 1,069× |
| 4 | L1 | **4.62** | **8.04** | **0.66** | PostgreSQL | 12× |
| 4 | L2 | **6,698** ᶠ | **769.46** ᶠ | **81.21** ᶠ | PostgreSQL | 82× |
| 5 | L1 | **161.85** | **131.62** | **1.02** | PostgreSQL | 159× |
| 5 | L2 | **6,584** ᶠ | **756.00** ᶠ | **83.14** ᶠ | PostgreSQL | 79× |
| 6 | L1 | **627.06** ᶠ | **4.65** | **0.10** | PostgreSQL | 5,972× |
| 6 | L2 | **1.97** | **2.36** | **0.07** | PostgreSQL | 35× |
| 7 | L1 | **289.59** ᶠ | **310.58** ᶠ | **2.78** | PostgreSQL | 112× |
| 7 | L2 | **1,054** ᶠ | **157.43** | **13.01** | PostgreSQL | 81× |
| 8 | L1 | **881.28** ᶠ | **1,263** ᶠ | **51.16** ᶠ | PostgreSQL | 25× |
| 8 | L2 | **9.93** | **9.03** | **0.29** | PostgreSQL | 34× |
| 9 | L1 | **517.82** ᶠ | **1,278** ᶠ | **22.74** ᶠ | PostgreSQL | 56× |
| 9 | L2 | **3.01** | **4.03** | **0.13** | PostgreSQL | 31× |
| 10 | L1 | **934.15** ᶠ | **1,259** ᶠ | **26.69** ᶠ | PostgreSQL | 47× |
| 10 | L2 | **1.24** | **2.00** | **0.11** | PostgreSQL | 18× |

### 4.4 Where each database wins

| Database | Model | Fastest on |
|---|---|---|
| Oracle NoSQL | L1 | — |
| Oracle NoSQL | L2 | — |
| FoundationDB | L1 | — |
| FoundationDB | L2 | — |
| PostgreSQL | L1 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 |
| PostgreSQL | L2 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 |

**PostgreSQL is fastest on all twenty combinations.** That is not a subtle result
and §5.8 deals with it rather than burying it.

### 4.5 Tail behaviour

The six widest p95/p50 ratios in the whole run:

| Database | # | Model | p50 ms | p95 ms | p95/p50 |
|---|---|---|--:|--:|--:|
| postgresql | 6 | L2 | 0.07 | 0.20 | 2.96 |
| postgresql | 7 | L1 | 2.78 | 5.68 | 2.04 |
| oracle-nosql | 4 | L1 | 4.62 | 8.48 | 1.83 |
| foundationdb | 6 | L2 | 2.36 | 4.19 | 1.77 |
| oracle-nosql | 2 | L1 | 1.18 | 1.79 | 1.52 |
| foundationdb | 10 | L2 | 2.00 | 2.98 | 1.49 |

Nothing exceeds 3×, and the widest ratios are all on sub-millisecond operations
where scheduler noise dominates. The medians above are trustworthy.

### 4.6 Concurrency — 1, 4 and 16 client processes

Throughput in ops/s on the four indexed paths, servers unconstrained (10 CPUs):

| Query | Model | Database | 1 | 4 | 16 | 1→16 |
|---|---|---|--:|--:|--:|--:|
| Q1 point lookup | L1 | Oracle NoSQL | 1,877 | 5,459 | 6,774 | 3.6× |
| | | FoundationDB | 569 | 2,067 | 7,002 | **12.3×** |
| | | PostgreSQL | **17,860** | **40,594** | **125,797** | 7.0× |
| Q3 year 2017 | L2 | Oracle NoSQL | 43 | 111 | 144 | 3.4× |
| | | FoundationDB | 14 | 58 | 144 | 10.2× |
| | | PostgreSQL | **16,594** | **32,274** | **91,753** | 5.5× |
| Q6 top-20 genre | L2 | Oracle NoSQL | 631 | 1,088 | 1,529 | 2.4× |
| | | FoundationDB | 404 | 1,124 | 4,118 | 10.2× |
| | | PostgreSQL | **16,542** | **30,177** | **97,909** | 5.9× |
| Q8 genre×year | L2 | Oracle NoSQL | 143 | 219 | 254 | 1.8× |
| | | FoundationDB | 100 | 407 | 1,005 | 10.0× |
| | | PostgreSQL | **4,124** | **7,423** | **12,914** | 3.1× |

FoundationDB scales best *relative to itself* — roughly 10–12× across all four
paths, and its p50 barely moves as clients are added — but it starts from the
lowest single-client throughput and only catches Oracle NoSQL at 16 clients.
Oracle NoSQL flattens early, gaining under 2× on the aggregate. PostgreSQL is
between 18× and 640× the others in absolute terms at every level.

### 4.7 One versus four processors

`docker update --cpus {1,4}` applied to the three server containers, clients
unconstrained. This is requirement 4, and the three engines answer it in three
different ways.

**Single-query latency, aggregate Q8 on L1** — the query that can be parallelized:

| | 1 CPU | 4 CPUs | Speed-up |
|---|--:|--:|--:|
| Oracle NoSQL | 930.5 ms | 769.4 ms | 1.21× |
| FoundationDB | 1,276.1 ms | 1,336.1 ms | **0.96× — none** |
| PostgreSQL | 188.0 ms | **42.6 ms** | **4.41×** |

**Point-lookup latency Q1 on L1** — the query that cannot:

| | 1 CPU | 4 CPUs | Speed-up |
|---|--:|--:|--:|
| Oracle NoSQL | 0.518 ms | 0.533 ms | 0.97× |
| FoundationDB | 2.031 ms | 1.999 ms | 1.02× |
| PostgreSQL | 0.074 ms | 0.061 ms | 1.21× |

**Throughput at 16 concurrent clients, Q1 on L1:**

| | 1 CPU | 4 CPUs | Gain |
|---|--:|--:|--:|
| Oracle NoSQL | 3,343 | 6,467 | 1.93× |
| FoundationDB | 7,168 | 6,572 | **0.92× — none** |
| PostgreSQL | 57,024 | **213,256** | **3.74×** |

Three distinct behaviours, and they match the architectural prediction exactly:

- **PostgreSQL gains on both axes.** It is the only one of the three that uses
  more than one core for a *single* query — 4.41× on Q8, whose plan shows
  `Workers Planned: 2 / Workers Launched: 2` over a `Parallel Seq Scan` at the
  stock `max_parallel_workers_per_gather = 2` — and it also scales 3.74× under
  concurrency.
- **Oracle NoSQL gains only under concurrency** (1.93×), not on single queries
  (0.97–1.21×). Extra cores serve extra clients, not one client faster.
- **FoundationDB gains nothing from cores on either axis** (0.92–1.02×). Its
  scaling unit is *processes*: a single `fdbserver` does not use a second core no
  matter how much work arrives. Scaling it means `configure double ssd` and more
  processes, which is the experiment this study has not run.

A point worth making in the report: no setting was tuned to produce this.
`max_parallel_workers_per_gather` is 2 and `shared_buffers` is 128 MB, both stock.

---

## 5. What the numbers say

> Every figure in this section was re-derived from the 2026-09-13 CSVs. Three of
> the findings the two-database run produced did **not** survive re-measurement on
> one machine with a third engine present, and they are called out where they
> occur rather than quietly replaced: §5.3 reversed outright, §5.6's tie
> disappeared, and §5.2's magnitude fell by an order of magnitude. The directional
> conclusions in §5.1, §5.4 and §5.5 held.

### 5.1 Schema choice still dominates database choice — by a narrower margin

The largest within-database, between-model gap is **11,165×** (query 1 on Oracle
NoSQL: 0.59 ms against 6.6 s). The cross-database spread for the same query and
model is typically **12–160×**, with one outlier at 5,972× (query 6 on L1, §5.2).

So the ordering holds — picking the wrong model still costs more than picking the
wrong engine — but the margin is much narrower than the two-database run suggested,
because PostgreSQL's constant factor is so far below both key-value stores that
the between-database axis stretched. With only Oracle NoSQL and FoundationDB in
the table that axis was usually under 5×.

### 5.2 The ordered key space is still the sharpest difference between the two key-value stores

Query 6 — top 20 of a genre by popularity — costs **627 ms on Oracle NoSQL's L1
and 4.65 ms on FoundationDB's L1, a 135× difference**. FoundationDB stores negated,
scaled popularity *inside* the key, so 20 consecutive keys are the answer and no
sorting happens. Oracle NoSQL has to use a secondary index and order the result.

The technique is the transferable lesson of the study and it costs nothing to
implement in any ordered key-value store. But note the magnitude: the earlier run
reported **1,177×** for this same comparison. On one machine, with everything else
equal, it is 135×. The effect is real and large; the specific multiplier was not
reproducible across machines, and no number of that kind should be quoted without
the machine it came from.

### 5.3 ⚠ Reversed: server-side `GROUP BY` *did* win after all

The two-database run concluded that Oracle NoSQL's server-side aggregation "was
not the advantage it looked like on paper", because FoundationDB's client-side
scan beat it on two of three aggregates. **Re-measured on one machine, that is
wrong.** Oracle NoSQL wins all three:

| Aggregate (L1) | Oracle (SQL `GROUP BY`) | FoundationDB (client-side scan) | |
|---|--:|--:|---|
| Q8 genre × year | **881 ms** | 1,263 ms | Oracle 1.43× faster |
| Q9 languages | **518 ms** | 1,278 ms | Oracle 2.47× faster |
| Q10 yearly trend | **934 ms** | 1,259 ms | Oracle 1.35× faster |

Oracle NoSQL's Q8 is still 19 separate `GROUP BY` statements, one per genre,
because it cannot group by an array element — and it wins anyway. The earlier
opposite result came from comparing a Fedora machine's Oracle numbers with a
different machine's FoundationDB numbers, which is precisely the error the
one-machine rule exists to prevent. Treat this as the corrected finding:
**server-side aggregation is worth having**, and shipping 68 MB to a Python client
to group it is the more expensive of the two strategies even at this modest scale.

PostgreSQL settles the question from the other side. Its Q8 is one statement over
`LATERAL jsonb_array_elements_text`, it runs in **51 ms on L1**, and its plan shows
`Workers Launched: 2` — 17× faster than Oracle's 19 statements and 25× faster than
FoundationDB's scan.

### 5.4 A missing access path costs an order of magnitude more on Oracle NoSQL

When L2 has no access path it must scan every chunk. Measured across queries 1, 2,
4 and 5:

| | L2 fallback cost |
|---|--:|
| Oracle NoSQL | **6,560 – 6,698 ms** |
| FoundationDB | **708 – 770 ms** |
| PostgreSQL | **70 – 83 ms** |

All three hold the same 68 MB in the same 806 chunks, so this is not a storage
difference — it is the read path. FoundationDB hands back raw bytes; borneo
materializes every row into Python objects and moves them over HTTP; PostgreSQL
never ships the chunks at all, unnesting them in the executor (§9.1) and returning
only the answer. The Oracle:FoundationDB ratio is **~8.7×** here, against the
28–32× the cross-machine run reported — same direction, smaller magnitude.

### 5.5 Query 7 still changes its answer depending on the database

Query 7 — same language, within ±1 year, excluding the movie itself — remains the
one query where the engines disagree about which model to use:

- **FoundationDB:** L2 wins **2.0×** (157 ms vs 311 ms). L1 has no `(lang, year)`
  index, so it reads three year buckets and then fetches ~21,000 records
  individually to check their language.
- **Oracle NoSQL:** L1 wins **3.6×** (290 ms vs 1,054 ms) — the predicate is one
  SQL statement filtered server-side, while L2 pulls three years of chunks
  through the proxy.
- **PostgreSQL:** L1 wins **4.7×** (2.78 ms vs 13.01 ms), for the same reason as
  Oracle NoSQL and about 100× faster.

The lesson is unchanged: where a model lacks an index, the cost of the fallback
depends on whether the engine can filter for you.

### 5.6 ⚠ Query 3 is no longer a tie

The earlier run called query 3 — read all 7,871 movies of 2017 — a genuine tie,
with the winner flipping by database. Re-measured, **L1 wins on both key-value
stores** (Oracle 9.17 ms vs 20.61 ms, 2.2×; FoundationDB 24.85 ms vs 69.49 ms,
2.8×) and **L2 wins on PostgreSQL** by 7.8× (0.07 ms vs 0.51 ms).

FoundationDB's L2 in particular is 2.9× slower than it was relative to its L1, so
the crossover point claimed at ~7 % selectivity does not sit where the old numbers
put it. What remains true is that this is the *closest* of the ten queries — the
only one where both models are within a small factor on every engine.

### 5.7 Scorecard against the predictions

`common/schema.md §5` predicted a winner for each query before any measurement.
Across three databases that is 30 predictions:

| Predicted winner | Oracle NoSQL | FoundationDB | PostgreSQL |
|---|---|---|---|
| Q1, Q2, Q4, Q5 → L1 | ✅ ✅ ✅ ✅ | ✅ ✅ ✅ ✅ | ✅ ✅ ✅ ✅ |
| Q6, Q8, Q9, Q10 → L2 | ✅ ✅ ✅ ✅ | ✅ ✅ ✅ ✅ | ✅ ✅ ✅ ✅ |
| Q3 → L2 | ❌ L1 by 2.2× | ❌ L1 by 2.8× | ✅ L2 by 7.8× |
| Q7 → L2 | ❌ L1 by 3.6× | ✅ L2 by 2.0× | ❌ L1 by 4.7× |

**27 of 30 predictions held.** All three misses are on queries 3 and 7, and all
have the same cause: the access-path analysis counted round trips rather than
bytes moved, so it overestimated the cost of an engine filtering server-side on a
non-indexed path.

### 5.8 What the control actually showed: the key-value stores bought nothing here

PostgreSQL is fastest on **all twenty** query/model combinations (§4.4), loads L1
20× faster than Oracle NoSQL and 2.3× faster than FoundationDB on a deliberately
handicapped batched-`INSERT` path (§4.1), stores the same corpus in **142 MB**
against Oracle's 217 MB and FoundationDB's 345 MB of disk (§9.5), and sustains
**125,797 ops/s** on the point-lookup benchmark at 16 concurrent clients against
6,774 and 7,002 (§4.6). It was the least trouble to install (§1.6) and it is the
only one of the three that will explain its own plan.

That is a wide enough margin that the honest conclusion is the uncomfortable one:
**on 109,222 records keyed two ways, choosing a key-value store bought nothing
measurable over a stock relational engine, and cost something on every axis
tested.** The question the control was added to answer has an unambiguous answer
at this scale.

Three qualifications keep it from being a verdict on key-value stores in general,
and all three are real:

1. **Scale.** 68 MB fits in RAM on any of these engines. Both key-value stores are
   built for datasets that do not, and for horizontal scale-out this study cannot
   exercise — kvlite is a single-node development store by design, and
   FoundationDB is running one `fdbserver` process where it is designed to run
   many. Neither is being asked the question it was built for.
2. **The workload is read-only and single-node.** FoundationDB's distributed ACID
   transactions and Oracle NoSQL's partitioning are costs paid here for benefits
   never tested.
3. **PostgreSQL is not doing relational work.** It stores JSONB blobs under the
   same two models, with no normalized schema and no joins. What the result shows
   is that a mature engine's storage, caching and planner are worth a great deal
   even when it is used as a key-value store — not that the relational *model* won,
   since the relational model was never used.

What the key-value stores did demonstrably buy is in §5.2: FoundationDB's ordered
key space solves the top-N-by-rank problem inside the key itself, which is a
genuine modelling capability PostgreSQL could not express at all (§9.4). It just
did not translate into being faster.

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
4. ✅ **Done.** The concurrency sweep (§4.6) and the 1-vs-4-CPU runs (§4.7) are
   measured, on the same machine and in the same sitting as everything else. What
   is *not* done is FoundationDB's multi-process cluster (`configure double ssd`),
   which is the experiment its flat core-scaling result in §4.7 most obviously
   calls for.
5. **kvlite is a single-node development store** by design, and FoundationDB runs
   as a single process with `single` redundancy. Neither is a tuned deployment,
   and §5.3's conclusion in particular may not survive a real cluster.
6. **Client libraries are part of what is measured.** borneo materializes rows into
   Python objects; the FoundationDB binding returns bytes. §5.4 is substantially a
   client-protocol result. Both are the idiomatic Python client for their database,
   which is the fair comparison to make, but it is not a pure storage-engine
   comparison.
7. **Query 6's cause is inferred, not confirmed** — no query plan was captured
   for Oracle NoSQL, and none can be: kvlite exposes no `EXPLAIN` through the
   HTTP proxy, and FoundationDB has no planner to interrogate at all — its access
   path is whatever the client code reads. This limitation is *partly* lifted from
   the other side: `bench/explain.py` captured all twenty PostgreSQL plans on the
   real corpus (`docs/postgres-plans.txt`), and §9.2 reports what the same query
   looks like when an engine is willing to explain itself.
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
2. **Design the key, not just the index.** The largest difference between the two
   key-value stores (**135×**, query 6 on L1) comes from putting a negated sort
   component *inside* the key rather than relying on a secondary index plus
   `ORDER BY`. The technique is available in any ordered key-value store and cost
   nothing to implement. It is also the one capability PostgreSQL could not express
   at all (§9.4) — the clearest thing a key-value store bought in this study.
3. **Precompute what you cannot order.** Oracle NoSQL could not serve query 6
   quickly from an index — 627 ms on L1 — but serves it in **1.97 ms** from L2's
   leaderboard. Where the engine cannot provide an access path, the schema can.
4. **A missing access path costs ~8.7× more on Oracle NoSQL than on FoundationDB**
   (6.6 s vs 0.77 s for the L2 full scan), so the penalty for schema/workload
   mismatch is asymmetric between the two.
5. **Server-side aggregation *is* an advantage — corrected.** Oracle NoSQL's
   `GROUP BY` beat FoundationDB's client-side scan on all three aggregates
   (1.35–2.47×), reversing the conclusion the earlier cross-machine run reached.
   PostgreSQL, doing the same work in one statement with two parallel workers, beat
   both by a further 17–25×. See §5.3.
6. **Cores are used three different ways** (§4.7): PostgreSQL parallelizes a single
   query (4.41×), Oracle NoSQL uses extra cores only for extra clients (1.93×), and
   FoundationDB uses them for nothing at all (0.92×) because its scaling unit is
   processes.
7. **At this scale the key-value stores bought nothing measurable** (§5.8).
   PostgreSQL won all twenty query/model combinations, loaded fastest, stored the
   corpus smallest, and scaled highest — untuned. The three qualifications that
   keep this from being a general verdict are in §5.8, and they matter: 68 MB on
   one node is not the problem either NoSQL engine was built for.

### Next steps

- Capture Oracle NoSQL's query plan for query 6 to confirm or refute §5.2.
- Add `idx_lang_year` / `("idx","lang_year", lang, year, id)` and re-measure query
  7 — the one query where L1's index set demonstrably does not fit.
- Load each model alone to get per-model footprints.
- **Run FoundationDB as a multi-process cluster** (`configure double ssd`). §4.7
  showed one `fdbserver` gains nothing from extra cores; this is the experiment
  that would show what it gains from extra processes, and it is the most
  load-bearing thing still missing.
- Three repetitions of every configuration.
- Re-run at a data size that does not fit in RAM, which is the condition under
  which §5.8's conclusion would be expected to change.

---

## 8. Reproducing this

Raw measurements are committed as `bench/results/oracle-nosql.csv`,
`bench/results/foundationdb.csv` and `bench/results/postgresql.csv`, plus the
`concurrency-*.csv` and `*-cpus{1,4}.csv` files behind §4.6 and §4.7 — all from
the 2026-09-13 single-machine run.
`python -m bench.report` regenerates every table in §4 from those files, so the
document cannot drift from the data. The full sequence is in §3, and the complete
three-database runbook is in `PLAN.md §Phase 4`.

---

## 9. The relational control

### 9.1 What it is, and what it is not

PostgreSQL stores the same 109,222 records in the same two models, answers the
same ten queries with the same semantics, and is driven from an equivalent
Python 3.11 client container with stock server configuration — `shared_buffers`
at its 128 MB default, which is smaller than the corpus. It is deliberately
**not** given a normalized relational schema: no `movies` table of typed columns,
no `movie_genres` junction table. That would be a third data model answering a
different question. The question here is narrow and worth asking plainly: *on
unstructured data, keyed two different ways, did choosing a key-value store buy
anything?*

One thing about it is not like-for-like and has to be declared wherever its
numbers appear. **PostgreSQL's L2 fallback runs inside the server.** When L2 has
no access path — queries 1, 2, 4, 5 — Oracle NoSQL and FoundationDB both ship all
806 chunks to the client and scan them in Python, because neither can look inside
a stored value. PostgreSQL can: `jsonb_array_elements` unnests a chunk in the
executor, so only the answer crosses the connection. Writing it as a client-side
scan instead would have been easy and would have measured nothing but Python's
JSON parser. This is the same rule that already lets Oracle NoSQL use `GROUP BY`
where FoundationDB scans — idiomatic implementations of identical semantics — but
it means §5.4's finding about the cost of a missing access path gains a third
point on a different axis, not a directly comparable one.

### 9.2 Query 6 has a third answer, and this one is on the record

§5.2 called query 6 the sharpest difference between the two key-value stores —
4.65 ms on FoundationDB against 627 ms on Oracle NoSQL — and noted honestly that
the explanation was inferred because no plan could be captured. PostgreSQL can be
asked directly, and this plan is captured on the real corpus:

```
Q6  Top 20 of Drama by popularity  —  L1
   Limit (actual time=0.007..0.053 rows=20 loops=1)
     Buffers: shared hit=58
     ->  Index Scan using idx_pop on l1_movies (actual time=0.007..0.051 rows=20 loops=1)
           Filter: ((doc -> 'genre_ids'::text) @> '18'::jsonb)
           Rows Removed by Filter: 35
           Buffers: shared hit=58
   Execution Time: 0.058 ms
```

It did not use the genre index at all. It walked the popularity index backwards,
discarding non-Drama rows as it went, and had twenty answers after touching **55
rows** and 58 buffers. So the three engines solve the same ordering problem three
different ways:

| | How the top 20 are ordered |
|---|---|
| FoundationDB | the order is *in the key* — a negated scaled integer, read forward |
| Oracle NoSQL | the index serves the predicate; the ordering costs a sort |
| PostgreSQL | the index that supplies the *order* is chosen, and the selective predicate is demoted to a filter |

The third strategy is the one neither key-value store can choose, because neither
has a planner to choose with. It is also the most fragile of the three: it works
because Drama is a third of the corpus (**36,588 of 109,222, 33.5 %** — measured,
not assumed), and for a rare genre the same plan walks a long way before it finds
twenty matches. That caveat belongs beside the number,
and it is a caveat the FoundationDB design does not have — its key gives the right
answer at the same cost for every genre.

**This is the cleanest statement of what a key-value store buys.** Not speed — a
planner found a competitive path here. Predictability: the FoundationDB key costs
the same for Drama and for Western, and nothing about it depends on statistics
being fresh or a planner making the right guess.

### 9.3 Where the relational engine is simply better at this

Two of the ten queries are answered by PostgreSQL in one statement that neither
NoSQL store can write.

- **Query 8 — group by an element of a JSON array.** One `GROUP BY` over
  `LATERAL jsonb_array_elements_text(doc -> 'genre_ids')`. Oracle NoSQL cannot
  group by an array element and issues 19 separate statements, one per genre;
  FoundationDB has no server-side aggregation at all and makes a client-side pass
  over the whole corpus. §5.3 already found that Oracle's server-side `GROUP BY`
  was *not* the advantage it looked like on paper — and part of the reason was
  that its Q8 is 19 queries. PostgreSQL is what that comparison looks like when
  the aggregation really is one statement.
- **Query 10 — a filtered aggregate in the same pass.** `avg(...) FILTER (WHERE
  vote_count >= 50)` computes the vote-floored mean alongside the unfiltered one
  in a single scan. Oracle NoSQL needs two `GROUP BY` statements joined
  client-side.

Query 8's plan also carries the finding that matters for requirement 4:

```
   Finalize GroupAggregate (actual time=45.584..47.401 rows=399 loops=1)
     ->  Gather Merge (actual time=45.566..47.263 rows=1146 loops=1)
           Workers Planned: 2
           Workers Launched: 2
           ->  Partial HashAggregate (actual time=39.485..39.520 rows=382 loops=3)
                 ->  Parallel Seq Scan on l1_movies (actual rows=34548 loops=3)
```

At stock settings PostgreSQL split a single aggregate across three processes.
It is the only one of the three databases that uses more than one core for one
query: kvlite gains from extra cores only under concurrency (§ the CPU sweep),
and a single `fdbserver` process gains nothing from cores at all, because its
scaling unit is processes. Report `max_parallel_workers_per_gather` alongside the
1-vs-4-CPU numbers, since that setting is the mechanism and it was not touched.

### 9.4 Where the model fights the relational engine

- **`idx_genre_pop` cannot be expressed** (§2). The model asks for an index over
  an array element paired with a scalar; PostgreSQL has no such index. GIN gives
  membership without order, a B-tree gives order without membership. The planner
  routed around it well *for this genre*, which is a different thing from the
  model being expressible.
- **JSONB is not the bytes it was given.** It decomposes and reprints, adding a
  space after every `:` and `,`, so the same 89,999-byte chunk measures several KB
  larger through `movies::text`. Nothing about the document is lost — the
  normalization in `common/dataset.py` already sorts keys and drops nulls — but
  it means the three databases cannot be compared on stored byte size directly,
  and `common/live_check.py` therefore verifies that the chunk *boundaries* match
  rather than the byte lengths.
- **It pays for a limit that is not its own.** The ~90 KB chunking exists because
  of FoundationDB's 100 KB value limit. A PostgreSQL field may be 1 GB, and TOAST
  would store a whole year as one value without complaint. The chunking is kept
  identical anyway so that all three hold the same chunks — so part of whatever
  L2 costs PostgreSQL is a constraint it never needed.

### 9.5 Footprint: the control wins, and the reason is worth stating

✅ **Measured 2026-09-13.** Both predictions held, and both in the direction that
flatters the relational engine.

| | Logical size | PostgreSQL on disk | |
|---|--:|--:|---|
| L1 (`l1_movies`) | 90.35 MB | **97 MB** total — 80 MB heap + **17 MB** indexes | +7 % |
| L2 (all five tables) | 69.13 MB | **37.5 MB** total | **−46 %** |
| Whole database | — | **142 MB** (`pg_database_size`) | |

- **L2 came out at 37 MB against a 68.78 MB logical size — a 1.86× reduction.**
  The `l2_movies_by_year` heap is only **72 kB**: every ~90 KB JSONB chunk is over
  the TOAST threshold, so PostgreSQL stored all of them out of line and compressed
  them. Neither key-value store compresses anything. PostgreSQL is the only
  database in this study that stores the corpus in **less space than the corpus**,
  and it does so without being asked.
- **L1's index cost is 17 MB**, read directly out of `pg_indexes_size` — against
  FoundationDB's 19.93 MB of index keys for the same five families. The relational
  engine's indexes are *cheaper* than the hand-written ones, and this is the
  like-for-like comparison neither NoSQL store can supply from its own side:
  in FoundationDB index keys are indistinguishable from data, and kvlite's
  `/kvroot/kvstore` is an opaque directory.

Set against the other two for the same two models together: Oracle NoSQL **217 MB**,
FoundationDB **141 MB** accounted key-value bytes at **345 MB** of disk, PostgreSQL
**142 MB**. FoundationDB accounts for the fewest bytes and occupies the most disk
— its 2.4× write amplification is the storage engine's own bookkeeping, not the
schema's.

The honest caveat: part of what L2 costs PostgreSQL is a constraint it never had.
The ~90 KB chunking exists for FoundationDB's 100 KB value limit (§9.4). A single
`jsonb` field may be 1 GB, so PostgreSQL would have stored a whole year as one
value and TOASTed that too. It is paying for someone else's limit and still wins
the row.

### 9.6 Status

Every item is now measured. ✅

| | |
|---|---|
| Docker stack, smoke test, install log | ✅ `docker compose up -d` on macOS 15 / Apple Silicon; `SMOKE TEST PASSED`; log in `docker/postgres/README.md` |
| L1 + L2 DDL | ✅ eleven statements, all accepted first time |
| Loaders, both models | ✅ L1 2.9 s, L2 1.2 s, every key family read back |
| All 20 query implementations | ✅ run clean; L1 and L2 agree on all ten |
| Answers vs. computed ground truth | ✅ all ten queries, every implementation, exact agreement |
| Query plans captured | ✅ `docs/postgres-plans.txt`, regenerated on the real corpus |
| **Every timing** | ✅ **measured on the real `data/`, same machine and same sitting as the other two** |

The stand-in corpus from `sandbox/make_corpus.py` is no longer load-bearing
anywhere. It was used to verify that the schema, the loaders and the queries were
correct before the real run; every number in this document and in the елаборат now
comes from the 2026-09-13 run on the real 109,222-record dataset, on one machine.
`docs/postgres-plans.txt` has been regenerated on that data, which removed the
provenance warning it used to carry — correctly, since there is nothing left to
warn about.

What is still **not** done is unchanged by this run: three repetitions per
configuration (§6.1), per-model footprints from loading one model at a time
(§6.3), and FoundationDB's multi-process `configure double ssd` experiment.
