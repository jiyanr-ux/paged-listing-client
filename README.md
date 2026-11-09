# paged-listing-client

A small, typed Python client for reading a public **listing feed** and paging
through it with cursor tokens.

Concretely: it targets Reddit's public read-only JSON API (`/r/<subreddit>`
listings — the front page, a subreddit's hot/new/top feed, etc.). It
authenticates anonymously, so no account, no API key and no browser are
needed, and it returns Python objects instead of raw JSON.

Not affiliated with Reddit. Read-only, public data.

---

## What you get

```python
from api_client import get_listing, iter_pages

page = get_listing("popular", limit=25)
for post in page:
    print(f"[r/{post.subreddit}] {post.title} — {post.score} points")
```

```text
[r/whatdoIdo] My girlfriend is furious about my first tattoo — 8098 points
[r/technology] NVIDIA CEO Admits Infidelity With Suspected Chinese Spy — 28830 points
```

Each `Post` carries: `post_id`, `title`, `subreddit`, `author`, `score`,
`num_comments`, `permalink`, `url`, `is_self`, `created_utc`.

## Install

Requires Python 3.9+.

```bash
pip install -r requirements.txt
```

Only dependency: [`curl_cffi`](https://pypi.org/project/curl_cffi/).

## Quick start

```bash
python api_client.py          # demo: prints two pages of r/popular
python example_usage.py       # writes posts.json next to the script
```

## API reference

### `get_listing(subreddit="popular", sort=None, limit=25, after=None, before=None, timeframe=None) -> Page`

One page of a listing.

| Argument | Meaning |
|---|---|
| `subreddit` | Subreddit name without `r/`, or `"popular"` / `"all"` for the aggregated feeds. |
| `sort` | `"hot"` (default when omitted), `"new"`, `"top"`, `"rising"`, `"controversial"`. |
| `limit` | Posts per page, 1–100. Higher values are clamped. |
| `after` / `before` | Cursor tokens — take them from a previous `Page`. |
| `timeframe` | For `top`/`controversial`: `hour`, `day`, `week`, `month`, `year`, `all`. |

### `iter_pages(subreddit="popular", sort=None, limit=25, max_pages=None, timeframe=None, delay=0.0)`

Generator over successive pages. Stops when Reddit returns no `after` cursor —
which, not an empty page, is the real end of the feed. `delay` sleeps between
pages; pagination is deliberately sequential because the rate limit is
per-client (roughly 60 requests/minute).

### `Page`

| Field | Type | Meaning |
|---|---|---|
| `posts` | `list[Post]` | The parsed submissions on this page. |
| `after` | `str \| None` | Cursor for the next page; `None` on the last page. |
| `before` | `str \| None` | Cursor for the previous page. |

`Page` is iterable and sized, so `for post in page` and `len(page)` work.

### `Post`

| Field | Type | Meaning |
|---|---|---|
| `post_id` | `str` | Post id, e.g. `1xrcy4l`. |
| `title` | `str` | Title, as written (no HTML entities). |
| `subreddit` | `str` | Subreddit name without `r/`. |
| `author` | `str` | Username without `u/`; `"[deleted]"` for removed accounts. |
| `score` | `int` | Upvotes minus downvotes. |
| `num_comments` | `int` | Comment count. |
| `permalink` | `str` | Absolute URL to the post's comment page. |
| `url` | `str` | Outbound link; equals `permalink` for text posts. |
| `is_self` | `bool` | `True` for text posts. |
| `created_utc` | `float` | Unix timestamp (`post.created_iso` gives ISO-8601). |

### Pagination

```python
from api_client import get_listing, iter_pages

# automatic — until the cursor runs out
for page in iter_pages("python", sort="top", timeframe="week", max_pages=3, delay=1.0):
    for post in page:
        print(post.title)

# manual — you keep the cursors
p1 = get_listing("python", limit=25)
p2 = get_listing("python", limit=25, after=p1.after)
p3 = get_listing("python", limit=25, after=p2.after)
```

The cursors are opaque fullnames (e.g. `t3_1xrcy4l`) — pass them back
untouched, never build them yourself.

## How it works

1. `POST https://www.reddit.com/api/v1/access_token` with the public
   installed-app client id as HTTP Basic username (empty password) and grant
   type `https://oauth.reddit.com/grants/installed_client`. That returns a
   Bearer token valid for 24 hours.
2. `GET https://oauth.reddit.com/r/<subreddit>[/<sort>]` with
   `Authorization: Bearer …`, `Accept: application/json` and `raw_json=1`.
3. The token is cached and refreshed automatically 60 seconds before expiry.

**Jargon-free:** one anonymous token request, then plain listing requests.
Nothing else to set up.

## Why `curl_cffi`, and why those headers

Three details decide whether this works at all. Drop any one and you get
`403`, or — more confusingly — **HTTP 200 with an HTML page instead of
JSON**, which looks like success right up to the point it fails to parse:

| Detail | Why |
|---|---|
| `curl_cffi` with `impersonate="chrome131"` | Presents a real Chrome TLS/JA3 fingerprint. Plain `requests`/`urllib` is refused on many networks. |
| `Accept: application/json` | Without it the endpoint answers with the HTML web app. |
| `raw_json=1` | Keeps `&`, `<`, `>` literal in titles instead of HTML entities. |

The client raises `RedditApiError` with the status code and content type when
Reddit answers with HTML, so this failure mode is loud, not silent.

## Configuration

Both are environment variables; the defaults work out of the box.

| Variable | Default | Purpose |
|---|---|---|
| `REDDIT_CLIENT_ID` | a public installed-app id | Use your own app id (register at <https://www.reddit.com/prefs/apps>). |
| `REDDIT_USER_AGENT` | `python:paged_listing_client:v1.0 …` | Reddit's API rules ask for a client-identifying User-Agent. |
| `REDDIT_MIN_INTERVAL` | `0.1` | Minimum seconds between two requests (0.1 = Reddit's 10 req/s ceiling). |

## Tests

Offline unit tests for the payload parser (no network, no credentials):

```bash
python -m unittest discover -s tests -v
```

## Errors and limits

- `RedditApiError` — Reddit returned HTML or a payload that is not a listing.
- `curl_cffi.requests.errors.HTTPError` — non-2xx status; check
  `resp.status_code` (401 = token problem, 403 = fingerprint/headers, 429 =
  rate limit).
- Rate limits: **10 requests per second** is Reddit's ceiling (its documented
  OAuth budget is around 100 requests/minute per client). This client enforces
  a 0.1 s minimum gap on every request, so a plain loop cannot trip the limit;
  raise it via `REDDIT_MIN_INTERVAL` (or `delay=` in `iter_pages`) to stay
  well under. A `429` is retried once, honouring `Retry-After`.
- Scopes: anonymous app-only access is read-only.

## Legal / etiquette

Public, read-only data. Reddit's API terms expect a descriptive `User-Agent`
and a modest request rate — set `REDDIT_USER_AGENT` to something that
identifies you before running this at any volume. For anything beyond light
anonymous reads, register your own app.

## License

MIT — see [LICENSE](LICENSE).
