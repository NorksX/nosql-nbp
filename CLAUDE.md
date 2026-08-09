# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A university coursework project (Неструктурирани бази на податоци, 2025/2026) comparing two
key-value NoSQL databases — **Oracle NoSQL Database CE** (sub-team A) and **FoundationDB**
(sub-team B) — over the same TMDB movies dataset (109,222 records, 2000–2020 fetch years).
The deliverable is an елаборат (report) plus benchmarks, not a shipped application.

**`PLAN.md` is the source of truth.** It carries the phase plan, verified dataset facts,
key-space designs (L1/L2), the 10 query definitions, and the benchmark protocol. Read it
before doing substantive work; update its status markers when a phase advances.

Phase 1 (installation) is complete. Phases 2–4 (loaders, queries, benchmarks) are unwritten —
`common/`, `oracle/`, `fdb/`, and `bench/` do not exist yet. `PLAN.md §1.2` gives the intended
layout, and explicitly says the shared `common/` module is to be written by hand incrementally,
not scaffolded up front.

## Setup

```bash
python download.py                    # populates data/ from Kaggle (gitignored, ~135 MB)

cd docker/oracle-nosql && docker compose up -d --build
cd docker/foundationdb && docker compose up -d --build
docker exec fdb fdbcli --exec "configure new single ssd"   # MANDATORY once per fdbdata volume
```

Smoke tests (run these to confirm a machine is set up):

```bash
docker compose exec client python docker/oracle-nosql/smoke_test.py   # from docker/oracle-nosql
docker compose exec client python docker/foundationdb/smoke_test.py   # from docker/foundationdb
```

There is no build, lint, or unit-test suite. Verification is the smoke tests plus the
checklists in `docker/*/README.md`.

## Non-negotiable constraints

These were established by actual debugging during Phase 1; violating them silently breaks things.

- **Never add a `platform:` key to either compose file.** Both images are multi-arch. Forcing
  `linux/amd64` runs under emulation and invalidates every Phase 4 benchmark number.
- **All database access runs inside the `client` container**, not from the host. For
  FoundationDB this is mandatory: the cluster file advertises the container IP, and the
  Linux `network_mode: host` workaround does not work on Docker Desktop for Mac. For Oracle
  NoSQL it is a fairness requirement — both DBs must be driven from equivalent Python 3.11
  client containers. Published host ports (4500, 5000, 5999) are for diagnostics only.
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

## Working conventions

- Both sub-teams must implement the *same* L1 and L2 models and the *same* 10 query semantics,
  or the Phase 4 comparison is worthless. Key formats and query definitions belong in `common/`
  and must be agreed before either loader is written.
- Loaders must be idempotent and restartable, and must record wall-clock load time and resulting
  on-disk size — both feed the Phase 4 results section.
- `docker/*/README.md` are literal installation logs, and section 4 of the елаборат is graded on
  them. When something breaks and is fixed, append the actual commands and captured output there
  rather than describing it abstractly.
- `sandbox/try_*.py` are throwaway one-file explorations of each driver, useful as API references
  for the real loaders.
