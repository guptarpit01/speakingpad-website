import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

from scripts import generate_linkedin_test
from scripts.generate_linkedin_test import article_html_to_text
from scripts.publish_linkedin import (
    ArticleTextParser,
    LINKEDIN_CHARACTER_LIMIT,
    PublishError,
    build_payload,
    build_post_text,
    deliver_payload,
    load_article,
    load_payload,
)


class FakeResponse:
    def __init__(self, status=200, body=b"Accepted"):
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self):
        return self.status

    def read(self, _limit=-1):
        return self.body


class ArticleTextParserTests(unittest.TestCase):
    def test_extracts_only_article_content_and_formats_blocks(self):
        parser = ArticleTextParser()
        parser.feed(
            '<nav>Ignore me</nav><div class="article-content reveal">'
            '<h2>Why it matters</h2><p>Clear &amp; useful.<br>Still inside.</p>'
            '<ul><li>First <strong>point</strong></li><li>Second point</li></ul>'
            '</div><footer>Ignore me too</footer>'
        )

        self.assertEqual(
            parser.text(),
            "WHY IT MATTERS\n\nClear & useful.\nStill inside.\n\n• First point\n\n• Second point",
        )

    def test_converts_generated_body_without_a_website_file(self):
        self.assertEqual(
            article_html_to_text("<h2>Fresh idea</h2><p>LinkedIn-only body.</p>"),
            "FRESH IDEA\n\nLinkedIn-only body.",
        )


class PayloadTests(unittest.TestCase):
    def test_post_text_reserves_space_for_url_and_never_exceeds_limit(self):
        article_url = "https://speakingpad.in/posts/example.html"
        text = build_post_text("A useful title", "word " * 1000, article_url)

        self.assertLessEqual(len(text), LINKEDIN_CHARACTER_LIMIT)
        self.assertTrue(text.endswith(article_url))
        self.assertIn("...\n\n🔗 Read the full article here:", text)

    def test_payload_preserves_make_field_names_and_adds_stable_event_id(self):
        post = {"slug": "example", "title": "Example", "date": "2026-08-30"}
        first = build_payload(post, "Useful article text")
        second = build_payload(post, "Useful article text")

        self.assertEqual(first["event_id"], second["event_id"])
        self.assertIn("linkedin_text", first)
        self.assertEqual(first["article_url"], "https://speakingpad.in/posts/example.html")

    def test_linkedin_only_payload_has_no_unpublished_article_link(self):
        post = {"slug": "test", "title": "Test Article", "date": "2026-08-30T06:00:00Z"}
        payload = build_payload(
            post,
            "Useful standalone article text",
            include_article_link=False,
            source="speakingpad-linkedin-test",
        )

        self.assertEqual(payload["article_url"], "")
        self.assertNotIn("Read the full article here", payload["linkedin_text"])
        self.assertEqual(payload["source"], "speakingpad-linkedin-test")

    def test_load_payload_validates_prebuilt_test_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "payload.json"
            payload_path.write_text(
                json.dumps({"title": "Test", "linkedin_text": "Body", "event_id": "event-1"}),
                encoding="utf-8",
            )

            payload = load_payload(payload_path)

        self.assertEqual(payload["title"], "Test")

    def test_test_generation_builds_payload_without_writing_website_files(self):
        generated_article = {
            "title": "A Fresh LinkedIn Article",
            "body_html": "<h2>A practical idea</h2><p>Useful standalone guidance.</p>",
        }
        with (
            mock.patch.object(generate_linkedin_test, "load_blog_data", return_value=[]),
            mock.patch.object(generate_linkedin_test, "pick_topic", return_value={"title_seed": "Fresh idea"}),
            mock.patch.object(generate_linkedin_test, "generate_linkedin_article", return_value=generated_article),
        ):
            payload = generate_linkedin_test.generate_test_payload()

        self.assertTrue(payload["test_only"])
        self.assertEqual(payload["article_url"], "")
        self.assertEqual(payload["source"], "speakingpad-linkedin-test")

    def test_load_article_selects_latest_entry_and_rendered_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            posts = root / "posts"
            posts.mkdir()
            metadata = [
                {"slug": "old", "title": "Old", "date": "2026-08-29"},
                {"slug": "new", "title": "New", "date": "2026-08-30"},
            ]
            (root / "blog-data.json").write_text(json.dumps(metadata), encoding="utf-8")
            (posts / "new.html").write_text(
                '<div class="article-content reveal"><p>Newest body</p></div>',
                encoding="utf-8",
            )

            post, article_text = load_article(root / "blog-data.json", posts)

        self.assertEqual(post["slug"], "new")
        self.assertEqual(article_text, "Newest body")


class DeliveryTests(unittest.TestCase):
    def test_accepts_make_success_response(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            status, body = deliver_payload(
                "https://hook.example.make.com/abc",
                {"event_id": "event-1", "linkedin_text": "text"},
                attempts=1,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body, "Accepted")

    def test_raises_on_http_error_instead_of_masking_it(self):
        error = urllib.error.HTTPError(
            "https://hook.example.make.com/abc",
            410,
            "Gone",
            hdrs=None,
            fp=None,
        )
        error.read = mock.Mock(return_value=b"Scenario is inactive")
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(PublishError, "HTTP 410"):
                deliver_payload(
                    "https://hook.example.make.com/abc",
                    {"event_id": "event-1", "linkedin_text": "text"},
                    attempts=1,
                )

    def test_raises_when_success_status_contains_failure_body(self):
        response = FakeResponse(body=b'{"success": false, "error": "LinkedIn auth expired"}')
        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(PublishError, "reported a failure"):
                deliver_payload(
                    "https://hook.example.make.com/abc",
                    {"event_id": "event-1", "linkedin_text": "text"},
                    attempts=1,
                )


if __name__ == "__main__":
    unittest.main()
