"""Generate a stand-in corpus with the measured shape of the TMDB dataset.

    python sandbox/make_corpus.py [--out data]

This is NOT part of the project deliverable and nothing in the report should
ever be measured against it. It exists so the loaders, the schemas and the
twenty query implementations can be exercised end-to-end on a machine that has
no Kaggle credentials — every structural property the code depends on is
reproduced from PLAN.md §0.1, so a bug in a key format or a SQL statement shows
up here exactly as it would on the real files.

Reproduced from the measured profile:

    109,222 records, unique ``id``, 100% ``id_imdb`` present and unique
     21,934 (20.1%) with no genre at all
     41,071 (37.6%) with ``vote_count == 0``
      5,442 (5.0%)  with an empty ``release_date`` — no derivable year
        137 languages, ``en`` dominant; 19 genre ids; years 1926-2021
    the probe movie 419704 / tt2935510, 2019, ``en``, most popular in the corpus

What it does *not* reproduce is the actual text, so absolute byte sizes are
close but not identical, and no latency measured here is comparable with a run
on the real data.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

GENRES = [12, 14, 16, 18, 27, 28, 35, 36, 37, 53, 80, 99, 878, 9648,
          10402, 10749, 10751, 10752, 10770]
# Drama (18) most common, so PROBE_GENRE is the most common genre as measured.
GENRE_WEIGHTS = [6, 5, 5, 22, 8, 10, 14, 2, 1, 9, 6, 7, 4, 4, 3, 8, 5, 2, 3]

TOP_LANGS = [("en", 58015), ("es", 6455), ("fr", 5818), ("ja", 4027),
             ("de", 3973), ("it", 3100), ("zh", 2600), ("pt", 2300),
             ("hi", 1900), ("ko", 1700)]
TOTAL = 109_222
N_NO_GENRE = 21_934
N_ZERO_VOTES = 41_071
N_NO_DATE = 5_442

WORDS = (
    "a young woman returns to her home town after many years and discovers "
    "that the family business has been sold to a stranger who claims to know "
    "more about her father than she does while an old friend insists that the "
    "whole story was invented by someone who left the country long before any "
    "of it could have happened in the first place and nobody in the village "
    "seems willing to explain why the records for that decade are missing"
).split()


def make_languages(rng: random.Random) -> list[str]:
    """137 language codes: the measured top ten plus a long tail."""
    tail = []
    letters = "abcdefghijklmnopqrstuvwxyz"
    while len(tail) < 137 - len(TOP_LANGS):
        code = rng.choice(letters) + rng.choice(letters)
        if code not in dict(TOP_LANGS) and code not in tail:
            tail.append(code)
    return tail


def year_distribution(rng: random.Random) -> list[int]:
    """48 distinct years, as measured, heavily weighted to the 2010s.

    The real corpus spans 1926-2021 but only 48 of those years actually occur,
    which is what fixes L2 at 49 buckets (48 + the undated bucket) and the
    genre-year aggregate at a few hundred rows. Spreading the same records over
    96 years would quietly double that table and change what query 8 measures.
    """
    years, weights = [], []
    for y in range(1974, 2022):
        years.append(y)
        if y >= 2015:
            weights.append(90)
        elif y >= 2005:
            weights.append(55)
        elif y >= 1990:
            weights.append(18)
        else:
            weights.append(4)
    # 2017 is the largest bucket in the real data; make it so here too.
    weights[years.index(2017)] = 130
    return rng.choices(years, weights=weights, k=TOTAL - N_NO_DATE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data")
    parser.add_argument("--seed", type=int, default=20260913)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    langs = [code for code, _ in TOP_LANGS] + make_languages(rng)
    lang_weights = [n for _, n in TOP_LANGS] + [
        max(1, int(600 / (i + 1))) for i in range(len(langs) - len(TOP_LANGS))
    ]
    years = year_distribution(rng)

    ids = rng.sample(range(1_000, 1_400_000), TOTAL)
    no_genre = set(rng.sample(range(TOTAL), N_NO_GENRE))
    zero_votes = set(rng.sample(range(TOTAL), N_ZERO_VOTES))
    no_date = set(rng.sample(range(TOTAL), N_NO_DATE))

    records = []
    yi = 0
    for i in range(TOTAL):
        if i in no_date:
            release = ""
        else:
            release = (f"{years[yi]}-{rng.randint(1, 12):02d}-"
                       f"{rng.randint(1, 28):02d}")
            yi += 1

        if i in no_genre:
            genre_ids = []
        else:
            genre_ids = sorted(set(rng.choices(
                GENRES, weights=GENRE_WEIGHTS, k=rng.choice([1, 1, 2, 2, 3]))))

        votes = 0 if i in zero_votes else int(rng.lognormvariate(4.2, 1.9))
        overview = " ".join(
            rng.choice(WORDS) for _ in range(rng.randint(20, 90))
        ).capitalize() + "."

        records.append({
            "id": ids[i],
            "id_imdb": f"tt{i:07d}",
            "title": " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 6))).title(),
            "original_title": " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 6))),
            "original_language": rng.choices(langs, weights=lang_weights, k=1)[0],
            "genre_ids": genre_ids,
            "release_date": release,
            "overview": overview,
            "popularity": round(rng.lognormvariate(0.4, 1.1), 3),
            "vote_average": 0.0 if votes == 0 else round(rng.uniform(3.5, 8.8), 1),
            "vote_count": votes,
            "adult": False,
            "video": False,
            "poster_path": f"/{rng.getrandbits(88):022x}.jpg",
            "backdrop_path": None if rng.random() < 0.18 else f"/{rng.getrandbits(88):022x}.jpg",
        })

    # The probe movie the ten queries are asked about must exist, with exactly
    # the id, IMDb id, year and language common/keyspec.py fixes — and must be
    # the most popular record, so live_check's independently derived probe is
    # the same movie.
    probe = records[0]
    probe.update({
        "id": 419704,
        "id_imdb": "tt2935510",
        "title": "Ad Astra",
        "original_title": "Ad Astra",
        "original_language": "en",
        "genre_ids": [18, 878],
        "release_date": "2019-09-17",
        "popularity": 9_999.0,
        "vote_average": 6.0,
        "vote_count": 4_000,
    })

    # 21 files, grouped by "fetch year" the way the real dataset is — so the
    # year really does have to come from release_date, not from the filename.
    rng.shuffle(records)
    per_file = -(-TOTAL // 21)
    total_bytes = 0
    for fi, start in enumerate(range(0, TOTAL, per_file)):
        path = out / f"tmdb-movies-{2000 + fi}.json"
        with open(path, "w", encoding="utf-8") as fh:
            for record in records[start : start + per_file]:
                # The real files escape non-ASCII; ensure_ascii=True matches.
                fh.write(json.dumps(record) + "\n")
        total_bytes += path.stat().st_size

    print(f"wrote {TOTAL:,} records to {out}/ in 21 files, "
          f"{total_bytes / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
