"""Generate a fresh article payload for LinkedIn without changing the website."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys

if __package__:
    from .generate_blog import call_llm, load_blog_data, pick_topic, slugify
    from .publish_linkedin import ArticleTextParser, PublishError, build_payload
else:
    from generate_blog import call_llm, load_blog_data, pick_topic, slugify
    from publish_linkedin import ArticleTextParser, PublishError, build_payload


def article_html_to_text(body_html: str) -> str:
    parser = ArticleTextParser()
    parser.feed(f'<div class="article-content">{body_html}</div>')
    text = parser.text()
    if not text:
        raise PublishError("Generated article body is empty")
    return text


def generate_linkedin_article(topic: dict) -> dict:
    """Generate a complete LinkedIn-native article that fits in one post."""
    prompt = f"""You write practical communication advice for SpeakingPad, founded by Arpit Gupta.
SpeakingPad helps ambitious Indian students and early-career professionals improve public speaking,
interviews, presentations, and leadership communication.

Write a LinkedIn-native article on: "{topic['title_seed']}"

RULES:
- Write 300-400 words so the complete article fits inside LinkedIn's 3,000-character limit.
- Be direct, useful, conversational, and specific. Avoid generic motivational filler.
- Start with a strong hook; do not start with "In today's world".
- Use 2-3 short subheadings and at least one concise bullet list.
- End with one subtle sentence about improving through structured practice with SpeakingPad.
- Do not say this is a test and do not refer readers to a website article.

Return ONLY valid JSON with these exact keys:
{{
  "title": "a concise polished title",
  "body_html": "the complete article using only <h2>, <p>, <ul>, <li>, <strong>, and <blockquote> tags"
}}"""
    raw = call_llm(prompt).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        article = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PublishError(f"Could not parse the generated LinkedIn article: {exc}") from exc
    if not isinstance(article, dict):
        raise PublishError("Generated LinkedIn article must be a JSON object")
    return article


def generate_test_payload() -> dict:
    """Generate a payload only; no website or repository files are written."""
    existing = load_blog_data()
    topic = pick_topic(existing)
    print(f"🧪 LinkedIn-only topic: {topic['title_seed']}")
    article = generate_linkedin_article(topic)
    title = str(article.get("title", "")).strip()
    body_html = str(article.get("body_html", "")).strip()
    if not title or not body_html:
        raise PublishError("Generated article is missing its title or body")

    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    post = {
        "title": title,
        "slug": slugify(title),
        "date": generated_at,
    }
    payload = build_payload(
        post,
        article_html_to_text(body_html),
        include_article_link=False,
        source="speakingpad-linkedin-test",
    )
    payload["test_only"] = True
    return payload


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Temporary JSON payload destination")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        payload = generate_test_payload()
        args.output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(
            f"✅ Generated LinkedIn-only article '{payload['title']}' "
            f"({len(payload['linkedin_text'])} characters); website files untouched."
        )
        return 0
    except PublishError as exc:
        print(f"❌ LinkedIn-only generation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
