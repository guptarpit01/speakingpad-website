import json
import unittest
from unittest import mock
import urllib.error

from scripts import generate_blog


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class OpenRouterFallbackTests(unittest.TestCase):
    def test_credit_error_falls_back_to_free_router(self):
        credit_error = urllib.error.HTTPError(
            generate_blog.OPENROUTER_URL,
            402,
            "Payment Required",
            hdrs=None,
            fp=None,
        )
        credit_error.read = mock.Mock(return_value=b'{"error":{"message":"more credits required"}}')
        fallback_response = FakeResponse(
            {"choices": [{"message": {"content": '{"title":"Recovered"}'}}]}
        )

        with (
            mock.patch.object(generate_blog, "OPENROUTER_API_KEY", "test-key"),
            mock.patch.object(generate_blog, "OPENROUTER_PRIMARY_MODEL", "paid-model"),
            mock.patch.object(generate_blog, "OPENROUTER_FALLBACK_MODEL", "openrouter/free"),
            mock.patch("urllib.request.urlopen", side_effect=[credit_error, fallback_response]) as urlopen,
        ):
            content = generate_blog.call_llm("Write an article")

        self.assertEqual(content, '{"title":"Recovered"}')
        models = [json.loads(call.args[0].data)["model"] for call in urlopen.call_args_list]
        self.assertEqual(models, ["paid-model", "openrouter/free"])

    def test_request_stays_within_affordable_output_limit_and_requests_json(self):
        response = FakeResponse({"choices": [{"message": {"content": "{}"}}]})
        with (
            mock.patch.object(generate_blog, "OPENROUTER_API_KEY", "test-key"),
            mock.patch.object(generate_blog, "OPENROUTER_PRIMARY_MODEL", "paid-model"),
            mock.patch.object(generate_blog, "OPENROUTER_FALLBACK_MODEL", "paid-model"),
            mock.patch("urllib.request.urlopen", return_value=response) as urlopen,
        ):
            generate_blog.call_llm("Write an article")

        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["max_tokens"], 3000)
        self.assertEqual(payload["response_format"], {"type": "json_object"})


class SlugTests(unittest.TestCase):
    def test_duplicate_title_gets_date_suffix_instead_of_overwriting_old_post(self):
        existing = [{"slug": "how-to-speak-clearly", "date": "2026-08-01"}]

        slug = generate_blog.unique_slug("How to Speak Clearly", "2026-09-13", existing)

        self.assertEqual(slug, "how-to-speak-clearly-2026-09-13")

    def test_new_title_keeps_clean_slug(self):
        slug = generate_blog.unique_slug("A Fresh Topic", "2026-09-13", [])

        self.assertEqual(slug, "a-fresh-topic")


if __name__ == "__main__":
    unittest.main()
