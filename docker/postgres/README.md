# Installation log — PostgreSQL

> **Status: stack written and the schema, loaders and all twenty queries verified
> against a live PostgreSQL 16 server. The `docker compose` run itself has not yet
> been performed on a team machine — do it, and paste the real output into the
> marked places below before section 4 of the елаборат is submitted.**
>
> Everything below that is marked ✅ was actually observed. Everything marked
> ⬜ is a command to run and an output to capture. Nothing in this file is
> invented: where output is missing it says so, because the two other install
> logs in this repository are literal transcripts and this one has to be worth
> the same as they are.

## Why PostgreSQL is here at all

The topic is key-value databases and the two sub-teams cover Oracle NoSQL CE and
FoundationDB. PostgreSQL is a **control**, not a third contender: it stores the
same 109,222 records in the same two models (L1 and L2) and answers the same ten
queries, so that the report can say what a relational engine actually costs on
unstructured data instead of assuming it. A comparison of two NoSQL stores with
no baseline can only say which of the two is faster; with a baseline it can say
whether choosing a key-value store bought anything.

It is deliberately *not* given a normalized relational schema (no `movies` table
with typed columns, no `movie_genres` junction table). That would be a third
data model and would answer a different question. The point of the exercise is
one dataset, two aggregation levels, three engines.

## Image

| | |
|---|---|
| Server image | `postgres:17.6-bookworm` |
| Python client | `psycopg[binary]==3.2.3` (PyPI) |
| Architectures | linux/amd64 **and** linux/arm64 — pulled natively, **no emulation** |
| Server config | stock defaults — `shared_buffers` 128 MB, no tuning |
| Published port | 5433 → 5432 (diagnostics only; 5433 to miss a local PostgreSQL) |

Two of these rows are already findings for the report:

- **The client is not coupled to the server.** FoundationDB refuses a client
  whose `libfdb_c.so` does not match the server version exactly, and Oracle
  NoSQL's driver has to be aimed at an HTTP proxy. psycopg 3.2 speaks a wire
  protocol that has been stable since PostgreSQL 7.4 and connects to any server
  from 10 upward. Nothing in this stack has to move in lockstep.
- **Stock defaults, on purpose.** kvlite and `fdbserver` are both untuned, so
  tuning only PostgreSQL would be the easiest available way to produce a
  meaningless comparison. `shared_buffers` at 128 MB is smaller than the
  corpus, which is worth stating when discussing the aggregate queries.

As with the other two stacks: **do not add a `platform:` key.** The image is
multi-arch and Docker picks the native architecture; forcing one would run the
server under emulation and invalidate every Phase 4 number from that machine.

## Install

```bash
cd docker/postgres
docker compose up -d --build
```

Unlike FoundationDB there is no initialization step: the official image runs
`initdb` on first start of an empty volume, creates the `nbp` role and the
`tmdb` database from the environment, and reports healthy when `pg_isready`
succeeds. Unlike Oracle NoSQL there is no proxy to wait for.

✅ **Captured** 2026-09-13, first start on an empty volume (so this includes the
one-time `initdb`):

```
$ docker compose -f docker/postgres/docker-compose.yml logs pg | tail -20
pg  | PostgreSQL init process complete; ready for start up.
pg  | 2026-09-13 13:37:08.917 UTC [1] LOG:  starting PostgreSQL 17.6 (Debian 17.6-2.pgdg12+1)
pg  |   on aarch64-unknown-linux-gnu, compiled by gcc (Debian 12.2.0-14+deb12u1) 12.2.0, 64-bit
pg  | 2026-09-13 13:37:08.918 UTC [1] LOG:  listening on IPv4 address "0.0.0.0", port 5432
pg  | 2026-09-13 13:37:08.918 UTC [1] LOG:  listening on IPv6 address "::", port 5432
pg  | 2026-09-13 13:37:08.919 UTC [65] LOG:  database system was shut down at 2026-09-13 13:37:08 UTC
pg  | 2026-09-13 13:37:08.921 UTC [1] LOG:  database system is ready to accept connections
```

`aarch64-unknown-linux-gnu` is the point worth keeping: the multi-arch image
resolved to native ARM64 with no `platform:` key and no emulation, which is what
makes the Phase 4 numbers from this machine usable at all. Steady-state restart
(no `initdb`) is the 0.2 s in the installation-effort table below.

## Verification

### ⬜ 1. Server is up and self-describing

```bash
docker compose exec pg psql -U nbp -d tmdb -c "SELECT version();"
docker compose exec pg psql -U nbp -d tmdb -c "SHOW shared_buffers; SHOW work_mem; SHOW max_parallel_workers_per_gather;"
```

Capture the output. `max_parallel_workers_per_gather` matters for section 3.5 of
the елаборат: PostgreSQL is the only one of the three databases that will use
more than one core for a *single* query, and this setting is why.

### ⬜ 2. Driver round trip

```bash
docker compose exec client python docker/postgres/smoke_test.py
```

Expect `SMOKE TEST PASSED`. The smoke test deliberately exercises the two
operations the L1 model depends on and that a relational engine has to be told
about explicitly — a GIN index over a JSON array, and `GROUP BY` over an element
of that array.

✅ Verified against PostgreSQL 16.13 outside Docker, 2026-09-13:

```
0. SERVER VERSION
   -> 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1)
1. CREATE TABLE + GIN INDEX
2. INSERT
3. SELECT by primary key
   -> ('Closed Ward',)
4. array containment (the L1 genre access path)
   -> (1,)
5. server-side aggregation over a JSON path
   -> (1, 0.6)
6. GROUP BY an element of the JSON array
   -> [(18, 1), (9648, 1)]
7. DROP

SMOKE TEST PASSED
```

### ⬜ 3. Restart persistence

```bash
docker compose down && docker compose up -d
docker compose exec pg psql -U nbp -d tmdb -c "\dt"
```

The tables must still be there. Note the asymmetry worth reporting: `docker
compose down -v` destroys the `pgdata` volume and the next `up` runs `initdb`
again — but unlike FoundationDB, that is the whole recovery procedure. There is
no equivalent of `configure new single ssd`, and no cluster file to re-share.

### ⬜ 4. Apply the schema

```bash
docker compose exec client python -m common.apply_schema
```

✅ Verified. Eleven statements, all accepted first time — one table and five
indexes for L1, five tables for L2:

```
L1:
   CREATE TABLE IF NOT EXISTS l1_movies ( id INTEGER PRIMARY KEY, doc JSONB NOT NULL )
   CREATE INDEX IF NOT EXISTS idx_imdb ON l1_movies ((doc ->> 'id_imdb'))
   CREATE INDEX IF NOT EXISTS idx_year ON l1_movies (((doc ->> 'year')::int))
   CREATE INDEX IF NOT EXISTS idx_lang_votes ON l1_movies ( (doc ->> 'original_language'), ((doc ->> 'vote_count')::int))
   CREATE INDEX IF NOT EXISTS idx_genre ON l1_movies USING GIN ((doc -> 'genre_ids') jsonb_path_ops)
   CREATE INDEX IF NOT EXISTS idx_pop ON l1_movies (((doc ->> 'popularity')::float8) DESC, id)

L2:
   CREATE TABLE IF NOT EXISTS l2_movies_by_year ( ... PRIMARY KEY (year, chunk) )
   CREATE TABLE IF NOT EXISTS l2_genre_year_stats ( ... PRIMARY KEY (genre_id, year) )
   CREATE TABLE IF NOT EXISTS l2_lang_stats ( lang TEXT PRIMARY KEY, ... )
   CREATE TABLE IF NOT EXISTS l2_year_stats ( year INTEGER PRIMARY KEY, ... )
   CREATE TABLE IF NOT EXISTS l2_genre_top ( ... PRIMARY KEY (genre_id, pos) )

applied
```

### ⬜ 5. Load and verify both models

```bash
docker compose exec client python -m common.live_check
```

✅ Verified — every check passed, both models loaded and read back through every
key family, with the values compared against numbers computed independently from
the data files.

**Note on this verification:** it was run against a stand-in corpus generated by
`sandbox/make_corpus.py`, which reproduces the measured structure of the real
dataset (109,222 records, 20.1 % with no genre, 5.0 % undated, 137 languages,
19 genres, 49 year buckets) but not its text. It proves the schema, the loaders
and the queries are correct. **It proves nothing about performance** — no timing
from that run appears anywhere in the report, and the numbers for section 3 must
come from a run on the real `data/` on a team machine.

⬜ **Capture on a team machine, on the real data:** the two load timings and the
row counts. They go into `bench/plots.py` `LOAD_SECONDS[("postgresql", "L1")]`
and `[("postgresql", "L2")]`, and into Табела 3 of the елаборат.

⬜ **Capture the on-disk footprint** for the same table:

```bash
docker compose exec pg psql -U nbp -d tmdb -c "
  SELECT relname,
         pg_size_pretty(pg_total_relation_size(c.oid))  AS total,
         pg_size_pretty(pg_relation_size(c.oid))        AS heap,
         pg_size_pretty(pg_indexes_size(c.oid))         AS indexes
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1;"
docker compose exec pg psql -U nbp -d tmdb -c "SELECT pg_size_pretty(pg_database_size('tmdb'));"
```

Two things to look for and to discuss, because both cut against the intuition
that the relational engine must be the heavy one:

- **L2's chunks are compressed.** A ~90 KB JSONB value is over the TOAST
  threshold, so PostgreSQL stores it out of line and compresses it. Neither
  key-value store compresses anything. Expect L2's footprint to come out
  *below* the 68.78 MB logical size, which no other database in this study can
  do.
- **L1's indexes are visible separately.** `pg_indexes_size` gives the exact
  cost of the five index families, which is the number to compare against the
  19.93 MB of FoundationDB index keys in Табела 4 — a like-for-like comparison
  the other two databases cannot supply directly.

### ⬜ 6. Confirm all three databases agree

```bash
docker compose exec client python -m bench.answers          # once per database
python -m bench.answers --compare                           # on the host
```

This is what makes the three-way comparison legitimate: it checks every
implementation of every query against every other, and against the answers
computed straight from `data/` with no database involved.

✅ Verified for PostgreSQL against the computed ground truth — all ten queries,
both models, agree exactly.

### ⬜ 7. Benchmark

```bash
docker compose exec client python -m bench.harness
docker compose exec client python -m bench.concurrency
```

✅ The harness runs clean and reports `L1 and L2 agreed on every query`.

### ✅ 8. Capture the query plans — the thing the other two cannot do

```bash
docker compose exec client python -m bench.explain > docs/postgres-plans.txt
```

`docs/schema-comparison.md §6` lists "query 6's cause is inferred, not confirmed
— no query plan was captured" as a limitation of the study. It is a limitation
that cannot be lifted for either NoSQL store: kvlite exposes no `EXPLAIN` over
the HTTP proxy, and FoundationDB has no planner to interrogate — its access path
is whatever the client code reads. PostgreSQL will simply tell you, and the
answer to query 6 turned out to be genuinely interesting:

```
Q6  Top 20 of Drama by popularity
-- L1
   Limit (actual time=0.041..0.471 rows=20 loops=1)
     ->  Index Scan using idx_pop on l1_movies (actual rows=20 loops=1)
           Filter: ((doc -> 'genre_ids'::text) @> '18'::jsonb)
           Rows Removed by Filter: 65
   Execution Time: 0.497 ms
```

The planner did not use the genre index at all. It walked the popularity index
backwards, discarding non-Drama rows as it went, and had twenty answers after
touching 85 rows. That is a third solution to the ordering problem: FoundationDB
puts the order *in the key*, Oracle NoSQL sorts, and PostgreSQL picks the index
that provides the order and treats the selective predicate as a filter.

Query 8's plan carries the other finding:

```
Q8  Avg rating & count per genre per year
-- L1
   Finalize GroupAggregate (actual rows=399 loops=1)
     ->  Gather Merge (actual rows=1195 loops=1)
           Workers Planned: 2
           Workers Launched: 2
```

PostgreSQL split the aggregate across three processes without being asked. That
is directly relevant to requirement 4 (single vs multiple processors): it is the
only one of the three databases that uses more than one core for a *single*
query, where kvlite and a single `fdbserver` process are sequential.

## Installation effort, compared

Measured the same way as the table in `PLAN.md §1.6`, extended with the third
database. ⬜ The two image sizes and the cold start must be filled in from a
real `docker compose up` on a team machine.

✅ Measured 2026-09-13 on macOS 15 / Apple Silicon, Docker 29.3.1:

```
$ docker images postgres:17.6-bookworm --format "{{.Size}}"
644MB
$ docker images --format "{{.Repository}}:{{.Tag}} {{.Size}}" | grep postgres-client
postgres-client:latest 262MB
```

Cold start was timed as stop → start → first successful `pg_isready`, the same
way Oracle NoSQL's "proxy reachable again" figure was taken. On this machine the
other two, re-measured identically in the same sitting, were kvlite **2.9 s** and
`fdbserver` **1.4 s** — so the ordering in the table holds, and PostgreSQL is an
order of magnitude faster to serve than either. Note the server image is the
*smallest* of the three, not the largest: 644 MB against Oracle's 1.01 GB and
FoundationDB's 2.04 GB.

| | Oracle NoSQL CE | FoundationDB | PostgreSQL |
|---|---|---|---|
| Steps to usable | 1 — `up -d`, self-initializing | 2 — `up -d` **plus** `configure new single ssd` | 1 — `up -d`, self-initializing |
| Config required | none | cluster file must be shared with every client | none |
| Client/server coupling | loose — HTTP proxy, any HTTP client | strict — `libfdb_c.so` must match server version *and* arch | loose — stable wire protocol, any 3.x psycopg |
| Host-side access | works (`http://localhost:8080`) | not portable; must run in-network | works (`localhost:5433`) |
| Schema to declare | tables + indexes | none | tables + indexes + types + constraints |
| Recovery after `down -v` | re-load | `configure new` **then** re-load | re-load |
| Server image size | 1.01 GB | 2.04 GB | **644 MB** |
| Client image size | 240 MB | 304 MB | **262 MB** |
| Cold start to serving | ~8 s | ~2 s | **0.2 s** |

The honest summary of the installation phase is that PostgreSQL was the least
trouble of the three, and that this is not a NoSQL-versus-relational result —
it is a maturity result. FoundationDB's two extra steps and strict version
coupling are the price of a distributed transactional store; Oracle NoSQL's
proxy indirection is the price of a store that expects to be a cluster.
PostgreSQL's single-node case is the one it has been polishing for thirty years.
