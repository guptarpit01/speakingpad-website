"""Publish the latest generated SpeakingPad article through the Make webhook.

This script is intentionally separate from article generation. The workflow
commits the website first, then runs this publisher so a LinkedIn integration
failure is visible without preventing the blog from going live.
"""

from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BLOG_DATA = REPO_ROOT / "blog-data.json"
DEFAULT_POSTS_DIR = REPO_ROOT / "posts"
ARTICLE_BASE_URL = "https://speakingpad.in/posts"
LINKEDIN_CHARACTER_LIMIT = 3000
TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


class PublishError(RuntimeError):
    """Raised when a LinkedIn payload cannot be built or delivered."""


class ArticleTextParser(HTMLParser):
    """Extract readable text only from the generated article-content div."""

    BLOCK_TAGS = {"p", "ol", "ul", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.heading_depth = 0
        self.parts: list[str] = []

    @property
    def in_article(self) -> bool:
        return self.depth > 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if not self.in_article:
            classes = dict(attrs).get("class", "") or ""
            if tag == "div" and "article-content" in classes.split():
                self.depth = 1
            return

        if tag in self.VOID_TAGS:
            if tag in {"br", "hr"}:
                self.parts.append("\n")
            return

        self.depth += 1
        if tag in self.HEADING_TAGS:
            self.heading_depth += 1
            self.parts.append("\n\n")
        elif tag == "li":
            self.parts.append("\n• ")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_article:
            return

        tag = tag.lower()
        if tag in self.HEADING_TAGS:
            self.heading_depth = max(0, self.heading_depth - 1)
        if tag in self.BLOCK_TAGS or tag == "li":
            self.parts.append("\n\n")

        self.depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.in_article:
            return
        self.parts.append(data.upper() if self.heading_depth else data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.split("\n")]
        normalized = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def load_article(
    blog_data_path: Path = DEFAULT_BLOG_DATA,
    posts_dir: Path = DEFAULT_POSTS_DIR,
    date: str | None = None,
) -> tuple[dict, str]:
    """Load the newest article metadata and its rendered article text."""
    try:
        data = json.loads(blog_data_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublishError(f"Blog metadata not found: {blog_data_path}") from exc
    except json.JSONDecodeError as exc:
        raise PublishError(f"Blog metadata is invalid JSON: {exc}") from exc

    if not isinstance(data, list) or not data:
        raise PublishError("Blog metadata contains no articles")

    candidates = [post for post in data if not date or post.get("date") == date]
    if not candidates:
        raise PublishError(f"No article found for {date}")

    # Position is a stable tie-breaker when more than one entry has a date.
    indexed = list(enumerate(candidates))
    _, post = max(indexed, key=lambda item: (item[1].get("date", ""), item[0]))
    title = str(post.get("title", "")).strip()
    slug = str(post.get("slug", "")).strip()
    if not title or not slug:
        raise PublishError("Latest article is missing its title or slug")

    post_path = posts_dir / f"{slug}.html"
    try:
        rendered_html = post_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PublishError(f"Rendered article not found: {post_path}") from exc

    parser = ArticleTextParser()
    parser.feed(rendered_html)
    article_text = parser.text()
    if not article_text:
        raise PublishError(f"Could not find article content in {post_path}")
    return post, article_text


def build_post_text(title: str, article_text: str, article_url: str) -> str:
    """Build a complete post that always stays within LinkedIn's limit."""
    prefix = f"📢 {title.upper()}\n\n"
    suffix = f"\n\n🔗 Read the full article here: {article_url}"
    available = LINKEDIN_CHARACTER_LIMIT - len(prefix) - len(suffix)
    if available < 80:
        raise PublishError("Article title and URL leave too little room for post content")

    content = article_text.strip()
    if len(content) > available:
        cutoff = max(0, available - 3)
        shortened = content[:cutoff].rsplit(" ", 1)[0].rstrip()
        if not shortened:
            shortened = content[:cutoff].rstrip()
        content = f"{shortened}..."

    post_text = f"{prefix}{content}{suffix}"
    if len(post_text) > LINKEDIN_CHARACTER_LIMIT:
        raise PublishError("LinkedIn post exceeds the 3,000-character limit")
    return post_text


def build_payload(post: dict, article_text: str) -> dict:
    """Build the backward-compatible Make payload with diagnostic metadata."""
    slug = str(post["slug"])
    title = str(post["title"])
    article_url = f"{ARTICLE_BASE_URL}/{slug}.html"
    event_source = f"{post.get('date', '')}:{slug}"
    event_id = hashlib.sha256(event_source.encode("utf-8")).hexdigest()[:24]
    return {
        "title": title,
        "linkedin_text": build_post_text(title, article_text, article_url),
        "article_url": article_url,
        "published_date": post.get("date", ""),
        "event_id": event_id,
        "source": "speakingpad-daily-blog",
    }


def _validate_webhook_url(webhook_url: str) -> None:
    parsed = urllib.parse.urlparse(webhook_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise PublishError("MAKE_WEBHOOK_URL must be a valid HTTPS URL")


def _response_reports_failure(body: str) -> bool:
    """Detect explicit failure responses while accepting Make's plain 'Accepted'."""
    if not body.strip():
        return False
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return bool(re.search(r"\b(error|failed|failure|inactive|disabled)\b", body, re.IGNORECASE))
    if not isinstance(parsed, dict):
        return False
    if parsed.get("success") is False or parsed.get("ok") is False:
        return True
    return str(parsed.get("status", "")).lower() in {"error", "failed", "failure"}


def deliver_payload(webhook_url: str, payload: dict, attempts: int = 3) -> tuple[int, str]:
    """Deliver a payload, retrying only transient network and server failures."""
    _validate_webhook_url(webhook_url)
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "SpeakingPad-LinkedIn-Publisher/1.0",
        "X-SpeakingPad-Event-Id": str(payload["event_id"]),
    }

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(webhook_url, data=encoded, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.getcode()
                body = response.read(4096).decode("utf-8", errors="replace").strip()
                if not 200 <= status < 300:
                    raise PublishError(f"Make webhook returned HTTP {status}: {body or 'empty response'}")
                if _response_reports_failure(body):
                    raise PublishError(f"Make webhook reported a failure: {body}")
                return status, body
        except urllib.error.HTTPError as exc:
            body = exc.read(4096).decode("utf-8", errors="replace").strip()
            message = f"Make webhook returned HTTP {exc.code}: {body or exc.reason}"
            if exc.code not in TRANSIENT_HTTP_CODES or attempt == attempts:
                raise PublishError(message) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            message = f"Make webhook could not be reached: {exc.reason if hasattr(exc, 'reason') else exc}"
            if attempt == attempts:
                raise PublishError(message) from exc

        time.sleep(2 ** (attempt - 1))

    raise PublishError("Make webhook delivery failed")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Publish the latest article on this YYYY-MM-DD date")
    parser.add_argument("--blog-data", type=Path, default=DEFAULT_BLOG_DATA)
    parser.add_argument("--posts-dir", type=Path, default=DEFAULT_POSTS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    webhook_url = os.environ.get("MAKE_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("❌ MAKE_WEBHOOK_URL is not configured; LinkedIn publishing cannot run.", file=sys.stderr)
        return 1

    try:
        post, article_text = load_article(args.blog_data, args.posts_dir, args.date)
        payload = build_payload(post, article_text)
        print(
            f"💼 Publishing '{post['title']}' to LinkedIn "
            f"({len(payload['linkedin_text'])}/{LINKEDIN_CHARACTER_LIMIT} characters)..."
        )
        status, body = deliver_payload(webhook_url, payload)
        response_summary = body[:300] if body else "empty response"
        print(f"✅ Make webhook accepted the LinkedIn post (HTTP {status}: {response_summary})")
        return 0
    except PublishError as exc:
        print(f"❌ LinkedIn publishing failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
