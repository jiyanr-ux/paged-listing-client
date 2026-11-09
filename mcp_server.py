"""
MCP server for the paged listing client
========================================

Exposes read-only access to a public listing feed as MCP tools, so agents
(Claude Code, the reverse-api-engineer agent, etc.) can call it without
touching the client internals. Tool names and descriptions are intentionally
generic and describe only what the tools do.

Run with:

    uv run --with mcp python mcp_server.py

Register with Claude Code (user scope):

    claude mcp add --scope user paged-listing -- \\
        uv run --with mcp --directory <repo-path> python mcp_server.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

# Import the local client whether run from the repo root or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from api_client import iter_pages, get_listing

# Measured with the service's own rate-limit response headers:
#   - 100 requests per minute per OAuth client id (rolling window, bursts ok)
#   - 100 posts per request is the maximum the service returns
#   -> ~10,000 posts/minute is the practical ceiling on the free tier.
# Default pacing of 0.7 s between requests keeps bulk runs under the budget
# while the client's built-in backoff handles any 429 the service returns.
DEFAULT_DELAY_S: float = max(0.0, float(os.environ.get("PL_DELAY_S", "0.7")))

mcp = FastMCP(
    "paged-listing",
    instructions=(
        "Read-only access to a public listing feed with cursor pagination. "
        "Use read_feed for a single page (cursor-based) or read_bulk to fetch "
        "many pages in one call. Results are JSON."
    ),
)


def _page_to_json(page: Any) -> dict:
    return {
        "posts": [
            {
                "post_id": p.post_id,
                "title": p.title,
                "subreddit": p.subreddit,
                "author": p.author,
                "score": p.score,
                "num_comments": p.num_comments,
                "permalink": p.permalink,
                "created_utc": p.created_utc,
            }
            for p in page.posts
        ],
        "next_cursor": page.after,
    }


@mcp.tool()
def read_feed(
    subreddit: str = "popular",
    limit: int = 100,
    cursor: str | None = None,
    sort: str | None = None,
    timeframe: str | None = None,
) -> str:
    """Read one page of public posts from a listing feed.

    Returns JSON with ``posts`` (title, community, author, score, comment
    count, permalink) and ``next_cursor``; pass that value as ``cursor`` to
    read the next page. ``limit`` is clamped to the service maximum of 100.
    """
    page = get_listing(subreddit, sort=sort, limit=min(max(limit, 1), 100), after=cursor, timeframe=timeframe)
    return json.dumps(_page_to_json(page))


@mcp.tool()
def read_bulk(
    subreddit: str = "popular",
    limit: int = 100,
    max_pages: int = 10,
    sort: str | None = None,
    timeframe: str | None = None,
    delay_s: float | None = None,
) -> str:
    """Read several consecutive pages of public posts in one call.

    Pages sequentially with polite pacing (default 0.7 s between requests,
    safely under the service's 100-requests-per-minute budget) and stops when
    the cursor runs out or ``max_pages`` is reached. Returns JSON with all
    posts, the number of pages fetched, and the cursor to continue from.
    """
    delay = DEFAULT_DELAY_S if delay_s is None else max(0.0, float(delay_s))
    posts: list[dict] = []
    next_cursor: str | None = None
    pages_fetched = 0

    for page in iter_pages(
        subreddit,
        sort=sort,
        limit=min(max(limit, 1), 100),
        max_pages=max_pages,
        timeframe=timeframe,
        delay=delay,
    ):
        posts.extend(_page_to_json(page)["posts"])
        next_cursor = page.after
        pages_fetched += 1

    return json.dumps(
        {
            "posts": posts,
            "count": len(posts),
            "pages_fetched": pages_fetched,
            "next_cursor": next_cursor,
        }
    )


if __name__ == "__main__":
    mcp.run()
