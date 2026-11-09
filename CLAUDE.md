# Working in this repo (notes for Claude Code)

`api_client.py` is a self-contained, **standard-library-only** client for
Reddit's public read-only JSON API. No install step, no API key.

## Run it

```bash
python api_client.py        # prints two pages of r/popular
python example_usage.py     # writes posts.json
python -m unittest discover -s tests -v
```

## Use it from another script

```python
from api_client import get_listing, iter_pages

page = get_listing("popular", limit=25)          # or a subreddit
for post in page:
    print(post.title, post.score, post.permalink)

for page in iter_pages("python", sort="top", timeframe="week", max_pages=3):
    for post in page:
        print(post.permalink, post.num_comments)
```

`Post` fields: `post_id, title, subreddit, author, score, num_comments,
permalink, url, is_self, created_utc` (+ `created_iso`).

To import it from another directory in the same interpreter:
`pip install -e /path/to/paged-listing-client`.

## Things that matter here — do not "fix" these

- **`Accept: application/json` is load-bearing.** Reddit answers HTTP 200 with
  the HTML web app without it. This is the #1 cause of "why doesn't it parse".
- **Do not add TLS/JA3 impersonation.** A `curl_cffi` + `impersonate="chrome131"`
  version was the original generator output; it works, but plain `urllib`
  works identically. The dependency is not needed and was removed on purpose.
- **`new`, `rising` and `/r/all` return 403** with anonymous app-only access —
  that is the endpoint, not the code, and pacing does not change it. `hot`,
  `top`, `controversial` and `/r/popular` work. A 403 raises `RedditApiError`
  saying exactly this.
- **Rate limit: 10 requests/second max.** Every request is throttled inside
  `_send()`; keep it that way, and paginate sequentially.
- **Read-only, public data.** Never add posting/voting/moderation calls.
