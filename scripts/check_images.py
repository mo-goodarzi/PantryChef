"""Check how many stored photo URLs still load (a random sample, card size).

The app only links to Food.com's photos, so a moved photo shows as a broken image. This
measures how often that happens; it is never run by the app itself.

Usage:
    uv run python scripts/check_images.py              # 500 random recipes
    uv run python scripts/check_images.py --sample 100
"""

import argparse
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.db.images import CARD, sized

TIMEOUT_S = 15


def status(url: str) -> str:
    """The HTTP status ("200", "404") or the kind of network error."""
    request = urllib.request.Request(url, headers={"User-Agent": "PantryChef image check"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            response.read(1)
            return str(response.status)
    except urllib.error.HTTPError as error:
        return str(error.code)
    except (urllib.error.URLError, TimeoutError) as error:
        return type(error).__name__


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", type=int, default=500)
    args = parser.parse_args()

    conn = connect(settings.db_path)
    urls = [
        url
        for (raw,) in conn.execute(
            "SELECT image_url FROM recipes WHERE image_url IS NOT NULL ORDER BY random() LIMIT ?",
            (args.sample,),
        )
        if (url := sized(raw, CARD))
    ]
    conn.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = Counter(pool.map(status, urls))

    ok = counts.get("200", 0)
    print(f"\nPhotos that load: {ok} of {len(urls)} ({ok / max(len(urls), 1):.1%})")
    for code, n in counts.most_common():
        print(f"  {code:>14}  {n}")


if __name__ == "__main__":
    main()
