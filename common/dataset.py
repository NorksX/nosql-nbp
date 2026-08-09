"""Streaming reader and normalization for the TMDB dataset.

The 21 files in ``data/`` are JSONL — one movie object per line, *not* a JSON
array — so everything here streams line by line and nothing ever calls
``json.load`` on a whole file.

Both sub-teams load from ``iter_movies()`` and store the bytes produced by
``encode()``. That is a fairness requirement, not a style preference: if the two
databases hold different bytes, the Phase 4 on-disk footprint comparison and the
query results stop being comparable.

Usage:
    from common.dataset import iter_movies, encode

    for movie in iter_movies():
        blob = encode(movie)
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TMDB_DATA_DIR") or REPO_ROOT / "data")

#: Bucket for the 5,442 records with an empty ``release_date``. They have no
#: derivable year, so they are excluded from the L1 year index and collected
#: here in L2 rather than being dropped — the corpus must stay at 109,222.
YEAR_UNKNOWN = 0

#: The 19 genre ids that actually occur in the dataset, with their TMDB names.
#: Used for report labels and for iterating genre-keyed aggregates.
GENRE_NAMES = {
    12: "Adventure",
    14: "Fantasy",
    16: "Animation",
    18: "Drama",
    27: "Horror",
    28: "Action",
    35: "Comedy",
    36: "History",
    37: "Western",
    53: "Thriller",
    80: "Crime",
    99: "Documentary",
    878: "Science Fiction",
    9648: "Mystery",
    10402: "Music",
    10749: "Romance",
    10751: "Family",
    10752: "War",
    10770: "TV Movie",
}

#: Fields carried through normalization, in canonical order. ``year`` is derived
#: and appended by :func:`normalize`; it is not present in the source files.
_STRING_FIELDS = (
    "id_imdb",
    "title",
    "original_title",
    "original_language",
    "overview",
    "poster_path",
    "backdrop_path",
    "release_date",
)
_FLOAT_FIELDS = ("popularity", "vote_average")
_INT_FIELDS = ("vote_count",)
_BOOL_FIELDS = ("adult", "video")


def data_files(data_dir: Path | None = None) -> list[Path]:
    """The 21 JSONL files, sorted by fetch year so loads are reproducible."""
    directory = Path(data_dir) if data_dir else DATA_DIR
    files = sorted(directory.glob("tmdb-movies-*.json"))
    if not files:
        raise FileNotFoundError(
            f"no tmdb-movies-*.json in {directory} — run `python download.py` first"
        )
    return files


def derive_year(release_date: str | None) -> int | None:
    """Year from ``release_date``, or None when it cannot be derived.

    Never derive the year from the filename: the files are grouped by *fetch*
    year, so tmdb-movies-2000.json contains a movie released 2001-04-07.
    """
    head = (release_date or "")[:4]
    return int(head) if head.isdigit() else None


def normalize(raw: dict) -> dict:
    """Canonical record: typed, null-free, deterministically ordered.

    - ``popularity`` / ``vote_average`` are forced to float and ``vote_count``
      to int. Oracle NoSQL indexes a JSON path with a declared type
      (``doc.popularity AS DOUBLE``), so a value that serializes as ``1``
      instead of ``1.0`` in a handful of records would be a type mismatch.
    - JSON ``null`` becomes an absent key (18% of records have a null
      ``backdrop_path``); absent is cheaper to store and both databases treat
      "missing" consistently, whereas null vs missing differ.
    - ``genre_ids`` is sorted and de-duplicated so the genre index entries of a
      movie are generated in a stable order.
    - ``year`` is added when derivable and omitted otherwise.
    """
    out: dict = {"id": int(raw["id"])}

    for field in _STRING_FIELDS:
        value = raw.get(field)
        if value:  # drops both None and ""
            out[field] = value

    for field in _FLOAT_FIELDS:
        out[field] = float(raw.get(field) or 0.0)
    for field in _INT_FIELDS:
        out[field] = int(raw.get(field) or 0)
    for field in _BOOL_FIELDS:
        out[field] = bool(raw.get(field))

    out["genre_ids"] = sorted({int(g) for g in raw.get("genre_ids") or ()})

    year = derive_year(raw.get("release_date"))
    if year is not None:
        out["year"] = year

    return out


def iter_movies(
    data_dir: Path | None = None, files: Iterable[Path] | None = None
) -> Iterator[dict]:
    """Stream every movie in the dataset as a normalized record."""
    for path in files if files is not None else data_files(data_dir):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield normalize(json.loads(line))


def encode(record: dict) -> bytes:
    """Canonical stored form: compact UTF-8 JSON with sorted keys.

    ``ensure_ascii=False`` matters — the source files escape non-ASCII as
    ``\\uXXXX``, which inflates the corpus from 63.5 MB to 70.6 MB and would
    make the footprint comparison measure the escaping, not the database.
    ``sort_keys`` makes the bytes identical in both stores.
    """
    return json.dumps(
        record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def decode(blob: bytes | str) -> dict:
    """Inverse of :func:`encode`."""
    return json.loads(blob)
