# Project Plan — Key-Value NoSQL Databases (Тема 1)

**Course:** Неструктурирани бази на податоци, 2025/2026
**Topic:** Key-value бази на податоци
**Databases:** Oracle NoSQL Database CE (sub-team A) · FoundationDB (sub-team B) · **PostgreSQL (joint — the relational control)**
**Dataset:** TMDB Movies 2000–2020 with IMDb ID

> **Scope extension, 2026-09-13 — a third database.** The assignment asks for two
> key-value stores. We extended the study with **PostgreSQL as a relational
> control** — not as a third key-value contender — because we judged that a
> comparison of two NoSQL stores against each other cannot answer the question the
> topic actually raises. It stores the same 109,222 records in the same two models
> (L1 and L2), answers the same ten queries with the same semantics, and is
> measured by the same harness.
>
> The question it exists to answer is the one the topic implies but two NoSQL
> stores alone cannot test: **does a key-value store actually buy anything on
> unstructured data, or would a relational engine have done as well?** A
> comparison of Oracle NoSQL against FoundationDB can only say which of the two
> is faster. With a baseline it can say whether the choice mattered at all.
>
> **Scope extension, 2026-09-15 — model R, the structured arm.** The control above
> was first built *without* a normalized relational schema, on the reasoning that a
> third data model would answer a different question. That reasoning was right that
> it is a different question and wrong that the question should be left unasked:
> without it the study can say which engine is faster on a key-value model, but
> nothing about whether a key-value model was the right shape for this data in the
> first place.
>
> So PostgreSQL now also carries **model R** — third normal form, typed columns, a
> `movie_genres` junction table, no JSON at all (`POSTGRES_R_DDL` in
> `common/keyspec.py`, `PostgresRelationalBackend` in `bench/queries.py`). Same ten
> queries, same semantics, same harness, and checked by the same
> `python -m bench.answers --compare` as everything else. It was measured on
> 2026-09-15, in its own sitting — say so wherever an R number stands beside a
> key-value one.
>
> **The thesis is therefore a two-axis comparison: two aggregation models over
> unstructured data in two key-value stores, set against structured data in a
> relational one.** Every result belongs to one axis or the other:
>
> - *Does the aggregation model matter?* L1 against L2, inside one database.
> - *Does the storage paradigm matter?* Unstructured key-value against structured
>   relational, on identical data and identical query semantics.
>
> One dataset, three aggregation levels — L1 at 730,414 keys, L2 at 1,867 keys, R at
> 258,862 rows across four tables — and three engines.
>
> Adding the control meant **re-measuring all three databases on one machine in
> one sitting** (macOS 15 / Apple Silicon, 2026-09-13). The earlier Oracle NoSQL
> and FoundationDB figures came from two other machines, and a table that mixes
> machines cannot be read — so every performance number in this document is from
> that single run. Three conclusions changed as a result; they are marked where
> they occur in [docs/schema-comparison.md](docs/schema-comparison.md) §5.

---

## 0. Verified starting facts

These were checked against the actual data and the actual container registries, not assumed.

### 0.1 Dataset profile

| Property | Value |
|---|---|
| Files | 21 (`data/tmdb-movies-2000.json` … `2020.json`) |
| Format | **JSONL** (newline-delimited JSON), *not* a JSON array — one movie object per line |
| Total records | **109,222** |
| Unique `id` | 109,222 (no duplicates across files — safe as primary key) |
| `id_imdb` present | 109,222 (100% — usable as an alternate key) |
| Total size | **70.85 MB** in `data/` (`du -sb` = 70,847,803 B). Budget ~142 MB of disk: kagglehub keeps an identical copy in `~/.cache/kagglehub` |
| Schema | Fully flat, 15 fields, no missing keys anywhere |

Record shape:

```json
{
  "id": 582585,                    // TMDB id — PRIMARY KEY
  "id_imdb": "tt0263470",          // IMDb id — ALTERNATE KEY
  "title": "Closed Ward",
  "original_title": "いのちの海",
  "original_language": "ja",
  "genre_ids": [18, 9648],         // only nested field: array of ints
  "release_date": "2001-04-07",
  "overview": "",
  "popularity": 0.6,
  "vote_average": 0.0,
  "vote_count": 0,
  "adult": false,
  "video": false,
  "poster_path": "/3zaXzs....jpg",
  "backdrop_path": null
}
```

**19 distinct genre ids** are used: 12, 14, 16, 18, 27, 28, 35, 36, 37, 53, 80, 99, 878, 9648, 10402, 10749, 10751, 10752, 10770.
**Top languages:** en (58,015), es (6,455), fr (5,818), ja (4,027), de (3,973), it, zh, pt, hi, ko.

**Data-quality findings to document in the report** — measured directly from the 21 data files. These are exactly the kind of observations section 5 of the елаборат wants, and they are worth re-deriving yourselves as the first real task:

| Finding | Measured | Consequence |
|---|---|---|
| Movies with **no genre at all** | 21,934 (**20.1 %**) | every genre-based query silently reaches only ~80 % of the corpus |
| `vote_count == 0` | 41,071 (**37.6 %**) | "top rated" is meaningless without a vote floor → `MIN_VOTE_COUNT = 50` |
| Empty `release_date` | 5,442 (**5.0 %**) | no derivable year; excluded from the year index, bucketed under year `0` in L2 |
| Actual year range | **1926–2021**, 48 distinct years | the dataset is *not* confined to 2000–2020; 130 records fall outside it |
| Distinct languages | 137 | long tail — only the top 10 are worth reporting |

- Files are grouped by *fetch* year, not by `release_date` — the 2000 file contains a movie released 2001-04-07. Always derive the year from `release_date`.
- Encoding: the raw files serialize non-ASCII as `\uXXXX` escapes. Re-encoding as UTF-8 (`ensure_ascii=False`) saves far less than first estimated — **70.74 MB → 69.87 MB, a 1.2 % saving**, because only a small share of the corpus is non-ASCII. (An earlier revision of this plan claimed 70.6 → 63.5 MB; that figure does not reproduce and every number derived from it has been recomputed.) The effect is real per *record* — the largest single record does drop from 5,264 to **2,021 bytes** — just not corpus-wide. Both databases store the UTF-8 form, so sizes are comparable either way.
- After normalization (JSON `null` dropped, `year` added, keys sorted) the stored corpus is **68.67 MB**. This is the number the Phase 4 on-disk footprints are compared against. All of these are reproduced by `python -m common.verify_schemas`.

### 0.2 Container images — the cross-platform question is solved

Both images are **multi-arch (linux/amd64 + linux/arm64)**. Confirmed by reading the registry manifest lists directly:

| Image | Tag | amd64 | arm64 |
|---|---|---|---|
| `ghcr.io/oracle/nosql` | `latest-ce` (= `2025-12-ce`, same digest) | ✅ | ✅ |
| `foundationdb/foundationdb` | `7.3.79` | ✅ | ✅ |
| `postgres` | `17.6-bookworm` | ✅ | ✅ |

**Consequence: no `--platform linux/amd64`, no Rosetta, no QEMU emulation.** Apple Silicon teammates and x64 teammates run byte-for-byte the same compose file and both get native performance. This matters enormously for Phase 4 — emulated numbers would be worthless for a performance comparison.

> **Rule for the team:** never add a `platform:` key to the compose files. Docker picks the native arch automatically. If someone adds it, benchmark results from that machine must be discarded.

### 0.3 Image internals (read from the image configs)

**Oracle NoSQL CE `latest-ce`** — KV **25.3.21**, GraalVM Community Java 17, entry `bash -c ./start-kvlite.sh` (kvlite = single-node dev store), volume `/kvroot`. Built-in env:

| Var | Default | Meaning |
|---|---|---|
| `KV_PORT` | 5000 | store service port |
| `KV_PROXY_PORT` | 8080 | **HTTP proxy** — this is what the drivers talk to |
| `KV_ADMIN_PORT` | 5999 | admin |
| `KV_HARANGE` | 5010-5020 | HA replication range |
| `KV_SERVICERANGE` | 5021-5049 | internal services |

**FoundationDB `7.3.79`** — entrypoint `tini -g -- /var/fdb/scripts/fdb.bash`, volume `/var/fdb/data`. Built-in env:

| Var | Default | Meaning |
|---|---|---|
| `FDB_PORT` | 4500 | fdbserver port |
| `FDB_CLUSTER_FILE` | `/var/fdb/fdb.cluster` | cluster descriptor |
| `FDB_NETWORKING_MODE` | `container` | what IP the process advertises |
| `FDB_COORDINATOR` | *(empty)* | coordinator hostname |
| `FDB_PROCESS_CLASS` | `unset` | storage / stateless / log |

### 0.4 Client version pinning

| Client | Version | Note |
|---|---|---|
| `borneo` (Oracle NoSQL Python SDK) | `5.4.3` | talks HTTP to the proxy on 8080 |
| `foundationdb` (Python binding) | `7.3.79` | **must exactly match the server image tag** |
| `psycopg[binary]` (PostgreSQL) | `3.2.3` | **not** coupled to the server version |

FoundationDB is strict about client/server version matching. Pin `foundationdb/foundationdb:7.3.79` and `foundationdb==7.3.79` and bump them together or not at all.

The contrast is worth a sentence in the report: the same table has one client that must match its server to the patch release, one that is aimed at an HTTP proxy, and one that speaks a wire protocol unchanged since PostgreSQL 7.4 and will connect to any server from 10 upward. Version coupling is a real operational cost and it is not distributed evenly.

---

## Phase 1 — ИНСТАЛАЦИЈА ✅ DONE

Goal: every teammate, on any machine, runs `docker compose up -d` in two directories and has both databases reachable, with a documented verification checklist. Everything in this phase goes verbatim into section 4 (Методологија) of the елаборат.

> **Status: complete and verified** on macOS / Apple Silicon (aarch64), Docker 29.6.1, 8 CPU, 7.75 GB RAM, 2026-08-09. Both stacks build, start, pass a driver round trip, and survive restart. Full captured output, including the two problems hit and how they were solved, is in the installation logs:
> - [docker/oracle-nosql/README.md](docker/oracle-nosql/README.md)
> - [docker/foundationdb/README.md](docker/foundationdb/README.md)
>
> Still to confirm: one run on an **x64** machine. Nothing in either stack is arch-specific, so this is expected to be a formality — but do it and record the output before writing section 4.

### 1.1 Prerequisites (identical on macOS and Windows/Linux x64)

- Docker Desktop ≥ 4.30 (or Docker Engine + compose plugin on Linux).
- Docker Desktop resources: **≥ 6 GB RAM, ≥ 4 CPUs**. Both JVM-based kvlite and a multi-process FDB cluster need headroom, and Phase 4 pins CPU counts explicitly.
- Python 3.11 on the host for orchestration scripts, matching the client containers. ⚠️ On the Fedora x64 machine `.venv` is currently **3.14.6**, not 3.11 — harmless for `download.py` and `common/verify_schemas.py`, which are pure stdlib, but recreate it with `uv venv --python 3.11 .venv` before running anything whose timings feed the report.
- Git, ~2 GB free disk.
- Run `python download.py` once to populate `data/` (it is gitignored — each member downloads it themselves).

### 1.2 Target repository layout

```
baze/
├── data/                          # 109,222 records, gitignored
├── download.py
├── docker/
│   ├── oracle-nosql/
│   │   ├── docker-compose.yml
│   │   ├── Dockerfile.client
│   │   └── README.md              # install log → elaborate
│   ├── foundationdb/
│   │   ├── docker-compose.yml
│   │   ├── Dockerfile.client
│   │   └── README.md              # install log → elaborate
│   └── postgres/                  # ✅ the relational control
│       ├── docker-compose.yml
│       ├── Dockerfile.client
│       ├── smoke_test.py
│       └── README.md              # install log → elaborate
├── common/
│   ├── dataset.py                 # ✅ JSONL reader, normalization, genre map
│   ├── keyspec.py                 # ✅ all three schemas: FDB keys + Oracle + PostgreSQL DDL
│   ├── schema.md                  # ✅ L1/L2 design, measurements, access paths
│   ├── verify_schemas.py          # ✅ offline check of both schemas vs the data
│   ├── apply_schema.py            # ✅ create/drop either schema in any of the three DBs
│   ├── live_check.py              # ✅ load + verify every key family, per database
│   └── queries.md                 # ⬜ the 10 query definitions, DB-agnostic
├── oracle/                        # sub-team A
│   ├── load_l1.py  load_l2.py  queries.py
├── fdb/                           # sub-team B
│   ├── load_l1.py  load_l2.py  queries.py
├── bench/
│   ├── queries.py                 # ✅ 10 queries × L1/L2 × 3 databases + ground truth
│   ├── harness.py                 # ✅ correctness gate + latency sweep → results/
│   ├── answers.py                 # ✅ cross-database answer equality → results/answers-*.json
│   ├── explain.py                 # ✅ EXPLAIN ANALYZE for every PostgreSQL query
│   ├── report.py                  # ✅ merges results/*.csv into the doc tables
│   ├── results/                   # ✅ oracle-nosql.csv, foundationdb.csv, postgresql.csv
│   ├── plots.py                   # ✅ Слика 7–11 (латенција, конкурентност, CPU) → plots/
│   └── concurrency.py             # ✅ 1/4/16-client sweep (multiprocess) → results/
├── sandbox/
│   └── make_corpus.py             # ✅ stand-in corpus for code verification — never for timings
└── docs/
    ├── schema-comparison.md       # ✅ L1 vs L2 × 3 databases, measured — feeds sections 4-6
    ├── postgres-plans.txt         # ✅ captured query plans — evidence for §5.2
    └── elaborat.md  presentation/ # ⬜
```

### 1.3 Oracle NoSQL CE — `docker/oracle-nosql/docker-compose.yml`

```yaml
services:
  kvlite:
    image: ghcr.io/oracle/nosql:2025-12-ce   # pinned; latest-ce is the same digest today
    container_name: kvlite
    hostname: kvlite
    ports:
      - "5000:5000"    # KV_PORT   — store
      - "8080:8080"    # KV_PROXY_PORT — HTTP proxy, this is what borneo uses
      - "5999:5999"    # KV_ADMIN_PORT
    volumes:
      - kvroot:/kvroot
    networks: [kvnet]

  client:
    build:
      context: .
      dockerfile: Dockerfile.client
    depends_on: [kvlite]
    environment:
      NOSQL_ENDPOINT: http://kvlite:8080
    volumes:
      - ../../:/work
    working_dir: /work
    networks: [kvnet]
    command: sleep infinity

volumes:
  kvroot:
networks:
  kvnet:
```

`Dockerfile.client`:

```dockerfile
FROM python:3.11-slim
RUN pip install --no-cache-dir borneo==5.4.3 orjson tqdm
```

**Connection strategy — decided deliberately, document the reasoning:**

Connect **through the proxy on 8080 using `borneo`**, not directly to port 5000 with the Java `KVStore` driver. Reason: kvlite registers its service under the *container's* hostname. A driver running on the host resolves the registry, gets back `kvlite:5000`, and cannot route to it. The HTTP proxy has no such indirection — it is a plain request/response endpoint and works identically from the host and from a sibling container, on every OS. The `client` service also exists so that both teams benchmark from an identical containerized Python environment.

Endpoint from the host: `http://localhost:8080`. From the client container: `http://kvlite:8080`.

**Verification checklist (record the output of each in the install log):**

1. `docker compose up -d && docker compose logs -f kvlite` — wait for kvlite + proxy startup lines.
2. `docker exec kvlite bash -c 'java -jar /app/kv-*/lib/kvstore.jar ping -host localhost -port 5000'` — expect the store and RN to report as `RUNNING`.
3. From the client container: open a `NoSQLHandle` against `http://kvlite:8080`, issue `CREATE TABLE IF NOT EXISTS smoke (id INTEGER, PRIMARY KEY(id))`, put one row, get it back, drop the table.
4. `docker compose down && docker compose up -d` → confirm data survives in the `kvroot` volume.

✅ All four verified; output captured in [docker/oracle-nosql/README.md](docker/oracle-nosql/README.md). Store reports `Edition: Community`, version 25.3.21, 10 partitions, RN + Admin `RUNNING`. Proxy is reachable again ~8 s after a restart.

**One correction to this design during install:** the healthcheck originally probed `http://localhost:8080/ping`, which fails permanently — the proxy returns **400** for `/` and `/ping` and only answers **200** on `/V2/nosql/data`. The committed compose file uses the working probe and kvlite now reports `healthy`.

### 1.4 FoundationDB — `docker/foundationdb/docker-compose.yml`

```yaml
services:
  fdb:
    image: foundationdb/foundationdb:7.3.79
    container_name: fdb
    hostname: fdb
    environment:
      FDB_NETWORKING_MODE: container
      FDB_COORDINATOR: fdb
    ports:
      - "4500:4500"
    volumes:
      - fdbdata:/var/fdb/data
    networks: [fdbnet]

  client:
    build:
      context: .
      dockerfile: Dockerfile.client
    depends_on: [fdb]
    environment:
      FDB_CLUSTER_FILE: /etc/foundationdb/fdb.cluster
    volumes:
      - ../../:/work
      - fdbconf:/etc/foundationdb
    working_dir: /work
    networks: [fdbnet]
    command: sleep infinity

volumes:
  fdbdata:
  fdbconf:
networks:
  fdbnet:
```

`Dockerfile.client` (multi-stage — lifts `libfdb_c.so` out of the official image so the arch always matches):

```dockerfile
FROM foundationdb/foundationdb:7.3.79 AS fdbsrc

FROM python:3.11-slim
COPY --from=fdbsrc /usr/lib/libfdb_c.so /usr/lib/libfdb_c.so
COPY --from=fdbsrc /usr/bin/fdbcli /usr/bin/fdbcli
RUN pip install --no-cache-dir foundationdb==7.3.79 orjson tqdm
```

**Two mandatory first-run steps** — FDB does *not* self-initialize:

```bash
docker compose up -d
# 1. create the database (once per volume; without this, status says "unavailable")
docker exec fdb fdbcli --exec "configure new single ssd"
# 2. make the cluster file reachable by the client container
docker exec fdb cat /var/fdb/fdb.cluster
# copy it into the shared fdbconf volume as /etc/foundationdb/fdb.cluster
```

**Networking — the macOS-critical decision:** FDB's cluster file contains the address the server advertises. With `FDB_NETWORKING_MODE=container` that is the container's internal IP, so a client on the *host* connecting to `localhost:4500` will fail even though the port is published. The usual Linux workaround is `FDB_NETWORKING_MODE=host` + `network_mode: host` — **that does not work on Docker Desktop for Mac**, where the daemon runs inside a VM.

Therefore: **all FoundationDB access happens from the `client` container on the same compose network.** This is not a workaround for the Mac users only — it is the single portable path, and it has the side benefit of making Phase 4 measurements comparable, since both databases are then driven from an equivalent containerized Python 3.11 client. Port 4500 stays published only for `fdbcli` inspection and diagnostics.

**Verification checklist:**

1. `docker exec fdb fdbcli --exec "status minimal"` → `The database is available.`
2. `docker exec fdb fdbcli --exec "status details"` → capture process count, class, storage engine for the report.
3. From the client container: `fdb.api_version(730)`, open the db, `db[b'smoke'] = b'ok'`, read it back, delete it.
4. Verify `libfdb_c.so` really landed at `/usr/lib/`. ✅ Confirmed — the multi-stage `COPY` of `/usr/lib/libfdb_c.so` and `/usr/bin/fdbcli` builds cleanly, and a missing source path would be a hard build error.
5. Restart test → confirm `fdbdata` volume persistence.

✅ All verified; output in [docker/foundationdb/README.md](docker/foundationdb/README.md). Storage engine `ssd-2`, 1 process, 1 coordinator. Data survived both `restart` and a full `down`/`up`, and the shared cluster file was regenerated automatically — **no second `configure new` required**.

**Note on `status`:** right after `configure new`, FDB reports *"available, but has issues"* with `(Re)initializing automatic data distribution`. That is transient init, not a fault. `Fault Tolerance - 0 machines` is also expected and correct for `single` redundancy.

### 1.5 PostgreSQL — `docker/postgres/docker-compose.yml`

The relational control. Full installation log, with the captured output and the
verification checklist, is in [docker/postgres/README.md](docker/postgres/README.md).

```yaml
services:
  pg:
    image: postgres:17.6-bookworm
    container_name: pg
    hostname: pg
    environment:
      POSTGRES_USER: nbp
      POSTGRES_PASSWORD: nbp
      POSTGRES_DB: tmdb
      PGDATA: /var/lib/postgresql/data/pgdata
    ports:
      - "5433:5432"   # diagnostics only; 5433 to miss a local PostgreSQL
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks: [pgnet]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U nbp -d tmdb"]

  client:
    build: { context: ., dockerfile: Dockerfile.client }
    container_name: pg-client
    depends_on:
      pg: { condition: service_healthy }
    environment:
      PG_DSN: postgresql://nbp:nbp@pg:5432/tmdb
    volumes:
      - ../../:/work
    working_dir: /work
    networks: [pgnet]
    command: sleep infinity
```

`Dockerfile.client`:

```dockerfile
FROM python:3.11-slim
RUN pip install --no-cache-dir "psycopg[binary]==3.2.3" orjson tqdm
```

**Three decisions, each of which is a finding rather than a detail:**

1. **Stock configuration.** `shared_buffers` stays at its 128 MB default, which
   is smaller than the 68 MB corpus plus its indexes — but kvlite and
   `fdbserver` are equally untuned, and tuning only the relational engine would
   be the single easiest way to produce a comparison that means nothing. State
   the setting; do not change it.
2. **Driven from the `client` container**, exactly like the other two, even
   though PostgreSQL — unlike FoundationDB — is perfectly reachable from the
   host. This is purely a fairness requirement: all three databases must be
   measured from an equivalent containerized Python 3.11 client, or the
   latencies partly measure the client.
3. **No `platform:` key**, same rule as the other two. The image is multi-arch.

**What installing it did *not* require**, which is the comparison §1.6's table
makes: no initialization command, no cluster file to distribute, no proxy to
wait on, and no version lockstep between client and server.

**Verification checklist:** in [docker/postgres/README.md](docker/postgres/README.md)
— server version and settings, `smoke_test.py` round trip, restart persistence,
schema application, load and verify, cross-database answer equality, benchmark,
and query-plan capture. Items already verified are marked ✅ there; items that
still need a real `docker compose up` on a team machine are marked ⬜ with the
exact command to run.

### 1.6 Phase 1 deliverables

- ✅ Both compose stacks committed and working on Apple Silicon. ⬜ Still needs one x64 run.
- ✅ `docker/*/README.md` = the literal installation log: commands run, output captured, every problem hit and how it was solved. This is what section 4 of the елаборат is graded on.
- ✅ Smoke tests committed (`docker/*/smoke_test.py`) so any teammate can prove their machine is set up correctly in one command.
- ✅ Comparison of installation effort, for the report:

| | Oracle NoSQL CE | FoundationDB | PostgreSQL |
|---|---|---|---|
| Steps to usable | 1 — `up -d`, self-initializing | 2 — `up -d` **plus** `configure new single ssd` | 1 — `up -d`, self-initializing |
| Config required | none | cluster file must be shared with every client | none |
| Client/server coupling | loose — HTTP proxy, any HTTP client | strict — `libfdb_c.so` must match server version *and* arch | loose — stable wire protocol, any psycopg 3.x |
| Host-side access | works (`http://localhost:8080`) | not portable; must run in-network | works (`localhost:5433`) |
| Schema to declare | tables + indexes | none | tables + indexes + types + constraints |
| Recovery after `down -v` | re-load | `configure new` **then** re-load | re-load |
| Server image size | 1.01 GB (JVM + GraalVM) | 2.04 GB | **644 MB** |
| Client image size | 240 MB | 304 MB | **262 MB** |
| Cold start to serving | ~8 s | ~2 s | **0.2 s** |

That contrast is a genuine finding, not filler: Oracle NoSQL trades a slow start for a zero-configuration, protocol-agnostic entry point; FoundationDB starts four times faster but pushes cluster topology and strict version management onto the client. Note the image sizes cut against the intuition that the JVM stack must be the heavier one — the FoundationDB image is twice the size.

PostgreSQL is the least trouble of the three, and the report should say why rather than letting that stand as a NoSQL-versus-relational result. It is a maturity result. FoundationDB's extra step and strict version coupling are the price of a distributed transactional store; Oracle NoSQL's proxy indirection is the price of a store that expects to become a cluster. Neither cost buys anything at the single-node scale this project runs at — which is precisely the point the control exists to expose, and it recurs in Phase 4.

---

## Phase 2 — ИМПОРТИРАЊЕ НА ПОДАТОЦИТЕ

The assignment explicitly asks for **two different aggregation levels**. All three implementations use the same two models so the Phase 4 comparison is apples-to-apples.

> **Status: both schemas are designed, written, and verified live on all three databases
> with the full 109,222-record corpus.** ✅ Every key family loads and reads back correctly,
> and the databases return identical answers to identical questions.
>
> The authoritative specification is **[common/schema.md](common/schema.md)** (design, DDL,
> access-path matrix, live results) and **`common/keyspec.py`** (the executable form). The
> sketch below is kept for context and has been reconciled with what was actually built;
> where the two differ, `keyspec.py` wins.
>
> ```bash
> python -m common.verify_schemas                      # offline, ~5 s, no DB needed
> docker exec kv-client  python -m common.apply_schema # DDL (FoundationDB needs none)
> docker exec kv-client  python -m common.live_check   # full load + verify, ~95 s
> docker exec fdb-client python -m common.live_check   # full load + verify, ~19 s
> docker exec pg-client  python -m common.apply_schema # 11 statements, all accepted
> docker exec pg-client  python -m common.live_check   # full load + verify
> ```
>
> Reference load timings, from those runs:
>
> | | Oracle NoSQL | FoundationDB | PostgreSQL |
> |---|---|---|---|
> | L1 | 109,222 rows, **56.8 s** (1,921 rows/s) | 730,414 keys, **6.8 s** (107,689 keys/s) | 109,222 rows, **2.9 s** (37,151 rows/s) |
> | L2 | 1,867 rows, **4.7 s** | 1,867 keys, **0.4 s** | 1,867 rows, **1.2 s** |
> | Store after both models | 217 MB (`/kvroot/kvstore`) | 141 MB accounted KV, 345 MB disk | 142 MB (`pg_database_size`) |
>
> Not like-for-like — Oracle does one HTTP put per row with server-side index maintenance,
> FoundationDB batches ~1,000 pairs per transaction and writes its own index keys. Phase 4
> makes it fair. Note both stores currently hold **L1 and L2 together**, so the footprints
> are not per-model; loading one model at a time is a Phase 4 prerequisite.
>
> The PostgreSQL loader batches `INSERT ... ON CONFLICT` at the *same* 1,000 rows per
> transaction FoundationDB uses, deliberately **not** `COPY`. `COPY` is PostgreSQL's real
> bulk path and is several times faster, but neither key-value store has a counterpart, so
> using it would turn the load comparison into a comparison of loading strategies. The
> upsert also makes the loader idempotent and restartable in exactly the sense the other
> two are. Worth measuring `COPY` separately and reporting it as an aside — "the relational
> engine also has a bulk path the others do not" is a real result, just not this one.
>
> Two things to expect from PostgreSQL's footprint, both of which cut against intuition:
> **L2 will be smaller than its 68.78 MB logical size**, because a ~90 KB JSONB chunk is
> over the TOAST threshold and gets compressed, which neither key-value store does; and
> **L1's index cost is directly readable** via `pg_indexes_size`, which is the like-for-like
> number to set against FoundationDB's 19.93 MB of index keys.
>
> Still open: production loaders (`oracle/load_l*.py`, `fdb/load_l*.py`) — restartable, with
> progress and per-model timing — and `common/queries.md`.

A shared `common/` module holds the key format constants and query definitions so neither team drifts — **written by hand, one piece at a time, once the team understands the shape of the data.** Not scaffolded up front. Written so far: `dataset.py`, `keyspec.py`, `schema.md`, `verify_schemas.py`, `apply_schema.py`.

Confirmed against the running databases — all of these are now observed, not assumed:
- Oracle NoSQL accepts an index on an array inside a JSON column (`doc.genre_ids[] AS INTEGER`), the same index with a trailing scalar (`..., doc.popularity AS DOUBLE`), and a composite primary key with a partition key (`PRIMARY KEY(SHARD(year), chunk)`). All 11 DDL statements applied first time.
- FoundationDB keys sort in order, so a negated scaled integer inside the key gives descending-popularity reads with no sorting — verified to return the identical top-20 that Oracle's `ORDER BY ... DESC LIMIT 20` returns.
- Array containment in Oracle NoSQL SQL is written **`t.doc.genre_ids[] =any 18`**; `live_check.py` probes the alternatives and prints whichever the running version accepts.
- Server-side `GROUP BY` works over a JSON path (`GROUP BY t.doc.original_language`, 137 groups). FoundationDB has no equivalent and must scan client-side — expect this to be the largest single gap in Phase 4.
- L2 chunks must be batched **by bytes, not by count**: at ~85 KB per chunk, the 1,000-pair batch that suits L1 would build an 85 MB transaction against FoundationDB's 10 MB limit. `keyspec.batched()` bounds by both, at 4 MB.
- A half-open key filter needs a genuinely two-sided range. `fdb.tuple.range(("idx","lang","en",501))` bounds the movies whose vote count is *exactly* 501 and returns 1 row, not the 2,611 with more than 500. `keyspec.KeyRange` carries the separate start bound so this cannot be written by accident.

### Model L1 — fine-grained (one movie per key)

Pure key-value, one record per key:

| Purpose | Oracle NoSQL | FoundationDB | PostgreSQL |
|---|---|---|---|
| Primary | `l1_movies(id INTEGER, doc JSON, PRIMARY KEY(id))` | `("movie", id) → encode(record)` | `l1_movies(id INTEGER PRIMARY KEY, doc JSONB)` |
| By IMDb | `idx_imdb` on `doc.id_imdb` | `("idx","imdb", imdb_id) → pack((id,))` | B-tree on `(doc ->> 'id_imdb')` |
| By genre | `idx_genre` on `doc.genre_ids[]` | `("idx","genre", genre_id, id) → ø` | **GIN** on `(doc -> 'genre_ids') jsonb_path_ops` |
| By genre, ranked | `idx_genre_pop` on `(doc.genre_ids[], doc.popularity)` | `("idx","genre_pop", genre_id, −pop×1000, id) → ø` | **not expressible** — B-tree on `popularity DESC, id` instead |
| By language | `idx_lang_votes` on `(doc.original_language, doc.vote_count)` | `("idx","lang", lang, vote_count, id) → ø` | B-tree on `(lang, vote_count)` |
| By year | `idx_year` on derived `year` | `("idx","year", year, id) → ø` | B-tree on `((doc ->> 'year')::int)` |

**The one key family PostgreSQL cannot express is the interesting row.** A B-tree
cannot be built over "each element of this JSON array, paired with this scalar":
that needs one index entry per (movie, genre) pair, and an expression index
produces exactly one entry per row. GIN indexes the array but stores no order.
So the model's fifth family is split in two — GIN for membership, a plain
popularity B-tree for order — and query 6 has to be served by one or the other.

What the planner actually did with that is the best single result the control
produced, and it was captured rather than guessed (`bench/explain.py`):

```
Limit (actual rows=20)
  ->  Index Scan using idx_pop on l1_movies (actual rows=20)
        Filter: ((doc -> 'genre_ids') @> '18'::jsonb)
        Rows Removed by Filter: 65
```

It ignored the genre index, walked the popularity index backwards, and had
twenty answers after touching 85 rows. That is a **third** solution to the
ordering problem: FoundationDB puts the order inside the key, Oracle NoSQL sorts
after the fact, and PostgreSQL picks the index that supplies the order and
demotes the selective predicate to a filter. It only works because Drama is a
third of the corpus (36,588 of 109,222, **33.5 %** — measured) — for a rare genre the
same plan would walk a long way — and
that caveat belongs in the report next to the number.

**Changed from the original sketch:** the language index is composite, `(lang, vote_count, id)` rather than `(lang, id)`. Query 4 (language X with `vote_count > 500`) then starts its range read at `("idx","lang", X, 501)` instead of scanning the language and filtering client-side — measured live, **2,611 entries read instead of 58,015** for English. The entry count is unchanged. Table names gained `l1_`/`l2_` prefixes so every SQL snippet in the елаборат says which model it belongs to.

Exact key population (counted, not estimated): 109,222 movie records + 621,192 index entries = **730,414 keys**.

| Key family | Entries |
|---|---|
| `movie` | 109,222 |
| `idx:imdb` | 109,222 |
| `idx:lang` | 109,222 |
| `idx:year` | 103,780 *(5,442 skipped — no release_date)* |
| `idx:genre` | 149,484 |
| `idx:genre_pop` | 149,484 |

### Model L2 — coarse-grained (aggregate per year / per genre-year)

PostgreSQL gets the same five tables as Oracle NoSQL, with the composite primary
keys carrying the same column order — `PRIMARY KEY (year, chunk)` rather than
`PRIMARY KEY(SHARD(year), chunk)`, because a multi-column B-tree already puts one
year's rows next to each other, which is closer to FoundationDB's ordered
keyspace than a hashed shard component is. No secondary indexes, same rule.

One asymmetry to state rather than hide: **the ~90 KB chunking is a constraint
PostgreSQL does not have.** It exists because of FoundationDB's 100 KB value
limit; a PostgreSQL field may be 1 GB and TOAST would happily store a whole year
as one value. The chunking is kept identical anyway, because the three databases
must hold the same chunks for the latencies to compare — so PostgreSQL is paying
for a limit that is not its own, which is a point in its favour and should be
reported as one.

| Key | Oracle NoSQL table | Value | Entries |
|---|---|---|---|
| `("year", year, chunk)` | `l2_movies_by_year` | that year's movies as a JSON array, ≤ 90 KB | **806** |
| `("stats","genre_year", genre_id, year)` | `l2_genre_year_stats` | `{n_movies, sum_vote, avg_vote, sum_popularity, avg_popularity, sum_votes}` | 495 |
| `("stats","lang", lang)` | `l2_lang_stats` | `{n_movies, sum_popularity, avg_popularity}` | 137 |
| `("stats","year", year)` | `l2_year_stats` | `{n_movies, avg_popularity, n_rated, avg_vote_rated}` | 49 |
| `("top","genre", genre_id, pos)` | `l2_genre_top` | `encode(record)`, positions 0–19 | 380 |
| | | | **1,867** |

**Changed from the original sketch:** the last three families are new. With only chunks and genre-year stats, L2 could answer query 8 and essentially nothing else — six of the ten queries would have degraded to a full scan and the comparison would have been one-sided. The additions cost 566 keys and 0.33 MB and let L2 answer queries 6, 9 and 10 directly.

**Corrected numbers:** the largest year is 2017 at **4.95 MB / 7,871 movies** (49× over FDB's value limit), and the corpus packs into **806 chunks** across 49 buckets, not 744. The earlier figures were derived from the 63.5 MB corpus size that does not reproduce (see §0.1). Bucket sizes are very uneven — 27 of the 49 years fit in one chunk, 2017 needs 56. Largest chunk actually produced: 89,999 B against a 90,000 B target and a 100,000 B hard limit.

Deliberately **no secondary indexes on L2**. Adding one turns it back into L1 and erases the contrast the whole comparison rests on.

L2 is where the interesting limits show up, and both are worth a paragraph in the report:
- **FoundationDB:** value limit **100 KB**, key limit 10 KB, transaction limit **10 MB / 5 seconds**. A whole year cannot be one value — it must be split into ~90 KB chunks under `("year", 2014, chunk_no)`. Batch writes at ~500–1,000 KV pairs per transaction with retry on `transaction_too_old`. Verified headroom: largest L1 value 2,021 B, largest key 43 B — both models sit far inside the key and value limits.
- **Oracle NoSQL:** a large JSON column is accepted but request-size and read-unit limits apply; measure and document where it degrades.

### Loader design

✅ Shared skeleton written — `common/dataset.py` streams the JSONL line by line (never `json.load` a whole file — they are not JSON arrays), derives `year` from `release_date`, normalizes `null` → absent, forces `popularity`/`vote_average` to float (a typed JSON index over `doc.popularity AS DOUBLE` rejects a value serialized as `1` instead of `1.0`), and emits canonical bytes via `encode()`. `common/keyspec.py` turns a record into its keys. Each team writes `load_l1.py` / `load_l2.py` on top. Loaders must be **idempotent and restartable**, and must record wall-clock load time + resulting on-disk size — both feed Phase 4. `common/apply_schema.py --drop --yes` resets either model to a clean state.

Expected logical footprint, to compare the Phase 4 on-disk figures against and expose each engine's amplification:

| | L1 | L2 |
|---|---|---|
| Record / chunk values | 68.67 MB | 68.78 MB |
| Primary keys | 1.75 MB | 0.02 MB |
| Index entries | 19.93 MB (621,192) | — |
| Precomputed aggregates | — | 0.33 MB |
| **Total** | **90.35 MB** | **69.13 MB** |
| Keys written per movie | 6.7 | 0.017 |

L1 costs 31 % more space, almost all of it index keys, and writes ~390× more keys during load. That is the trade the two models exist to measure.

**Deliverable:** ER-style / key-space diagram of both models, the loader code, load timings, and a written analysis of why each model suits or fights each database. The key-space tables and the per-query access-path matrix in [common/schema.md](common/schema.md) are the written half of this; the diagram and the timings still need doing.

---

## Phase 3 — КОРИСТЕЊЕ НА ПОДАТОЦИТЕ

> **Status: all ten queries are implemented seven times** — L1 and L2 on each of the three
> databases, plus model R on PostgreSQL — in `bench/queries.py`, and all seven
> implementations of every query return the same answer. ⬜ `common/queries.md` (the DB-agnostic write-up, with captured output and
> per-query limitations) is still to write; the implementations and the measured results
> in [docs/schema-comparison.md](docs/schema-comparison.md) are the raw material for it.
>
> **How "the same answer" is now checked.** `bench/harness.py` refuses to time a query
> whose L1 and L2 results disagree, but that gate is local to one database — it cannot see
> that Oracle NoSQL and PostgreSQL returned different numbers. Across the three databases
> there are seven implementations per query, so `bench/answers.py` writes each database's
> canonical answers to `bench/results/answers-<db>.json` and compares every file against
> every other *and* against `DatasetBackend` — an eighth implementation that computes the answers
> straight from `data/` by brute force, with no database involved at all. That brute-force
> version is the only one in the project that cannot be wrong for an interesting reason,
> which is what makes it the reference.
>
> ```bash
> docker exec kv-client  python -m bench.answers
> docker exec fdb-client python -m bench.answers
> docker exec pg-client  python -m bench.answers
> python -m bench.answers --compare        # on the host
> ```

Ten queries, spanning the three categories the assignment requires (6–10 needed). Defined DB-agnostically in `common/queries.md`, implemented once per model per database.

**The implementations are deliberately not transliterations of each other.** Each database is asked the same question in the way that database is meant to be asked: SQL with `GROUP BY` where there is a planner, hand-built range reads where there is an ordered keyspace. Comparing deliberately hobbled implementations would measure nothing. The place this matters most is L2's fallback: when L2 has no access path, Oracle NoSQL and FoundationDB both ship all 806 chunks to the client and scan them in Python because neither can look inside a stored blob, while PostgreSQL unnests the chunk in the executor with `jsonb_array_elements` and sends back only the answer. That is a real difference in what the engines can do, not a thumb on the scale — but it must be stated wherever those numbers appear.

The access path each model offers for each of these ten is already worked out in [common/schema.md §5](common/schema.md) — queries 1, 2, 4, 5 favour L1 and queries 3, 6, 7, 8, 9, 10 favour L2. Query 7 is a known weak spot in L1 (it needs language *and* a year range, and L1 has no composite `(lang, year)` index); that is kept as a finding, with the index that would fix it written down for a Phase 4 tuning experiment. What `queries.md` still has to pin down is the exact parameters — which id, which year, which genre pair — so both sub-teams measure the identical thing.

**Simple (filters):**
1. Point lookup of a movie by TMDB `id`.
2. Lookup by `id_imdb` (alternate key — exercises the secondary index).
3. All movies of a given year (range/index scan).
4. Movies in language `X` with `vote_count > 500`.

**Complex (combining data from multiple instances):**
5. Movies belonging to **both** genre A and genre B — intersection of two index scans.
6. Given a genre, resolve index entries → fetch the movie records → return top 20 by popularity (index→entity "join", done client-side in FDB, via SQL in Oracle NoSQL).
7. Same-language movies released within ±1 year of a given movie, excluding it (multi-key composition).

**Very complex (aggregate reports):**
8. Average `vote_average` and count per genre per year, 2000–2020 (full-scan aggregation vs. L2 precomputed — run both, compare).
9. Top 10 languages by movie count, with mean popularity.
10. Yearly trend report: count, mean popularity, mean rating filtered to `vote_count ≥ 50`.

For each query, document: the exact command/code, the result, and **the limitations encountered**. Requirement 3 explicitly asks for the problems, so record honestly — e.g. FDB has no server-side aggregation at all (everything is a client-side scan) while Oracle NoSQL offers SQL `GROUP BY`; conversely FDB's ordered keyspace makes range queries trivial where Oracle NoSQL's hash partitioning does not.

The control adds two more honest entries to that list, one in each direction:

- **PostgreSQL can group by an element of a JSON array in one statement.** Query 8 is a single `GROUP BY` over `LATERAL jsonb_array_elements_text(doc -> 'genre_ids')`. Oracle NoSQL cannot, and issues 19 separate statements, one per genre; FoundationDB has no server-side aggregation at all. Its plan also shows `Workers Launched: 2` — it is the only one of the three that uses more than one core for a *single* query, which feeds requirement 4 directly.
- **PostgreSQL cannot index "array element paired with a scalar".** The L1 model's `idx_genre_pop` family has no relational expression, and the workaround (GIN for membership, a separate B-tree for order) is a genuine modelling defeat even though the planner happened to route around it well for this particular genre.

**Whether these classify as "full scan" is now declared, not timed.** The harness used to infer it from a first run slower than 250 ms, which is a property of the machine as much as of the schema — a fast host silently marks a genuine full scan as indexed, which is exactly what happened to PostgreSQL's queries 9 and 10 over L1. `bench/harness.py DEGRADES_TO_SCAN` writes the access-path analysis down per database instead, seeded with precisely the flags the original Oracle NoSQL and FoundationDB runs produced, so nothing already reported changed. The timing is kept as a cross-check that prints a note when it disagrees.

---

## Phase 4 — СПОРЕДБА НА ПЕРФОРМАНСИТЕ

> **Status: complete for all three databases.** The full sweep — latency, concurrency,
> and 1-vs-4 CPUs — was measured on **2026-09-13, all three databases on one machine in
> one sitting** (macOS 15 / Apple Silicon, Docker 29.3.1, 10 CPUs / 8.2 GB). Harness
> `bench/harness.py`, tables regenerated by `bench/report.py`, raw CSVs in
> `bench/results/`, charts in `bench/plots/`, and the written analysis is
> **[docs/schema-comparison.md](docs/schema-comparison.md)**.
>
> Headline: the between-model gap reaches **11,165×**, still far larger than the
> between-database gap for the same query and model — schema choice dominates database
> choice. **PostgreSQL was fastest on all twenty query/model combinations**, so at this
> scale the key-value stores bought nothing measurable; the three qualifications that
> keep this from being a general verdict are in §5.8.
>
> ⚠ **Re-measuring on one machine changed three conclusions**, all marked ⚠ in §5:
> Oracle's server-side `GROUP BY` **won** all three aggregates rather than losing two of
> three (the old result compared two different machines); query 6's cross-database gap is
> **135×**, not 1,177×; and query 3 is no longer a tie. Nothing here is a code change —
> it is what the one-machine rule exists to catch.
>
> The three engines use extra cores three different ways (§4.7): PostgreSQL parallelizes a
> *single* query (4.41× on Q8, `Workers Launched: 2` at stock settings), Oracle NoSQL gains
> only under concurrency (1.93×), and FoundationDB gains nothing at all (0.92×) because its
> scaling unit is processes, not cores.
>
> ⬜ Still to do: three repetitions per configuration, per-model footprints (load one model
> at a time), and the multi-process FDB cluster experiment (`configure double ssd`) — which
> §4.7's flat core-scaling result makes the most load-bearing gap remaining.

### Phase 4 runbook — the three-database sweep

Run the whole sequence on **one machine**, in one sitting, with nothing else
running. Numbers from different machines cannot be compared, and the study
already carries one such split (§3.7 of the елаборат); do not add a second.

```bash
# once per machine
python download.py
cd docker/oracle-nosql && docker compose up -d --build && cd ../..
cd docker/foundationdb && docker compose up -d --build && cd ../..
docker exec fdb fdbcli --exec "configure new single ssd"
cd docker/postgres && docker compose up -d --build && cd ../..

# schema + load + verify, per database
for c in kv-client fdb-client pg-client; do
  docker exec $c python -m common.apply_schema
  docker exec $c python -m common.live_check      # record the load timings
done

# correctness across all three, before any timing is believed
for c in kv-client fdb-client pg-client; do docker exec $c python -m bench.answers; done
python -m bench.answers --compare

# latency, concurrency, and the CPU sweep
for c in kv-client fdb-client pg-client; do
  docker exec $c python -m bench.harness
  docker exec $c python -m bench.concurrency
done
for n in 1 4; do
  docker update --cpus $n kvlite fdb pg
  for c in kv-client fdb-client pg-client; do
    docker exec $c python -m bench.harness     --tag cpus$n
    docker exec $c python -m bench.concurrency --tag cpus$n
  done
done
docker update --cpus 8 kvlite fdb pg          # or whatever the host has

# evidence only PostgreSQL can supply
docker exec pg-client python -m bench.explain > docs/postgres-plans.txt

# tables and figures
python -m bench.report            # paste into docs/schema-comparison.md §4
python -m bench.plots             # → bench/plots/fig7..fig11
```

Then fill in `bench/plots.py LOAD_SECONDS[("postgresql", "L1")]` and `[("postgresql", "L2")]`
from the `live_check` output, or Слика 7 leaves PostgreSQL out.

Shared harness `bench/harness.py`, run from each database's `client` container, writing CSV to `bench/results/`.

**Protocol:** discard warm-up runs; point queries × 1,000 iterations, complex × 200, aggregates × 30; report p50 / p95 / p99 and throughput; three repetitions per configuration; identical dataset, identical query semantics.

**Measure:** import time (L1 and L2), on-disk footprint, per-query latency distribution, aggregate query time L1 vs L2, and behaviour under 1 / 4 / 16 concurrent client threads.

**Single vs. multiple processors** (requirement 4) — portable across all three stacks:
- Constrain each DB service with `docker update --cpus 1` then `4`, re-run the suite. Same cgroup knob, all three databases, works identically on macOS and x64.
- Additionally for FoundationDB: scale from a single `fdbserver` process to a multi-process cluster (`configure double ssd` with separate storage/stateless/log processes) and re-measure — FDB is architecturally built for this and the delta is a strong result. Oracle NoSQL CE via kvlite is single-node by design; state that limitation explicitly rather than faking a cluster.
- **PostgreSQL is the one to watch here**, because it is the only one of the three whose *single* queries use more than one core: the captured plan for query 8 shows `Workers Launched: 2`. Oracle NoSQL gains from extra cores only under concurrency, FoundationDB gains nothing from cores at all (its scaling unit is processes), and PostgreSQL gains on both axes. Report `max_parallel_workers_per_gather` alongside the numbers, since that setting is what makes the difference and it is at its stock value.

**Output:** `bench/plots.py` → matplotlib bar/line charts (latency by query, latency by CPU count, import throughput) for section 5 of the елаборат.

---

## Team split & sequencing

Sub-team A → Oracle NoSQL CE. Sub-team B → FoundationDB. **PostgreSQL is joint**, like the harness — it is the control both sub-teams are measured against, so neither owns it. Shared/joint: dataset analysis, `common/` (key spec + query definitions + loader skeleton), benchmark harness, report, presentation.

| Step | Work | Owner |
|---|---|---|
| 1 | ✅ Compose stacks up + install logs (pending one x64 run) | Both, in parallel |
| 2 | ✅ Dataset analysis + `common/` key spec ([schema.md](common/schema.md)). ⬜ Query definitions (`common/queries.md`) still to write | Joint — must be agreed **before** loaders are written |
| 3 | L1 loader + verification | Each sub-team |
| 4 | L2 loader + chunking | Each sub-team |
| 5 | ✅ 10 queries implemented, L1 and L2, all three databases (`bench/queries.py`) | Each sub-team + joint for PostgreSQL |
| 6 | ✅ Benchmark harness + single-threaded sweep on the two NoSQL stores. ⬜ 1 vs 4 CPU, concurrency, 3 repetitions, **and the whole PostgreSQL sweep** | Joint harness, separate runs |
| 7 | Charts + елаборат + 10-min presentation | Joint |

**Sync point:** step 2 must be finished and agreed by both sub-teams before step 3 starts. If the two teams pick different key formats or different query semantics, Phase 4 produces numbers that cannot be compared and the main deliverable is lost.

## Mapping to the required elaborate structure

| Елаборат section | Fed by |
|---|---|
| 3. Вовед | Key-value model overview, why Oracle NoSQL CE vs FoundationDB, and why a relational control at all |
| 4. Методологија | Phase 1 install logs (all three), [common/schema.md](common/schema.md) §1–3 (L1/L2 models + DDL), [docs/schema-comparison.md](docs/schema-comparison.md) §2–3, `bench/answers.py` as the correctness argument |
| 5. Добиени резултати | §0.1 dataset analysis, [docs/schema-comparison.md](docs/schema-comparison.md) §4–5 (measured), §6 limitations, Phase 4 charts, [docs/postgres-plans.txt](docs/postgres-plans.txt) as evidence for the query-6 explanation |
| 6. Заклучок | [docs/schema-comparison.md](docs/schema-comparison.md) §7; which DB suits which access pattern; kvlite single-node vs FDB scale-out; **what the key-value stores bought over the relational baseline, and where they did not** |
| 7. Литература | Oracle NoSQL CE docs, FoundationDB docs, PostgreSQL docs (JSONB, GIN, TOAST), psycopg, TMDB/Kaggle dataset, course materials |
