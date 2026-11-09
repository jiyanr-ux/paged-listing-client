"""
Paged listing client for Reddit's public JSON API
=================================================

A small, typed, dependency-light client for reading a public listing feed
(the front page, a subreddit, or a user's submissions) and paging through it
with Reddit's cursor tokens.

What it does
------------
* Authenticates anonymously (app-only OAuth, ``installed_client`` grant) — no
  account, no client secret, no browser. The token is cached and refreshed
  automatically before it expires.
* Returns typed ``Post`` objects: title, subreddit, author, score, comment
  count and permalink.
* Pages with reddit's opaque ``after`` cursor (``iter_pages``) and stops on
  the cursor running out, not on an empty page.

Requirements
------------
    pip install -r requirements.txt      # curl_cffi

`curl_cffi` is required, not optional: Reddit refuses stdlib clients
(`urllib`, plain `requests`) from many networks. See *Why curl_cffi* in
README.md.

Quick start
-----------
    python api_client.py                 # demo: two pages of r/popular

    from api_client import get_listing, iter_pages

    page = get_listing("popular", limit=25)
    for post in page.posts:
        print(post.subreddit, post.title, post.score)

License: MIT (see LICENSE).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Iterator, Optional

# curl_cffi is imported as `requests` on purpose: its API mirrors
# `requests`, with the addition of the `impersonate=` argument.
import curl_cffi.requests as requests

# Windows terminals often default to cp1252, which cannot encode the emoji and
# non-Latin scripts that show up in real post titles.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-16"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Public "installed app" client id — not a secret, and no redirect URI or user
# login is attached to it. Override with REDDIT_CLIENT_ID if you registered
# your own app at https://www.reddit.com/prefs/apps.
CLIENT_ID: str = os.environ.get("REDDIT_CLIENT_ID", "ohXpoqrZYub1kg")

# Reddit's API rules ask for a User-Agent that identifies the client. Set
# REDDIT_USER_AGENT to your own before heavy use.
USER_AGENT: str = os.environ.get(
    "REDDIT_USER_AGENT", "python:paged_listing_client:v1.0 (by /u/unknown)"
)

TOKEN_URL: str = "https://www.reddit.com/api/v1/access_token"
OAUTH_BASE: str = "https://oauth.reddit.com"

# TLS/JA3 fingerprint to present. This is what gets the request past Reddit's
# bot protection; see README.md.
TLS_IMPERSONATE: str = "chrome131"

# Refresh the token this many seconds before it actually expires.
TOKEN_SAFETY_MARGIN: int = 60

# Hard floor on the gap between two HTTP requests. Reddit allows at most
# 10 requests per second, so the default is exactly that ceiling; every
# request in this module goes through _throttle() so a caller looping over
# listings cannot trip the limit. Raise it (REDDIT_MIN_INTERVAL) to be gentler.
MIN_REQUEST_INTERVAL: float = float(os.environ.get("REDDIT_MIN_INTERVAL", "0.1"))

# Wait this long after a 429 when the response carries no Retry-After header,
# then try the request once more.
DEFAULT_RETRY_AFTER: float = 5.0
MAX_RETRY_AFTER: float = 60.0

_throttle_lock = threading.Lock()
_last_request_at: float = 0.0


def _throttle() -> None:
    """Block until at least MIN_REQUEST_INTERVAL has passed since the last call."""
    global _last_request_at
    with _throttle_lock:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _forbidden_hint(url: str) -> str:
    """Explain a 403 in terms of what actually works anonymously."""
    return (
        f"Reddit refused this request (403): {url}\n"
        "With anonymous app-only access the cached listings work — hot, top, "
        "controversial, and /r/popular — while 'new', 'rising' and /r/all are "
        "refused. Use one of the working sorts, or register your own app at "
        "https://www.reddit.com/prefs/apps and set REDDIT_CLIENT_ID. "
        "See README.md -> 'What works anonymously'."
    )


def _send(method: str, url: str, **kwargs):
    """
    Perform one HTTP request at the throttled rate, retrying once on 429.

    Reddit answers 429 (Too Many Requests) when the per-client budget is
    exhausted; the response may carry ``Retry-After`` in seconds. One retry
    covers the common burst case without hiding a real block. A 403 is raised
    as :class:`RedditApiError` with an explanation, because for anonymous
    clients it is about the endpoint, not about how often you ask.

    Raises:
        RedditApiError: on 403 (endpoint not available anonymously).
        curl_cffi.requests.errors.HTTPError: on any other non-2xx status.
    """
    for attempt in (1, 2):
        _throttle()
        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 429 and attempt == 1:
            header = resp.headers.get("retry-after")
            try:
                wait = float(header) if header else DEFAULT_RETRY_AFTER
            except (TypeError, ValueError):
                wait = DEFAULT_RETRY_AFTER
            time.sleep(max(0.0, min(wait, MAX_RETRY_AFTER)))
            continue
        if resp.status_code == 403:
            raise RedditApiError(_forbidden_hint(url))
        resp.raise_for_status()
        return resp

    # Not reachable: the second attempt either returns above or raises a
    # 429-carrying HTTPError from raise_for_status().
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Post:
    """One submission (a Reddit "t3" object), flattened to the useful fields."""

    post_id: str
    title: str
    subreddit: str
    author: str
    score: int
    num_comments: int
    permalink: str        # absolute URL: https://www.reddit.com/r/.../comments/...
    url: str              # link target; equals permalink for self/text posts
    is_self: bool
    created_utc: float    # unix timestamp

    @property
    def created_iso(self) -> str:
        """Post creation time as an ISO-8601 UTC string."""
        from datetime import datetime, timezone

        return datetime.fromtimestamp(self.created_utc, tz=timezone.utc).isoformat()


@dataclass
class Page:
    """One page of a listing, plus the cursors to move around it."""

    posts: list[Post]
    after: Optional[str]     # cursor for the next page; None on the last page
    before: Optional[str]    # cursor for the previous page

    def __iter__(self) -> Iterator[Post]:
        return iter(self.posts)

    def __len__(self) -> int:
        return len(self.posts)


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


@dataclass
class _TokenState:
    access_token: str = ""
    expires_at: float = 0.0     # unix timestamp


_token_state: _TokenState = _TokenState()


class RedditApiError(RuntimeError):
    """Raised when Reddit answers with something that is not a listing."""


def _fetch_token() -> tuple[str, int]:
    """
    Request a fresh anonymous (app-only) access token.

    Uses the ``installed_client`` grant: the public client id as HTTP Basic
    username, empty password, no secret, no user consent screen.

    Returns:
        ``(access_token, expires_in_seconds)``

    Raises:
        curl_cffi.requests.errors.RequestsError / HTTPError: on network or
            HTTP failure.
    """
    resp = _send(
        "POST",
        TOKEN_URL,
        auth=(CLIENT_ID, ""),
        data={
            "grant_type": "https://oauth.reddit.com/grants/installed_client",
            "device_id": "DO_NOT_TRACK_THIS_DEVICE",
        },
        headers={"User-Agent": USER_AGENT},
        impersonate=TLS_IMPERSONATE,
        timeout=30,
    )
    payload = resp.json()
    return payload["access_token"], int(payload.get("expires_in", 86400))


def _get_valid_token() -> str:
    """Return a live access token, fetching a new one when needed."""
    if time.time() >= _token_state.expires_at - TOKEN_SAFETY_MARGIN:
        token, expires_in = _fetch_token()
        _token_state.access_token = token
        _token_state.expires_at = time.time() + expires_in
    return _token_state.access_token


def _make_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_valid_token()}",
        "User-Agent": USER_AGENT,
        # Load-bearing: without an explicit JSON Accept header Reddit replies
        # HTTP 200 with the HTML web app, which parses as garbage.
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def get_listing(
    subreddit: str = "popular",
    sort: Optional[str] = None,
    limit: int = 25,
    after: Optional[str] = None,
    before: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> Page:
    """
    Fetch one page of a public listing.

    Args:
        subreddit: Subreddit name without ``r/``, or ``"popular"`` / ``"all"``
            for the aggregated front-page feeds.
        sort: ``"hot"`` (default when omitted), ``"new"``, ``"top"``,
            ``"rising"`` or ``"controversial"``.
            Anonymous app-only access serves ``hot``, ``top`` and
            ``controversial``; ``new`` and ``rising`` come back as 403 (see
            ``README.md -> "What works anonymously"``).
        limit: Posts per page, 1–100. Reddit clamps anything higher.
        after: Cursor for the *next* page — take ``Page.after`` from the
            previous page.
        before: Cursor for the *previous* page.
        timeframe: Only for ``sort="top"`` / ``"controversial"``:
            ``hour``, ``day``, ``week``, ``month``, ``year`` or ``all``.

    Returns:
        A :class:`Page` of typed posts with the next/previous cursors.

    Raises:
        RedditApiError: Reddit answered with HTML or a non-listing payload.
        HTTPError: Reddit answered with a 4xx/5xx status.

    Example:
        >>> page = get_listing("python", sort="top", timeframe="week", limit=10)
        >>> page.posts[0].title
    """
    path = f"/r/{subreddit.strip('/')}"
    if sort:
        path += f"/{sort}"

    params: dict[str, object] = {"limit": max(1, min(limit, 100)), "raw_json": 1}
    if after:
        params["after"] = after
    if before:
        params["before"] = before
    if timeframe:
        params["t"] = timeframe

    resp = _send(
        "GET",
        f"{OAUTH_BASE}{path}",
        headers=_make_headers(),
        params=params,
        impersonate=TLS_IMPERSONATE,
        timeout=30,
    )
    return parse_listing(_decode_json(resp))


def get_popular_posts(
    limit: int = 25,
    after: Optional[str] = None,
    before: Optional[str] = None,
) -> Page:
    """Convenience wrapper: one page of r/popular."""
    return get_listing("popular", limit=limit, after=after, before=before)


def _decode_json(resp) -> dict:
    """
    Decode a response as JSON, with a clear error when Reddit served HTML.

    Reddit answers the web app HTML with status 200 when it does not like the
    request (missing ``Accept`` header, wrong TLS fingerprint, login wall), so
    a parse failure here is a real diagnostic, not a bug.
    """
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        ctype = resp.headers.get("content-type", "unknown")
        raise RedditApiError(
            "Reddit did not return JSON "
            f"(status {resp.status_code}, content-type {ctype!r}). "
            "Check that curl_cffi is used with impersonate=TLS_IMPERSONATE "
            "and that the request sends 'Accept: application/json'."
        ) from exc


def parse_listing(raw: dict) -> Page:
    """
    Convert a raw listing payload into a typed :class:`Page`.

    Kept public so it can be unit-tested without network access, and reused
    on cached payloads.

    Raises:
        RedditApiError: if ``raw`` is not a ``Listing`` object.
    """
    if not isinstance(raw, dict) or raw.get("kind") != "Listing":
        raise RedditApiError(f"Expected a Listing payload, got {type(raw).__name__}")

    data = raw.get("data") or {}
    posts: list[Post] = []

    for child in data.get("children") or []:
        if not isinstance(child, dict) or child.get("kind") != "t3":
            continue  # ads, "more" placeholders and other non-post children
        d = child.get("data") or {}
        permalink = d.get("permalink") or ""
        posts.append(
            Post(
                post_id=d.get("id", ""),
                title=d.get("title", ""),
                subreddit=d.get("subreddit", ""),
                author=d.get("author", "[deleted]"),
                score=int(d.get("score", 0) or 0),
                num_comments=int(d.get("num_comments", 0) or 0),
                permalink=(
                    f"https://www.reddit.com{permalink}" if permalink else ""
                ),
                url=d.get("url", ""),
                is_self=bool(d.get("is_self", False)),
                created_utc=float(d.get("created_utc", 0.0) or 0.0),
            )
        )

    return Page(posts=posts, after=data.get("after"), before=data.get("before"))


def iter_pages(
    subreddit: str = "popular",
    sort: Optional[str] = None,
    limit: int = 25,
    max_pages: Optional[int] = None,
    timeframe: Optional[str] = None,
    delay: float = 0.0,
) -> Iterator[Page]:
    """
    Yield successive pages until the cursor runs out.

    Args:
        subreddit: see :func:`get_listing`.
        sort: see :func:`get_listing`.
        limit: posts per page.
        max_pages: stop after this many pages; ``None`` walks the whole feed.
        timeframe: see :func:`get_listing`.
        delay: extra seconds to sleep between pages on top of the built-in
            per-request throttle (Reddit's ceiling is 10 requests/second).

    Yields:
        :class:`Page` objects in forward order. Stops when Reddit returns no
        ``after`` cursor, which — not an empty page — is the real end of feed.
    """
    after: Optional[str] = None
    pages = 0

    while max_pages is None or pages < max_pages:
        page = get_listing(
            subreddit, sort=sort, limit=limit, after=after, timeframe=timeframe
        )
        if not page.posts:
            break

        yield page
        pages += 1
        after = page.after

        if not after:
            break
        if delay:
            time.sleep(delay)


def iter_popular(limit: int = 25, max_pages: Optional[int] = None) -> Iterator[Page]:
    """Convenience wrapper: successive pages of r/popular."""
    return iter_pages("popular", limit=limit, max_pages=max_pages)


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------


def main() -> None:
    """Print the first two pages of r/popular."""
    print("Fetching r/popular...\n")
    total = 0

    for page_num, page in enumerate(iter_pages("popular", limit=25, max_pages=2), 1):
        print(f"{'=' * 62}")
        print(f"  page {page_num}: {len(page)} posts   after={page.after!r}")
        print(f"{'=' * 62}")
        for post in page:
            print(f"[r/{post.subreddit}] {post.title[:70]}")
            print(
                f"  u/{post.author}  score={post.score}  "
                f"comments={post.num_comments}"
            )
            print(f"  {post.permalink}\n")
        total += len(page)

    print(f"total posts fetched: {total}")


if __name__ == "__main__":
    main()
