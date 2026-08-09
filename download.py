import shutil
from pathlib import Path

import kagglehub

DEST = Path(__file__).parent.resolve() / "data"

# Download latest version
path = kagglehub.dataset_download("hudsonmendes/tmdb-movies-20002020-with-imdb-id")

# Copy the dataset files out of the kagglehub cache into DEST
DEST.mkdir(parents=True, exist_ok=True)
for src in Path(path).iterdir():
    shutil.copy2(src, DEST / src.name)

print("Path to dataset files:", DEST)
