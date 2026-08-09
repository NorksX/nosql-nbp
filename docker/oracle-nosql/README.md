# Installation log — Oracle NoSQL Database CE

Verified working on **macOS / Apple Silicon (aarch64), Docker 29.6.1, 8 CPU, 7.75 GB RAM**, 2026-08-09.

## Image

| | |
|---|---|
| Image | `ghcr.io/oracle/nosql:2025-12-ce` (identical digest to `latest-ce`) |
| Size | 1.01 GB |
| Architectures | linux/amd64 **and** linux/arm64 — pulled natively, **no emulation** |
| KV version | 25.3.21 (build `60eebbcaec4d`, 2025-12-19), **Edition: Community** |
| Runtime | GraalVM Community Java 17 |

> Do **not** add `platform:` to the compose file. The image is multi-arch, so Docker
> already picks the native architecture. Forcing `linux/amd64` would run the JVM
> under emulation and invalidate all Phase 4 benchmark numbers.

## Install

```bash
cd docker/oracle-nosql
docker compose up -d --build
```

No configuration, no initialization step: the image's `start-kvlite.sh` creates the
store and starts the proxy on first boot. Startup arguments reported by the container:

```
Created new kvlite store with args:
-root /kvroot -store kvstore -host kvlite -port 5000 -admin-web-port 5999 -secure-config disable
Proxy started:
  helperHosts=kvlite:5000   httpPort=8080   proxyType=KVPROXY
  kvConsistency=NONE_REQUIRED   kvDurability=COMMIT_NO_SYNC
  numAcceptThreads=3   numRequestThreads=32
  proxyVersion=25.3.21   kvclientVersion=25.3.21
```

## Verification

### 1. Store health

```bash
docker exec kvlite bash -c 'java -jar /app/kv-*/lib/kvstore.jar ping -host localhost -port 5000'
```

```
Pinging components of store kvstore based upon topology sequence #14
10 partitions and 1 storage nodes
Shard Status: healthy: 1  writable-degraded: 0  read-only: 0  offline: 0  total: 1
Admin Status: healthy
Zone [name=KVLite id=zn1 type=PRIMARY]   RN Status: online: 1  read-only: 0  offline: 0
Storage Node [sn1] on kvlite:5000   Status: RUNNING   Ver: 25.3.21   Edition: Community
    Admin [admin1]      Status: RUNNING,MASTER
    Rep Node [rg1-rn1]  Status: RUNNING,MASTER  haPort: 5011  storageType: HD
```

The `kv-*` glob matters — the jar lives under a version-stamped directory
(`/app/kv-25.3.21/lib/kvstore.jar`), so hardcoding the version breaks on image updates.

### 2. Driver round trip

```bash
docker compose exec client python docker/oracle-nosql/smoke_test.py
```

```
endpoint: http://kvlite:8080
1. CREATE TABLE
2. PUT
3. GET
   -> OrderedDict([('id', 1), ('doc', OrderedDict([('title', 'Closed Ward'), ('year', 2001)]))])
4. QUERY (SQL over JSON)
   -> OrderedDict([('year', 2001)])
5. DELETE + DROP

SMOKE TEST PASSED
```

### 3. Persistence

Wrote a row, `docker compose restart kvlite`, read it back:

```
after restart -> OrderedDict([('id', 42), ('doc', OrderedDict([('v', 'survived')]))])
```

The `kvroot` named volume preserves the store. Proxy became reachable again **~8 s**
after restart.

## Problems encountered and how they were solved

### Problem 1 — the obvious healthcheck endpoint does not exist

The first healthcheck used `curl -sf http://localhost:8080/ping`. It failed on every
attempt (exit 1, empty output) and the container sat in `health: starting` forever.
Probing the proxy showed why:

| Path | HTTP status |
|---|---|
| `/` | 400 |
| `/ping` | 400 |
| `/V0/nosql/data` | 200 |
| `/V2/nosql/data` | **200** |

The proxy only answers on its data endpoints; anything else returns 400, which
`curl -f` treats as failure. **Fix:** probe `/V2/nosql/data`. The container now reports
`healthy`.

### Problem 2 — why we do not use port 5000 from the host

kvlite registers its storage node under the *container* hostname (`Storage Node [sn1]
on kvlite:5000`, see the ping output). A Java `KVStore` driver running on the host
resolves the registry, is handed back `kvlite:5000`, and cannot route to that name.

**Fix / design decision:** all access goes through the **HTTP proxy on 8080** using the
`borneo` Python SDK. The proxy is a plain request/response endpoint with no address
indirection, so it behaves identically from the host and from a sibling container, on
macOS and on x64 Linux alike. Ports 5000 and 5999 stay published for `ping` and admin
inspection only.

## Connection details

| From | Endpoint |
|---|---|
| `client` container (recommended) | `http://kvlite:8080` — preset as `$NOSQL_ENDPOINT` |
| Host machine | `http://localhost:8080` |

Non-secure store, so the SDK uses `StoreAccessTokenProvider()` with no credentials.

## Everyday commands

```bash
docker compose up -d              # start
docker compose ps                 # kvlite should read (healthy)
docker compose exec client bash   # shell with borneo installed, repo at /work
docker compose logs -f kvlite     # store + proxy logs
docker compose down               # stop, keep data
docker compose down -v            # stop and WIPE the store
```
