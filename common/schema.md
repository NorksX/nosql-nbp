# The two schemas — L1 and L2

Phase 2 requires two different aggregation levels over the same 109,222 movies.
This document is the agreed design; `common/keyspec.py` is the executable form of
it and wins in any disagreement. Both sub-teams implement both models.

Every number below was measured, not estimated — `python -m common.verify_schemas`
reproduces all of them in about five seconds and asserts the invariants.

---

## 1. Why two schemas, and how they differ

The two models are deliberately opposite, so that a Phase 4 difference in query
latency is attributable to the schema and to nothing else.

| | **L1 — fine-grained** | **L2 — coarse-grained** |
|---|---|---|
| Unit of storage | one movie per key | ~135 movies per key |
| Keys | **730,414** | **1,867** |
| Secondary indexes | 5 | none |
| Denormalization | none | full — aggregates precomputed at load |
| Optimized for | point lookups, selective filters | reports, whole-year reads |
| Degenerate case | aggregates → full scan | point lookup → full scan |

The 391× key-count ratio is the whole experiment. L1 pays per record on write
and wins on selectivity; L2 pays once at load time and wins on anything that
reads a large fraction of the corpus.

### Fairness invariants

Break any of these and the Phase 4 comparison stops meaning anything.

1. **Identical bytes.** Both models, in both databases, store the record bytes
   produced by `common.dataset.encode` — compact UTF-8 JSON with sorted keys. No
   model stores a reduced projection.
2. **Identical corpus.** All 109,222 records appear in both models. Records with
   no genre (21,934) or no derivable year (5,442) are kept, not dropped.
3. **Identical chunk boundaries.** L2 chunking is deterministic (sort by `id`,
   greedy fill to 90,000 bytes), so Oracle NoSQL and FoundationDB hold the same
   806 chunks with the same contents.
4. **Identical aggregate semantics.** The L2 stats are defined once, by
   `keyspec.aggregate()`. The L1 implementations of queries 8–10 must reproduce
   the same numbers by scanning — that equality is the correctness check.
5. **No index creep in L2.** Adding a secondary index to L2 turns it back into
   L1 and erases the contrast.

---

## 2. Model L1 — fine-grained

### FoundationDB key space

Keys are tuple-packed. Index entries carry an empty value; the key is the whole
payload, which keeps the index subspace small enough to stay in cache.

| Key | Value | Entries |
|---|---|---|
| `("movie", id)` | `encode(record)` | 109,222 |
| `("idx", "imdb", id_imdb)` | `pack((id,))` | 109,222 |
| `("idx", "year", year, id)` | `b""` | 103,780 |
| `("idx", "lang", lang, vote_count, id)` | `b""` | 109,222 |
| `("idx", "genre", genre_id, id)` | `b""` | 149,484 |
| `("idx", "genre_pop", genre_id, −popularity×1000, id)` | `b""` | 149,484 |
| | | **730,414** |

Two of these key shapes are doing real work:

- **`lang` carries `vote_count` before `id`.** Query 4 (language X with
  `vote_count > 500`) becomes one range read starting at
  `("idx", "lang", X, 501)` instead of a scan of the whole language followed by
  a client-side filter. Measured on the live store: **2,611 keys read instead of
  58,015** for English. Note this needs a genuinely two-sided range —
  `tuple.range(("idx","lang","en",501))` bounds the movies whose vote count is
  *exactly* 501, and returns 1 row. `keyspec.KeyRange` exists to make that
  distinction impossible to miss.
- **`genre_pop` stores negated scaled popularity.** FoundationDB sorts keys
  ascending, so negating makes a plain forward range read return most-popular
  first. Query 6 does zero client-side sorting. Popularity is a float and a
  float cannot be negated inside an order-preserving integer key, so it is
  scaled to fixed point at ×1000 — one decimal digit more than the data
  actually carries.

`idx:year` is 5,442 short of the corpus: records with an empty `release_date`
have no year and get no entry. They remain reachable by id, IMDb id, language
and genre, and they are still present in L2 under bucket 0.

### Oracle NoSQL

One table, one JSON column, five native secondary indexes. The document is *not*
flattened into typed columns — keeping it as one JSON value is what makes the
stored bytes match FoundationDB's, so the footprint comparison measures the
storage engine rather than the column encoding.

```sql
CREATE TABLE IF NOT EXISTS l1_movies (
  id INTEGER,
  doc JSON,
  PRIMARY KEY(id)
);

CREATE INDEX idx_imdb       ON l1_movies(doc.id_imdb AS STRING);
CREATE INDEX idx_year       ON l1_movies(doc.year AS INTEGER);
CREATE INDEX idx_lang_votes ON l1_movies(doc.original_language AS STRING,
                                         doc.vote_count AS INTEGER);
CREATE INDEX idx_genre      ON l1_movies(doc.genre_ids[] AS INTEGER);
CREATE INDEX idx_genre_pop  ON l1_movies(doc.genre_ids[] AS INTEGER,
                                         doc.popularity AS DOUBLE);
```

One index per FoundationDB index family, same column order, so both databases are
asked to do the same work with their native mechanism — Oracle NoSQL maintains
these itself, the FoundationDB loader writes them by hand in the same transaction
as the record.

A typed index over a JSON path requires the values at that path to actually have
that type, which is why `common.dataset.normalize` forces `popularity` and
`vote_average` to `float` (a value serialized as `1` rather than `1.0` in a
handful of records would otherwise break `idx_genre_pop`) and drops JSON `null`
instead of storing it.

---

## 3. Model L2 — coarse-grained

### FoundationDB key space

| Key | Value | Entries |
|---|---|---|
| `("year", year, chunk_no)` | JSON array of records, ≤ 90 KB | 806 |
| `("stats", "genre_year", genre_id, year)` | `{n_movies, sum_vote, avg_vote, sum_popularity, avg_popularity, sum_votes}` | 495 |
| `("stats", "lang", lang)` | `{n_movies, sum_popularity, avg_popularity}` | 137 |
| `("stats", "year", year)` | `{n_movies, avg_popularity, n_rated, avg_vote_rated}` | 49 |
| `("top", "genre", genre_id, position)` | `encode(record)`, positions 0–19 | 380 |
| | | **1,867** |

Note what is **absent**: no key leads from a movie id, an IMDb id or a genre to
an individual record. That is the design, not an oversight — it is what forces
queries 1, 2 and 5 into a full scan under L2 and makes the L1/L2 delta visible in
both directions.

**Chunking.** A whole year cannot be one value: 2017 is 4.95 MB, 49× over
FoundationDB's 100 KB value limit. Records are sorted by `id` and greedily packed
to a 90,000-byte target, which leaves room for the last record to overshoot
without crossing the limit — the largest record is 2,021 bytes and the largest
chunk produced is 89,999 bytes. The result is 806 chunks across 49 buckets,
averaging 135 movies and 85 KB each. Bucket sizes are wildly uneven: 27 of the 49
years fit in a single chunk, while 2017 needs 56.

**Aggregate semantics** (fixed in `keyspec.aggregate()`, not left to each team):

- `genre_year` — a movie counts once *per genre it lists*, so these counts sum to
  149,484, not 109,222. No vote floor; query 8 asks for the plain average.
- `lang` — every movie counts exactly once; the counts sum to 109,222.
- `year` — `n_movies`/`avg_popularity` cover the whole bucket, while
  `n_rated`/`avg_vote_rated` cover only `vote_count >= 50` (query 10).
- `genre_top` — top 20 by popularity, ties broken by ascending `id` so the
  leaderboard is deterministic.

Raw sums are stored next to the averages so a stat row can be re-derived or
merged without re-reading the chunks.

### Oracle NoSQL

One table per key family. The chunk and leaderboard tables use a composite
primary key with a shard component — the closest equivalent to FoundationDB's
ordered composite key, since rows sharing a year (or a genre) land in the same
shard and are read together.

```sql
CREATE TABLE IF NOT EXISTS l2_movies_by_year (
  year INTEGER, chunk INTEGER, n_movies INTEGER, movies JSON,
  PRIMARY KEY(SHARD(year), chunk)
);

CREATE TABLE IF NOT EXISTS l2_genre_year_stats (
  genre_id INTEGER, year INTEGER, n_movies INTEGER,
  sum_vote DOUBLE, avg_vote DOUBLE,
  sum_popularity DOUBLE, avg_popularity DOUBLE, sum_votes LONG,
  PRIMARY KEY(SHARD(genre_id), year)
);

CREATE TABLE IF NOT EXISTS l2_lang_stats (
  lang STRING, n_movies INTEGER, sum_popularity DOUBLE, avg_popularity DOUBLE,
  PRIMARY KEY(lang)
);

CREATE TABLE IF NOT EXISTS l2_year_stats (
  year INTEGER, n_movies INTEGER, sum_popularity DOUBLE, avg_popularity DOUBLE,
  n_rated INTEGER, sum_vote_rated DOUBLE, avg_vote_rated DOUBLE,
  PRIMARY KEY(year)
);

CREATE TABLE IF NOT EXISTS l2_genre_top (
  genre_id INTEGER, pos INTEGER, doc JSON,
  PRIMARY KEY(SHARD(genre_id), pos)
);
```

Column names avoid SQL keywords — `n_movies` rather than `count`, `pos` rather
than `rank`.

---

## 4. Measured shape

Logical bytes, before any storage-engine overhead. The Phase 4 on-disk figures
should be compared against these to expose each engine's amplification.

| | L1 | L2 |
|---|---|---|
| Record/chunk values | 68.67 MB | 68.78 MB |
| Primary keys | 1.75 MB | 0.02 MB |
| Index entries | 19.93 MB (621,192) | — |
| Precomputed aggregates | — | 0.33 MB |
| **Total** | **90.35 MB** | **69.13 MB** |
| Keys written per movie | 6.7 | 0.017 |

L1 spends **31 % more space**, almost all of it on index keys, and writes roughly
400× more keys during load. L2's chunk values are marginally larger than L1's
record values only because of the enclosing brackets and separating commas.

---

## 5. Access paths for the ten queries

The Phase 3 query set, with the access path each model offers. "Full scan" means
reading and parsing the entire corpus. This table is the prediction; Phase 4
measures whether it holds.

| # | Query | L1 | L2 |
|---|---|---|---|
| 1 | Movie by `id` | **1 read** | full scan of 806 chunks |
| 2 | Movie by `id_imdb` | **2 reads** (index → record) | full scan |
| 3 | All movies of year Y | index range + N record reads (N ≤ 7,871) | **16–56 chunk reads** |
| 4 | Language X, `vote_count > 500` | **1 range read + K record reads** | full scan + filter |
| 5 | Genre A ∩ genre B | **2 range reads, intersect, then reads** | full scan + filter |
| 6 | Top 20 of a genre by popularity | 20 index keys (pre-sorted) + 20 reads | **1 range read of 20 keys** |
| 7 | Same language, ±1 year, excluding self | weak — see below | **3 buckets (≤ 160 chunks) + filter** |
| 8 | Avg rating & count per genre per year | full scan (Oracle: SQL `GROUP BY`) | **399 stat reads** |
| 9 | Top 10 languages by count, mean popularity | full scan | **1 range read of 137 keys** |
| 10 | Yearly trend, `vote_count >= 50` | full scan | **1 range read of 49 keys** |

Queries 1, 2, 4 and 5 favour L1; queries 3, 6, 7, 8, 9 and 10 favour L2. That
split is intentional — a schema pair where one model wins everything would prove
nothing.

**Query 7 is a deliberate weak spot in L1.** It needs language *and* a year
range, and L1 has no composite `(lang, year)` index — only `(lang, vote_count)`
and `(year)`. Either index alone is unselective: filtering by `en` reads 58,015
entries, filtering by a year reads up to 7,871. We are keeping it that way and
reporting it, because "the index set does not match the query" is a real and
common finding, and because adding a sixth index for one query would inflate
L1's load time and footprint for the other nine. The Phase 4 index-tuning
experiment, if there is time, is to add:

```sql
CREATE INDEX idx_lang_year ON l1_movies(doc.original_language AS STRING,
                                        doc.year AS INTEGER);
```

and, on the FoundationDB side, `("idx", "lang_year", lang, year, id)` — then
re-measure query 7 alone.

**Two asymmetries to expect between the databases, both worth a paragraph in the
report:**

- Queries 8–10 under L1 are a client-side scan in FoundationDB, which has no
  server-side aggregation at all, but a server-side `GROUP BY` in Oracle NoSQL.
  This should be the largest single gap in the whole benchmark.
- Query 6 under L1 needs a descending scan of `idx_genre_pop`. FoundationDB gets
  it for free from the negated key; Oracle NoSQL must satisfy
  `ORDER BY ... DESC` from the index. Confirm the query plan actually uses the
  index rather than sorting, and record it either way.

Exact parameters (which id, which year, which genre pair) and the captured
results belong in `common/queries.md`, written at the start of Phase 3.

---

## 6. Divergences from `PLAN.md`

`PLAN.md` §Phase 2 sketched both models. Three things changed once the design was
measured against the real data:

1. **L2 gained three key families.** `PLAN.md` listed only per-year chunks and
   genre-year stats, which between them answer query 8 and little else. Language
   stats, year stats and the per-genre leaderboard were added so L2 can actually
   answer queries 6, 9 and 10 from precomputed data — otherwise six of the ten
   queries would degrade to a full scan under L2 and the comparison would be
   one-sided. They cost 566 keys and 0.33 MB.
2. **The L1 language index is composite.** `PLAN.md` had `("idx", "lang", lang,
   id)`; it is now `(lang, vote_count, id)`, mirroring Oracle's
   `idx_lang_votes`. Same entry count, and query 4 becomes a range read.
3. **806 chunks, not 744.** `PLAN.md`'s estimate was derived from a 63.5 MB
   corpus figure that does not reproduce. Re-encoding the raw lines as UTF-8
   gives 69.87 MB, and the normalized records are 68.67 MB — so the corpus is
   ~8 % larger than assumed and needs proportionally more chunks. The related
   `PLAN.md` §0.1 claim that UTF-8 re-encoding saves 70.6 → 63.5 MB is
   overstated: measured, it is 70.74 → 69.87 MB, a 1.2 % saving, because only a
   small share of the corpus is non-ASCII. The per-record figure in `PLAN.md`
   (largest record 2,021 bytes) does reproduce exactly.

---

## 7. Verified against both live databases

Both schemas have been created, loaded with all 109,222 records, and read back
through every key family. Reproduce with:

```bash
docker exec kv-client  python -m common.apply_schema   # DDL; FDB needs none
docker exec kv-client  python -m common.live_check     # load + verify, ~95 s
docker exec fdb-client python -m common.live_check     # load + verify, ~19 s
```

`common/live_check.py` computes every expected answer from the data files, so it
is checking the databases, not itself. All 17 checks pass on both stores, and
the two return identical answers to identical questions.

### Load

| | Oracle NoSQL | FoundationDB |
|---|---|---|
| L1 | 109,222 rows in **83.3 s** (1,311 rows/s) | 730,414 keys in **16.0 s** (45,775 keys/s = 6,845 movies/s) |
| L2 | 1,867 rows in **6.9 s** | 1,867 keys in **0.5 s** |

Not a like-for-like race: Oracle NoSQL is doing one HTTP `put` per row and
maintaining five indexes server-side, while the FoundationDB loader batches
~1,000 pairs per transaction and writes its index keys itself. That the totals
land within 5× of each other despite a 6.7× difference in keys written is
itself the interesting result — the batching is worth more than the index work
costs. A fair per-operation comparison is Phase 4's job.

### Footprint

| | Oracle NoSQL | FoundationDB |
|---|---|---|
| Reported | 228.9 MB (`du /kvroot/kvstore`) | 152 MB "sum of key-value sizes"; 345 MB disk used; 531 MB raw `du` |
| vs. 159.5 MB logical | 1.44× | 0.95× accounted / 2.16× on disk |

Read these carefully rather than quoting the headline: both stores currently
hold **L1 and L2 together**, so neither number is per-model — Phase 4 has to
load one model at a time to separate them. FoundationDB's raw directory size is
dominated by pre-allocated transaction-log files and says almost nothing about
the data; its "sum of key-value sizes" tracks our computed 159.5 MB closely,
which is a good independent confirmation that the loaders wrote what the design
says they should.

### Behaviour worth recording for the report

- **Oracle NoSQL accepts `t.doc.genre_ids[] =any 18`** for array containment.
  `live_check` probes the alternatives and prints which one the running version
  takes, so this is observed, not quoted from documentation.
- **`ORDER BY t.doc.popularity DESC LIMIT 20` over `idx_genre_pop` works** and
  returns exactly the same 20 ids, in the same order, as FoundationDB's negated
  key range. The two databases reach an identical answer by completely
  different mechanisms — the strongest single illustration in the whole project.
- **Server-side `GROUP BY` works** on the JSON path
  (`GROUP BY t.doc.original_language`, 137 groups). FoundationDB has no
  equivalent and must scan; expect this to be the largest gap in Phase 4.
- **L2 chunks must be batched by bytes, not by count.** Chunks average 85 KB, so
  the 1,000-pair batch size that suits L1 would build an 85 MB transaction
  against FoundationDB's 10 MB limit. `keyspec.batched()` bounds by both.

---

## 8. Status

- ✅ Both schemas defined, executable, verified offline, and verified live on
  both databases with the full corpus.
- ⬜ `common/queries.md` — exact query parameters, Phase 3. `live_check.py`
  already fixes the probe constants (year 2017, `en`, `vote_count > 500`,
  genres 18 ∩ 27) and those should carry over.
- ⬜ Production loaders (`oracle/load_l*.py`, `fdb/load_l*.py`) — restartable,
  progress-reporting, per-model timing. `live_check.py`'s loader is the
  simplest thing that works and is the timing to beat.
- ⬜ Per-model footprint, which needs one model loaded at a time.
