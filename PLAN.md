# Project Plan — Key-Value NoSQL Databases (Тема 1)

**Course:** Неструктурирани бази на податоци, 2025/2026
**Topic:** Key-value бази на податоци
**Databases:** Oracle NoSQL Database CE (sub-team A) · FoundationDB (sub-team B)
**Dataset:** TMDB Movies 2000–2020 with IMDb ID

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
| Total size | ~135 MB raw |
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
- Encoding matters: the raw files serialize non-ASCII as `\uXXXX` escapes (70.6 MB). Re-encoded as UTF-8 (`ensure_ascii=False`) the same data is **63.5 MB**, and the largest single record drops from 5,264 to **2,021 bytes**. Both databases store the UTF-8 form, so sizes are comparable.

### 0.2 Container images — the cross-platform question is solved

Both images are **multi-arch (linux/amd64 + linux/arm64)**. Confirmed by reading the registry manifest lists directly:

| Image | Tag | amd64 | arm64 |
|---|---|---|---|
| `ghcr.io/oracle/nosql` | `latest-ce` (= `2025-12-ce`, same digest) | ✅ | ✅ |
| `foundationdb/foundationdb` | `7.3.79` | ✅ | ✅ |

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

FoundationDB is strict about client/server version matching. Pin `foundationdb/foundationdb:7.3.79` and `foundationdb==7.3.79` and bump them together or not at all.

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
- Python 3.11 on the host for orchestration scripts. ✅ `.venv` recreated as **3.11.15** (`uv venv --python 3.11 .venv`), matching the client containers.
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
│   └── foundationdb/
│       ├── docker-compose.yml
│       ├── Dockerfile.client
│       └── README.md              # install log → elaborate
├── common/
│   ├── dataset.py                 # JSONL reader, normalization, genre map
│   ├── keyspec.py                 # shared key-design constants (both teams)
│   └── queries.md                 # the 10 query definitions, DB-agnostic
├── oracle/                        # sub-team A
│   ├── load_l1.py  load_l2.py  queries.py
├── fdb/                           # sub-team B
│   ├── load_l1.py  load_l2.py  queries.py
├── bench/
│   ├── harness.py  plots.py  results/
└── docs/
    ├── elaborat.md  presentation/
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

### 1.5 Phase 1 deliverables

- ✅ Both compose stacks committed and working on Apple Silicon. ⬜ Still needs one x64 run.
- ✅ `docker/*/README.md` = the literal installation log: commands run, output captured, every problem hit and how it was solved. This is what section 4 of the елаборат is graded on.
- ✅ Smoke tests committed (`docker/*/smoke_test.py`) so any teammate can prove their machine is set up correctly in one command.
- ✅ Comparison of installation effort, for the report:

| | Oracle NoSQL CE | FoundationDB |
|---|---|---|
| Steps to usable | 1 — `up -d`, self-initializing | 2 — `up -d` **plus** `configure new single ssd` |
| Config required | none | cluster file must be shared with every client |
| Client/server coupling | loose — HTTP proxy, any HTTP client | strict — `libfdb_c.so` must match server version *and* arch |
| Host-side access | works (`http://localhost:8080`) | not portable; must run in-network |
| Server image size | 1.01 GB (JVM + GraalVM) | 2.04 GB |
| Client image size | 240 MB | 304 MB |
| Cold start to serving | ~8 s | ~2 s |

That contrast is a genuine finding, not filler: Oracle NoSQL trades a slow start for a zero-configuration, protocol-agnostic entry point; FoundationDB starts four times faster but pushes cluster topology and strict version management onto the client. Note the image sizes cut against the intuition that the JVM stack must be the heavier one — the FoundationDB image is twice the size.

---

## Phase 2 — ИМПОРТИРАЊЕ НА ПОДАТОЦИТЕ

The assignment explicitly asks for **two different aggregation levels**. Both sub-teams implement the same two models so the Phase 4 comparison is apples-to-apples.

A shared `common/` module should hold the key format constants and query definitions so neither team drifts — **written by hand, one piece at a time, once the team understands the shape of the data.** Not scaffolded up front.

Two things were confirmed against the running databases and are worth keeping in mind when that code does get written:
- Oracle NoSQL accepts an index on an array inside a JSON column (`doc.genre_ids[] AS INTEGER`) and a composite primary key with a partition key (`PRIMARY KEY(SHARD(year), chunk)`).
- FoundationDB keys sort in order, so a negated scaled integer inside the key gives descending-popularity reads with no sorting.

### Model L1 — fine-grained (one movie per key)

Pure key-value, one record per key:

| Purpose | Oracle NoSQL | FoundationDB |
|---|---|---|
| Primary | `movies(id INTEGER, doc JSON, PRIMARY KEY(id))` | `("movie", id) → msgpack/json(record)` |
| By IMDb | index / `imdb` field | `("idx","imdb", imdb_id) → id` |
| By genre | index on `doc.genre_ids[]` | `("idx","genre", genre_id, id) → ø` |
| By language | index on `doc.original_language` | `("idx","lang", lang, id) → ø` |
| By year | index on derived `year` | `("idx","year", year, id) → ø` |

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

| Key | Value |
|---|---|
| `("year", 2017, chunk)` | that year's movies as JSON-array chunks — the largest year is 2017 at **4.38 MB / 7,871 movies**, 44× over FDB's value limit, so chunking at 90 KB is mandatory (**744 chunks** across 49 year buckets) |
| `("stats", "genre_year", genre_id, year)` | precomputed `{count, avg_vote, avg_popularity, sum_votes}` |

L2 is where the interesting limits show up, and both are worth a paragraph in the report:
- **FoundationDB:** value limit **100 KB**, key limit 10 KB, transaction limit **10 MB / 5 seconds**. A whole year cannot be one value — it must be split into ~100 KB chunks under `("year", 2014, chunk_no)`. Batch writes at ~500–1,000 KV pairs per transaction with retry on `transaction_too_old`.
- **Oracle NoSQL:** a large JSON column is accepted but request-size and read-unit limits apply; measure and document where it degrades.

### Loader design

Shared skeleton in `common/dataset.py`: stream the JSONL line by line (never `json.load` a whole file — they are not JSON arrays), derive `year` from `release_date`, normalize `null` → absent, emit `(key, value)` pairs. Each team writes `load_l1.py` / `load_l2.py` on top. Loaders must be **idempotent and restartable**, and must record wall-clock load time + resulting on-disk size — both feed Phase 4.

**Deliverable:** ER-style / key-space diagram of both models, the loader code, load timings, and a written analysis of why each model suits or fights each database.

---

## Phase 3 — КОРИСТЕЊЕ НА ПОДАТОЦИТЕ

Ten queries, spanning the three categories the assignment requires (6–10 needed). Defined DB-agnostically in `common/queries.md`, implemented twice.

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

---

## Phase 4 — СПОРЕДБА НА ПЕРФОРМАНСИТЕ

Shared harness `bench/harness.py`, run from each database's `client` container, writing CSV to `bench/results/`.

**Protocol:** discard warm-up runs; point queries × 1,000 iterations, complex × 200, aggregates × 30; report p50 / p95 / p99 and throughput; three repetitions per configuration; identical dataset, identical query semantics.

**Measure:** import time (L1 and L2), on-disk footprint, per-query latency distribution, aggregate query time L1 vs L2, and behaviour under 1 / 4 / 16 concurrent client threads.

**Single vs. multiple processors** (requirement 4) — portable across both stacks:
- Constrain each DB service with `deploy.resources.limits.cpus: "1"` then `"4"`, re-run the suite. Same knob, both databases, works identically on macOS and x64.
- Additionally for FoundationDB: scale from a single `fdbserver` process to a multi-process cluster (`configure double ssd` with separate storage/stateless/log processes) and re-measure — FDB is architecturally built for this and the delta is a strong result. Oracle NoSQL CE via kvlite is single-node by design; state that limitation explicitly rather than faking a cluster.

**Output:** `bench/plots.py` → matplotlib bar/line charts (latency by query, latency by CPU count, import throughput) for section 5 of the елаборат.

---

## Team split & sequencing

Sub-team A → Oracle NoSQL CE. Sub-team B → FoundationDB. Shared/joint: dataset analysis, `common/` (key spec + query definitions + loader skeleton), benchmark harness, report, presentation.

| Step | Work | Owner |
|---|---|---|
| 1 | ✅ Compose stacks up + install logs (pending one x64 run) | Both, in parallel |
| 2 | Dataset analysis + `common/` key spec & query definitions | Joint — must be agreed **before** loaders are written |
| 3 | L1 loader + verification | Each sub-team |
| 4 | L2 loader + chunking | Each sub-team |
| 5 | 10 queries implemented | Each sub-team |
| 6 | Benchmark harness + runs (incl. 1 vs 4 CPU) | Joint harness, separate runs |
| 7 | Charts + елаборат + 10-min presentation | Joint |

**Sync point:** step 2 must be finished and agreed by both sub-teams before step 3 starts. If the two teams pick different key formats or different query semantics, Phase 4 produces numbers that cannot be compared and the main deliverable is lost.

## Mapping to the required elaborate structure

| Елаборат section | Fed by |
|---|---|
| 3. Вовед | Key-value model overview, why Oracle NoSQL CE vs FoundationDB |
| 4. Методологија | Phase 1 install logs, Phase 2 L1/L2 models + diagrams, Phase 3 query set |
| 5. Добиени резултати | §0.1 dataset analysis, Phase 3 outputs & limitations, Phase 4 charts |
| 6. Заклучок | Which DB suits which access pattern; kvlite single-node vs FDB scale-out |
| 7. Литература | Oracle NoSQL CE docs, FoundationDB docs, TMDB/Kaggle dataset, course materials |
