# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A university coursework project (Неструктурирани бази на податоци, 2025/2026) comparing
**three** databases over the same TMDB movies dataset (109,222 records, 2000–2020 fetch
years): two key-value NoSQL stores — **Oracle NoSQL Database CE** (sub-team A) and
**FoundationDB** (sub-team B) — plus **PostgreSQL** as a joint relational control, added
by the team to give the two key-value stores a baseline to be measured against. The
deliverable is an елаборат (report) plus benchmarks, not a shipped application.

**The thesis is a two-axis comparison: two aggregation models over unstructured data in two
key-value stores, set against structured data in a relational one.** The same 109,222 records
are keyed two ways — L1 fine-grained, L2 coarse-grained — in Oracle NoSQL and FoundationDB.
PostgreSQL carries both of those in JSONB *and* model R, a normalized 3NF schema of typed
columns. So there are two separate questions, and every result belongs to one of them:

- *Does the aggregation model matter?* L1 against L2, inside one database.
- *Does the storage paradigm matter?* Unstructured key-value against structured relational,
  on identical data and identical query semantics.

PostgreSQL's L1/L2 are the control for the second question — a relational engine asked to
behave like a key-value store. Model R is the same engine allowed to be itself.

**`PLAN.md` is the source of truth.** It carries the phase plan, verified dataset facts,
key-space designs (L1/L2), the 10 query definitions, and the benchmark protocol. Read it
before doing substantive work; update its status markers when a phase advances.

Phases 1–4 are done for all three databases. The stacks are up, every schema loads and
verifies, and all ten queries are implemented on all seven (database, model) pairs — L1 and L2
in each of the three databases, plus model R in PostgreSQL (`bench/queries.py`). The full
Phase 4 sweep — latency, concurrency, and 1-vs-4 CPUs — was measured on
**2026-09-13, all three databases on one machine in one sitting** (macOS 15 / Apple Silicon).
**Model R was measured separately on 2026-09-15**, so say which sitting a number comes from
wherever an R figure stands beside a key-value one. Results in `bench/results/*.csv`, analysis
in [docs/schema-comparison.md](docs/schema-comparison.md), combined tables in
`bench/results/report-with-r.md`.

Headline: PostgreSQL was fastest on all twenty query/model combinations, so at this scale
the key-value stores bought nothing measurable — with three real qualifications recorded in
§5.8. Three earlier conclusions changed when everything was re-measured on one machine; they
are marked ⚠ in §5. What is still open: three repetitions per configuration, per-model
footprints, and FoundationDB's multi-process cluster (`configure double ssd`).

## Setup

```bash
python download.py                    # populates data/ from Kaggle (gitignored, ~135 MB)

cd docker/oracle-nosql && docker compose up -d --build
cd docker/foundationdb && docker compose up -d --build
docker exec fdb fdbcli --exec "configure new single ssd"   # MANDATORY once per fdbdata volume
cd docker/postgres && docker compose up -d --build
```

Smoke tests (run these to confirm a machine is set up):

```bash
docker compose exec client python docker/oracle-nosql/smoke_test.py   # from docker/oracle-nosql
docker compose exec client python docker/foundationdb/smoke_test.py   # from docker/foundationdb
docker compose exec client python docker/postgres/smoke_test.py       # from docker/postgres
```

There is no build, lint, or unit-test suite. Verification is the smoke tests, the checklists
in `docker/*/README.md`, `python -m common.verify_schemas` (offline), `common.live_check`
(per database), and `python -m bench.answers --compare`, which checks that every
implementation of every query — seven of them, plus a brute-force one computed straight from
`data/` — returns the same answer.

The client container names are `kv-client`, `fdb-client` and `pg-client`.

## Non-negotiable constraints

These were established by actual debugging during Phase 1; violating them silently breaks things.

- **Never add a `platform:` key to any of the three compose files.** All three images are
  multi-arch. Forcing `linux/amd64` runs under emulation and invalidates every Phase 4
  benchmark number from that machine.
- **All database access runs inside the `client` container**, not from the host. For
  FoundationDB this is mandatory: the cluster file advertises the container IP, and the
  Linux `network_mode: host` workaround does not work on Docker Desktop for Mac. For Oracle
  NoSQL and PostgreSQL it is a fairness requirement — all three DBs must be driven from
  equivalent Python 3.11 client containers. Published host ports (4500, 5000, 5999, 5433)
  are for diagnostics only.
- **PostgreSQL runs with stock configuration.** `shared_buffers` stays at 128 MB and nothing
  else is touched, because kvlite and `fdbserver` are equally untuned. Tuning only the
  relational engine is the easiest available way to make the whole comparison meaningless.
- **`foundationdb` pip version must exactly equal the server image tag** (`7.3.79` today).
  Bump both together or neither. API version is `730`.
- **Oracle NoSQL is reached over the HTTP proxy on 8080 via `borneo`**, never port 5000
  directly — kvlite registers its storage node under the container hostname, which a host-side
  driver cannot route to. The only working healthcheck path is `/V2/nosql/data`; `/` and
  `/ping` return 400.
- **`docker compose down -v` on FoundationDB wipes the store** and requires
  `configure new single ssd` again. A plain `down`/`up` does not.

## Data handling

- The 21 files in `data/` are **JSONL**, not JSON arrays — always stream line by line, never
  `json.load` a whole file.
- Derive `year` from `release_date`, never from the filename: files are grouped by fetch year,
  so `tmdb-movies-2000.json` contains a movie released in 2001. 5,442 records have an empty
  `release_date` and have no derivable year.
- Known data-quality facts that shape query semantics (measured, see `PLAN.md §0.1`): 20.1% of
  movies have no genre; 37.6% have `vote_count == 0`, hence the agreed `MIN_VOTE_COUNT = 50`
  floor for any "top rated" query.
- Store the UTF-8 form (`ensure_ascii=False`), not the raw `\uXXXX` escapes, so byte sizes are
  comparable across the two databases.

## Database-specific limits that drive the design

FoundationDB: value 100 KB, key 10 KB, transaction 10 MB / 5 s. Batch bulk writes at ~500–1,000
pairs per transaction with retry on `transaction_too_old`. The L2 per-year blob (largest year is
4.38 MB) must be chunked at ~90 KB. Keys sort in order, so a negated scaled integer inside a
tuple-packed key yields descending reads with no client sorting.

Oracle NoSQL: supports SQL `GROUP BY`, indexes on JSON array fields (`doc.genre_ids[] AS INTEGER`),
and composite keys with a shard component (`PRIMARY KEY(SHARD(year), chunk)`). kvlite is
single-node by design — state that as a limitation rather than simulating a cluster.

PostgreSQL: JSONB with a GIN index (`jsonb_path_ops`) for array containment, expression
B-trees over JSON paths, `LATERAL jsonb_array_elements_text` to group by an array element in
one statement, and `FILTER` to apply a vote floor in the same pass. Two things it cannot do
and one it does unasked, all of which are results rather than incidents:

- **No index over "array element paired with a scalar."** The L1 `idx_genre_pop` family has
  no relational expression — an expression index yields one entry per row, and the family
  needs one per (movie, genre). GIN indexes the array but carries no order. Query 6 is
  therefore served either by membership or by order, never both.
- **JSONB is not the bytes it was given.** It decomposes and reprints, so `movies::text`
  measures several KB larger than the chunk the key-value stores hold. Compare chunk
  *boundaries*, never byte lengths, when checking the three databases hold the same data.
  The normalization in `common/dataset.py` already sorts keys and drops nulls, so nothing
  about the document is actually lost.
- **It parallelizes a single query.** Query 8's plan shows `Workers Launched: 2` at stock
  settings. It is the only one of the three that uses more than one core for one query,
  which matters for requirement 4.

## Working conventions

- All three implementations must use the *same* L1 and L2 models and the *same* 10 query
  semantics, or the Phase 4 comparison is worthless. Key formats and query definitions belong
  in `common/` and must be agreed before any loader is written. `python -m bench.answers
  --compare` is what enforces this; run it before believing any timing.
- **Same semantics, idiomatic implementation.** The three backends are not transliterations of
  each other — each database is asked the question the way that database is meant to be asked.
  Oracle NoSQL uses `GROUP BY` where FoundationDB scans client-side; PostgreSQL unnests L2
  chunks inside the executor where the other two ship all 806 chunks to the client. Those are
  real capability differences and belong in the report; hobbling one engine to match another
  would measure nothing. Wherever it changes which side of the connection the work happens on,
  say so next to the number.
- **Never let a sandbox number reach the report.** `sandbox/make_corpus.py` generates a
  stand-in corpus with the measured *shape* of the real dataset for verifying code on a
  machine with no Kaggle access. It proves correctness and nothing else. Every timing in the
  елаборат comes from a run on the real `data/`, and all three databases' numbers must come
  from the same machine in the same sitting.
- Loaders must be idempotent and restartable, and must record wall-clock load time and resulting
  on-disk size — both feed the Phase 4 results section.
- `docker/*/README.md` are literal installation logs, and section 4 of the елаборат is graded on
  them. When something breaks and is fixed, append the actual commands and captured output there
  rather than describing it abstractly.
- `sandbox/try_*.py` are throwaway one-file explorations of each driver, useful as API references
  for the real loaders.
