"""
End-to-end example: read two pages of a listing and write them to JSON.

Run:
    python example_usage.py
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from api_client import Post, iter_pages

OUT_FILE = Path(__file__).with_name("posts.json")

SUBREDDIT = "popular"   # any public subreddit, or "popular" / "all"


def main() -> None:
    posts: list[Post] = []

    for page_num, page in enumerate(
        iter_pages(SUBREDDIT, limit=25, max_pages=2, delay=1.0), start=1
    ):
        print(f"page {page_num}: {len(page)} posts (after={page.after})")
        posts.extend(page)

    OUT_FILE.write_text(
        json.dumps([asdict(p) for p in posts], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\ntotal: {len(posts)} posts -> {OUT_FILE.name}")
    if posts:
        top = max(posts, key=lambda p: p.score)
        print(f"top post: [r/{top.subreddit}] {top.title} (score {top.score})")
        print(f"permalink: {top.permalink}")


if __name__ == "__main__":
    main()
