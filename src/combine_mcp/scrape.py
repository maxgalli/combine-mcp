"""Scrape the cms-talk Statistics category into ``corpora/forum/``.

Writes both ``.txt`` (human-readable) and ``.json`` (structured per-post)
for each topic, plus a ``.manifest.json`` that records ``last_posted_at``
so re-runs only fetch topics whose latest reply changed.

Invoked via the ``combine-mcp scrape`` CLI subcommand. See ``cli.py``.

Auth (one of):
    DISCOURSE_COOKIE
        Browser cookies from a logged-in cms-talk session. How to grab:

        1. Log into https://cms-talk.web.cern.ch via CERN SSO.
        2. Devtools -> Application -> Storage -> Cookies -> cms-talk.
        3. Copy BOTH ``_forum_session`` (the Rails session — authenticates
           API calls) and ``_t`` (remember-me; without it ``_forum_session``
           won't auto-refresh when it expires mid-run).
        4. Export them semicolon-separated::

            export DISCOURSE_COOKIE='_forum_session=<v>; _t=<v>'

        Quote the value in single quotes so the shell doesn't choke on
        ``;`` or ``=``. Cookies expire when the CERN SSO session does
        (typically days to weeks). The manifest is incremental so a
        mid-run 403 just means "re-grab and rerun".

    DISCOURSE_API_KEY + DISCOURSE_USERNAME
        Proper Discourse API key. More durable; survives SSO timeouts.
        Requires admin issuance.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://cms-talk.web.cern.ch"
CATEGORY_PATH = "c/physics/cat/cat-stats"
CATEGORY_ID = 279
DEFAULT_OUTPUT = Path("corpora/forum")
MANIFEST_NAME = ".manifest.json"
POSTS_PER_BATCH = 20  # Discourse batch limit for /t/{id}/posts.json

# Topics to never scrape (meta/admin threads that aren't real Q&A).
SKIP_TOPIC_IDS: frozenset[int] = frozenset({
    19755,  # subscribers list
})


class _HTMLStrip(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._buf: list[str] = []

    def handle_data(self, data: str) -> None:
        self._buf.append(data)

    def text(self) -> str:
        return "".join(self._buf)


def strip_html(html: str) -> str:
    """Render Discourse's HTML 'cooked' field as plain text."""
    s = _HTMLStrip()
    s.feed(html)
    raw = s.text()
    cleaned: list[str] = []
    prev_blank = False
    for ln in (line.strip() for line in raw.splitlines()):
        if ln:
            cleaned.append(ln)
            prev_blank = False
        elif not prev_blank:
            cleaned.append("")
            prev_blank = True
    return "\n".join(cleaned).strip()


def make_client() -> httpx.Client:
    """Build an httpx.Client authenticated against cms-talk.

    Picks ``DISCOURSE_COOKIE`` first, then ``DISCOURSE_API_KEY +
    DISCOURSE_USERNAME``. Raises if neither is set.
    """
    cookie = os.environ.get("DISCOURSE_COOKIE")
    api_key = os.environ.get("DISCOURSE_API_KEY")
    api_username = os.environ.get("DISCOURSE_USERNAME")
    if not cookie and not (api_key and api_username):
        msg = "set DISCOURSE_COOKIE, or DISCOURSE_API_KEY + DISCOURSE_USERNAME"
        raise SystemExit(f"ERROR: {msg}")

    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "combine-mcp-scraper/0.1",
    }
    if cookie:
        headers["Cookie"] = cookie
    else:
        # mypy: api_key / api_username are non-None per the check above.
        headers["Api-Key"] = api_key  # type: ignore[assignment]
        headers["Api-Username"] = api_username  # type: ignore[assignment]
    return httpx.Client(headers=headers, timeout=30.0)


def get_json(
    client: httpx.Client, url: str, sleep_s: float, max_retries: int = 5,
) -> dict[str, Any]:
    """GET ``url`` and return the parsed JSON.

    Treats 404 as "this topic was deleted; skip" (returns ``{}``). Backs off
    exponentially on 429 (rate-limited). Raises for everything else.
    """
    for attempt in range(max_retries):
        r = client.get(url)
        if r.status_code == 429:
            wait = min(60, 2**attempt)
            print(f"  rate-limited, sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        time.sleep(sleep_s)
        return r.json()
    msg = f"too many retries for {url}"
    raise RuntimeError(msg)


def iter_topics(
    client: httpx.Client, sleep_s: float,
) -> Iterator[dict[str, Any]]:
    """Yield topic summary dicts across all pages of the Statistics category."""
    page = 0
    while True:
        url = f"{BASE_URL}/{CATEGORY_PATH}/{CATEGORY_ID}.json?page={page}"
        data = get_json(client, url, sleep_s)
        topic_list = data.get("topic_list") or {}
        topics = topic_list.get("topics") or []
        if not topics:
            return
        yield from topics
        if not topic_list.get("more_topics_url"):
            return
        page += 1


def fetch_all_posts(
    client: httpx.Client, topic_id: int, sleep_s: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return ``(topic_meta, all_posts)`` for one topic.

    Discourse returns only the first ~20 posts in ``/t/{id}.json`` plus an
    ordered list of all post ids in ``post_stream.stream``. For long threads
    we follow that stream and batch-fetch the missing ones.
    """
    data = get_json(client, f"{BASE_URL}/t/{topic_id}.json", sleep_s)
    if not data:
        return {}, []
    stream = (data.get("post_stream") or {}).get("stream") or []
    posts = list((data.get("post_stream") or {}).get("posts") or [])
    have = {p["id"] for p in posts}
    missing = [pid for pid in stream if pid not in have]
    while missing:
        batch, missing = missing[:POSTS_PER_BATCH], missing[POSTS_PER_BATCH:]
        qs = "&".join(f"post_ids[]={pid}" for pid in batch)
        extra = get_json(
            client, f"{BASE_URL}/t/{topic_id}/posts.json?{qs}", sleep_s,
        )
        posts.extend((extra.get("post_stream") or {}).get("posts") or [])
    posts.sort(key=lambda p: p.get("post_number", 0))
    return data, posts


def find_accepted_post_number(
    topic: dict[str, Any], posts: list[dict[str, Any]],
) -> int | None:
    """Resolve the accepted-answer post number from the Discourse Solved plugin.

    Tries the topic-level ``accepted_answer`` blob first, then falls back to
    scanning posts for ``accepted_answer: true``. ``None`` if the topic
    isn't solved (or the plugin isn't active on this category).
    """
    acc = topic.get("accepted_answer")
    if isinstance(acc, dict) and acc.get("post_number"):
        return acc["post_number"]
    for p in posts:
        if p.get("accepted_answer"):
            return p.get("post_number")
    return None


def render_txt(
    topic: dict[str, Any], posts: list[dict[str, Any]],
) -> str:
    """Render a topic as a human-readable transcript."""
    topic_id = topic.get("id")
    accepted = find_accepted_post_number(topic, posts)
    lines = [
        f"TOPIC: {topic.get('title', '')}",
        f"URL: {BASE_URL}/t/{topic_id}",
        f"DATE: {topic.get('created_at', '')}",
        f"SOLVED: {'yes (reply ' + str(accepted) + ')' if accepted else 'no'}",
        "=" * 60,
    ]
    for post in posts:
        n = post.get("post_number", 0)
        if n == 1:
            role = "QUESTION"
        elif n == accepted:
            role = f"REPLY {n} [ACCEPTED ANSWER]"
        else:
            role = f"REPLY {n}"
        lines.append("")
        lines.append(
            f"{role} (by {post.get('username', '?')} "
            f"@ {post.get('created_at', '')}):",
        )
        lines.append(strip_html(post.get("cooked", "")))
        lines.append("-" * 40)
    return "\n".join(lines) + "\n"


def build_json(
    topic: dict[str, Any], posts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Render a topic as a structured per-post JSON object."""
    topic_id = topic.get("id")
    accepted = find_accepted_post_number(topic, posts)
    return {
        "topic_id": topic_id,
        "title": topic.get("title"),
        "url": f"{BASE_URL}/t/{topic_id}",
        "category_id": topic.get("category_id"),
        "created_at": topic.get("created_at"),
        "last_posted_at": topic.get("last_posted_at"),
        "tags": topic.get("tags", []),
        "accepted_answer_post_number": accepted,
        "posts": [
            {
                "post_number": p.get("post_number"),
                "username": p.get("username"),
                "name": p.get("name"),
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
                "reply_to_post_number": p.get("reply_to_post_number"),
                "is_accepted_answer": p.get("post_number") == accepted,
                "text": strip_html(p.get("cooked", "")),
                "cooked_html": p.get("cooked", ""),
            }
            for p in posts
        ],
    }


def load_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {"scraped_at": None, "topics": {}}


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["scraped_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def scrape(
    output_dir: Path,
    *,
    full: bool = False,
    sleep_s: float = 0.5,
    limit: int | None = None,
) -> dict[str, int]:
    """Run one scrape pass.

    Returns ``{"seen": N, "fetched": N, "skipped": N}`` for callers
    (cron wrapper, tests) that want to assert on counts.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / MANIFEST_NAME
    manifest = load_manifest(manifest_path)

    # Drop any skip-listed topics from a prior manifest and their files.
    for tid in SKIP_TOPIC_IDS:
        manifest["topics"].pop(str(tid), None)
        for suffix in (".txt", ".json"):
            f = output_dir / f"topic_{tid}{suffix}"
            if f.exists():
                f.unlink()

    known = {} if full else dict(manifest["topics"])

    seen = fetched = skipped = 0
    with make_client() as client:
        try:
            for summary in iter_topics(client, sleep_s):
                seen += 1
                if limit and seen > limit:
                    break
                topic_id = summary["id"]
                if topic_id in SKIP_TOPIC_IDS:
                    skipped += 1
                    continue
                last_posted_at = (
                    summary.get("last_posted_at")
                    or summary.get("bumped_at")
                )
                key = str(topic_id)
                prior = known.get(key, {}).get("last_posted_at")
                if prior and prior == last_posted_at:
                    skipped += 1
                    continue
                title = (summary.get("title") or "")[:70]
                print(f"[{seen}] topic {topic_id}: {title}")
                topic_meta, posts = fetch_all_posts(
                    client, topic_id, sleep_s,
                )
                if not posts:
                    print("  -> empty or 404, skipping", file=sys.stderr)
                    continue
                merged = {**summary, **topic_meta}
                (output_dir / f"topic_{topic_id}.txt").write_text(
                    render_txt(merged, posts),
                )
                (output_dir / f"topic_{topic_id}.json").write_text(
                    json.dumps(build_json(merged, posts), indent=2),
                )
                manifest["topics"][key] = {
                    "last_posted_at": last_posted_at,
                    "title": summary.get("title"),
                    "post_count": len(posts),
                    "accepted_answer_post_number": find_accepted_post_number(
                        merged, posts,
                    ),
                }
                fetched += 1
                if fetched % 25 == 0:
                    save_manifest(manifest_path, manifest)
        finally:
            save_manifest(manifest_path, manifest)

    print(f"\nDone. seen={seen} fetched={fetched} skipped={skipped}")
    return {"seen": seen, "fetched": fetched, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Invoked by ``combine-mcp scrape``."""
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"output directory (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--full", action="store_true",
        help="rescrape every topic, ignoring the manifest",
    )
    p.add_argument(
        "--sleep", type=float, default=0.5,
        help="seconds between API calls (default: 0.5)",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="stop after N topics (debug)",
    )
    args = p.parse_args(argv)

    scrape(
        output_dir=args.output,
        full=args.full,
        sleep_s=args.sleep,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
