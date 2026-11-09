"""
Offline tests for the payload parser — no network, no credentials.

Run:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from api_client import RedditApiError, parse_listing  # noqa: E402

LISTING = {
    "kind": "Listing",
    "data": {
        "after": "t3_next01",
        "before": None,
        "children": [
            {
                "kind": "t3",
                "data": {
                    "id": "abc123",
                    "title": "Tom & Jerry <b>rule</b>",
                    "subreddit": "python",
                    "author": "someone",
                    "score": 1234,
                    "num_comments": 56,
                    "permalink": "/r/python/comments/abc123/title/",
                    "url": "https://example.com/a?x=1&y=2",
                    "is_self": False,
                    "created_utc": 1750000000.0,
                },
            },
            {"kind": "t3", "data": {"id": "def456", "title": "text post", "is_self": True}},
            {"kind": "more", "data": {"count": 10}},
            {"kind": "t1", "data": {"id": "comment"}},  # comments are skipped
        ],
    },
}


class ParseListingTests(unittest.TestCase):
    def test_parses_posts_and_cursors(self) -> None:
        page = parse_listing(LISTING)

        self.assertEqual(len(page), 2)              # only t3 children
        self.assertEqual(page.after, "t3_next01")   # opaque cursor, untouched
        self.assertIsNone(page.before)
        self.assertEqual([p.post_id for p in page], ["abc123", "def456"])

    def test_absolute_permalink_is_built(self) -> None:
        post = parse_listing(LISTING).posts[0]
        self.assertEqual(
            post.permalink, "https://www.reddit.com/r/python/comments/abc123/title/"
        )
        self.assertEqual(post.url, "https://example.com/a?x=1&y=2")
        self.assertFalse(post.is_self)
        self.assertEqual(post.score, 1234)
        self.assertEqual(post.num_comments, 56)

    def test_missing_fields_get_safe_defaults(self) -> None:
        post = parse_listing(LISTING).posts[1]
        self.assertEqual(post.author, "[deleted]")
        self.assertEqual(post.score, 0)
        self.assertEqual(post.num_comments, 0)
        self.assertEqual(post.created_utc, 0.0)
        self.assertEqual(post.permalink, "")
        self.assertTrue(post.is_self)

    def test_created_iso_is_utc(self) -> None:
        self.assertTrue(parse_listing(LISTING).posts[0].created_iso.startswith("2025-06-15"))

    def test_page_is_iterable_and_sized(self) -> None:
        page = parse_listing(LISTING)
        self.assertEqual([p.post_id for p in page], ["abc123", "def456"])
        self.assertEqual(len(page), len(page.posts))

    def test_empty_listing_is_the_end_of_feed(self) -> None:
        page = parse_listing({"kind": "Listing", "data": {"children": [], "after": None}})
        self.assertEqual(len(page), 0)
        self.assertIsNone(page.after)

    def test_non_listing_payload_raises(self) -> None:
        with self.assertRaises(RedditApiError):
            parse_listing({"message": "Forbidden", "error": 403})
        with self.assertRaises(RedditApiError):
            parse_listing("<html>web app</html>")


if __name__ == "__main__":
    unittest.main()
