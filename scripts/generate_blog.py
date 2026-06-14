"""
generate_blog.py — Automated daily blog article generator for SpeakingPad.

This script:
1. Picks a topic from a curated pool (avoiding recent repeats).
2. Calls the Gemini API to generate an article in SpeakingPad's tone.
3. Renders the article HTML from the post-template.html template.
4. Updates blog-data.json with the new article's metadata.

Usage (local):
  export GEMINI_API_KEY="your-key"
  python scripts/generate_blog.py

Usage (GitHub Actions):
  Called automatically by .github/workflows/daily-blog.yml
"""

import json
import os
import re
import sys
import random
import datetime
import urllib.request
import urllib.error

# ─── Config ───────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS_DIR = os.path.join(REPO_ROOT, "posts")
TEMPLATE_PATH = os.path.join(REPO_ROOT, "post-template.html")
BLOG_DATA_PATH = os.path.join(REPO_ROOT, "blog-data.json")
SITEMAP_PATH = os.path.join(REPO_ROOT, "sitemap.xml")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = "google/gemini-2.0-flash-001"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ─── Topic Pool ───────────────────────────────────────────────────────────────
TOPICS = [
    {"title_seed": "How to Overcome Stage Fear Before a Big Presentation", "tag": "Public Speaking"},
    {"title_seed": "5 Body Language Mistakes That Kill Your Confidence on Stage", "tag": "Body Language"},
    {"title_seed": "How MBA Students Can Ace Group Discussions With Structured Communication", "tag": "Group Discussions"},
    {"title_seed": "Why Most Self-Introductions Fail in Interviews (And How to Fix Yours)", "tag": "Interviews"},
    {"title_seed": "The 3-Part Framework for Delivering a Memorable Speech", "tag": "Public Speaking"},
    {"title_seed": "How to Sound Confident When You Don't Feel It", "tag": "Confidence"},
    {"title_seed": "Communication Habits That Get You Promoted Faster", "tag": "Career Growth"},
    {"title_seed": "How to Structure Your Thoughts Before Speaking in Meetings", "tag": "Workplace Communication"},
    {"title_seed": "Why Filler Words Destroy Your Credibility (And How to Eliminate Them)", "tag": "Speech Skills"},
    {"title_seed": "How to Tell a Compelling Story in a Business Presentation", "tag": "Storytelling"},
    {"title_seed": "What Makes Executive Presence Different From Confidence", "tag": "Leadership"},
    {"title_seed": "How to Prepare for Campus Placements: A Communication-First Approach", "tag": "Placements"},
    {"title_seed": "The Feedback Loop: Why Practicing Alone Never Works", "tag": "Practice Methods"},
    {"title_seed": "How to Handle Tough Questions During Presentations Without Panicking", "tag": "Public Speaking"},
    {"title_seed": "Building Vocabulary for Professional Communication: A Practical Guide", "tag": "Vocabulary"},
    {"title_seed": "The Difference Between Speaking Fast and Speaking with Clarity", "tag": "Speech Skills"},
    {"title_seed": "How to Start a Speech: 7 Openings That Instantly Grab Attention", "tag": "Public Speaking"},
    {"title_seed": "Why Introverts Can Be Brilliant Public Speakers", "tag": "Confidence"},
    {"title_seed": "How to Communicate in High-Pressure Situations Without Freezing", "tag": "Confidence"},
    {"title_seed": "The Art of Pausing: Why Silence is Your Strongest Speaking Tool", "tag": "Speech Skills"},
    {"title_seed": "How to Build a Personal Brand Through Better Communication", "tag": "Career Growth"},
    {"title_seed": "What Freshers Get Wrong About Interview Communication", "tag": "Interviews"},
    {"title_seed": "How to Give Constructive Feedback Without Sounding Harsh", "tag": "Leadership"},
    {"title_seed": "Presentation Storytelling: How to Make Data Feel Like a Narrative", "tag": "Storytelling"},
    {"title_seed": "How Leaders Communicate Differently: Lessons for Young Professionals", "tag": "Leadership"},
    {"title_seed": "How to Improve English Speaking Fluency for Indian Professionals", "tag": "Fluency"},
    {"title_seed": "Mastering Virtual Presentations: Tips for Zoom and Video Calls", "tag": "Workplace Communication"},
    {"title_seed": "Why Communication Skills Are the #1 Career Accelerator", "tag": "Career Growth"},
    {"title_seed": "How to Speak With Authority in Team Meetings", "tag": "Workplace Communication"},
    {"title_seed": "Common Grammar Mistakes in Spoken English and How to Avoid Them", "tag": "Speech Skills"},
    {"title_seed": "The Psychology Behind Stage Fright and How to Rewire It", "tag": "Confidence"},
    {"title_seed": "How to Deliver a Persuasive Pitch in Under 2 Minutes", "tag": "Public Speaking"},
    {"title_seed": "How to Read the Room While Presenting", "tag": "Public Speaking"},
    {"title_seed": "From Nervous to Natural: A 30-Day Speaking Improvement Plan", "tag": "Practice Methods"},
    {"title_seed": "How to Sound Professional in Stakeholder Meetings", "tag": "Workplace Communication"},
    {"title_seed": "The Role of Emotional Intelligence in Great Communication", "tag": "Leadership"},
    {"title_seed": "How to Win Debates: Structure, Logic, and Delivery", "tag": "Public Speaking"},
    {"title_seed": "Why Most People Fail at Public Speaking and What SpeakingPad Does Differently", "tag": "Practice Methods"},
    {"title_seed": "How to Prepare a Speech in 15 Minutes: A Step-by-Step Guide", "tag": "Public Speaking"},
    {"title_seed": "Communication Tips for First-Time Managers", "tag": "Leadership"},
]

# Gradient palettes for card visuals
GRADIENTS = [
    "linear-gradient(135deg,#e8f0fe,#f0e6ff)",
    "linear-gradient(135deg,#dbeafe,#e0f2fe)",
    "linear-gradient(135deg,#fef3c7,#fde68a)",
    "linear-gradient(135deg,#d1fae5,#a7f3d0)",
    "linear-gradient(135deg,#fce7f3,#fbcfe8)",
    "linear-gradient(135deg,#e0e7ff,#c7d2fe)",
    "linear-gradient(135deg,#f0fdf4,#bbf7d0)",
    "linear-gradient(135deg,#fff7ed,#fed7aa)",
    "linear-gradient(135deg,#faf5ff,#e9d5ff)",
    "linear-gradient(135deg,#ecfdf5,#a7f3d0)",
]


def slugify(text: str) -> str:
    """Convert a title to a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def load_blog_data() -> list:
    """Load existing blog-data.json."""
    if not os.path.exists(BLOG_DATA_PATH):
        return []
    with open(BLOG_DATA_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_blog_data(data: list):
    """Save blog-data.json."""
    with open(BLOG_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def pick_topic(existing_data: list) -> dict:
    """Pick a topic that hasn't been used recently (last 10 posts)."""
    recent_slugs = set()
    for post in existing_data[-10:]:
        recent_slugs.add(post.get("slug", ""))

    available = [
        t for t in TOPICS
        if slugify(t["title_seed"]) not in recent_slugs
    ]
    if not available:
        available = TOPICS  # cycle

    return random.choice(available)


def call_llm(prompt: str) -> str:
    """Call the OpenRouter API (OpenAI-compatible) and return text."""
    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY environment variable is not set.")
        sys.exit(1)

    payload = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.85,
        "max_tokens": 4096,
    }).encode("utf-8")

    req = urllib.request.Request(
        OPENROUTER_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "https://speakingpad.in",
            "X-Title": "SpeakingPad Blog Generator",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"OpenRouter API error {e.code}: {body}")
        sys.exit(1)
    except Exception as e:
        print(f"OpenRouter API call failed: {e}")
        sys.exit(1)


def generate_article(topic: dict) -> dict:
    """Use Gemini to generate a full article as structured JSON."""
    prompt = f"""You are the content writer for SpeakingPad — a communication coaching platform founded by Arpit Gupta.
SpeakingPad helps MBA students, final-year college students, and early-stage corporate professionals improve their public speaking, interview skills, presentation delivery, and leadership communication.

Write a blog article on the topic: "{topic['title_seed']}"

RULES:
- The tone should be confident, direct, practical, honest, and conversational — like advice from a mentor who has real corporate experience.
- Use "you" voice. Write for ambitious Indian students and young professionals.
- Include real, actionable advice. No fluffy motivational filler.
- The article should be 800–1200 words.
- Use short paragraphs (2–3 sentences max).
- Include at least 3 subheadings (use H2 tags).
- Include at least 1 blockquote with an insight or a punchy statement.
- End with a subtle mention of SpeakingPad and how readers can improve their skills through structured coaching.
- Do NOT start with "In today's world" or any cliché opener. Start with a strong, specific hook.

Return ONLY valid JSON (no markdown fences, no extra text) with these exact keys:
{{
  "title": "the final polished article title",
  "meta_description": "a 150-character SEO meta description",
  "excerpt": "a 2-sentence excerpt for the blog listing card (max 180 chars)",
  "read_time": "X min read",
  "body_html": "the full article body as HTML (use <h2>, <p>, <ul>, <li>, <blockquote>, <strong> tags)"
}}"""

    raw = call_llm(prompt)
    # Clean up potential markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Failed to parse Gemini response as JSON: {e}")
        print(f"Raw response:\n{raw[:500]}")
        sys.exit(1)


def render_post(article: dict, topic: dict, date_str: str) -> str:
    """Render the article HTML from the template."""
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%d %b %Y")

    html = template
    html = html.replace("{{TITLE}}", article["title"])
    html = html.replace("{{META_DESCRIPTION}}", article.get("meta_description", ""))
    html = html.replace("{{TAG}}", topic["tag"])
    html = html.replace("{{DATE_DISPLAY}}", date_display)
    html = html.replace("{{READ_TIME}}", article.get("read_time", "5 min read"))
    html = html.replace("{{ARTICLE_BODY}}", article.get("body_html", ""))
    return html


def update_sitemap(existing_data: list):
    """Generate and update sitemap.xml with all blog posts for SEO discovery."""
    urls = [
        "<url><loc>https://speakingpad.in/</loc><priority>1.0</priority></url>",
        "<url><loc>https://speakingpad.in/blog.html</loc><priority>0.9</priority></url>"
    ]
    
    for post in existing_data:
        date_str = post.get("date", "")
        slug = post.get("slug", "")
        if slug:
            urls.append(f"<url><loc>https://speakingpad.in/posts/{slug}.html</loc><lastmod>{date_str}</lastmod><priority>0.8</priority></url>")
            
    sitemap_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  {"".join(urls)}
</urlset>"""
    
    with open(SITEMAP_PATH, "w", encoding="utf-8") as f:
        f.write(sitemap_content)


def main():
    print("🚀 SpeakingPad Blog Generator")
    print("=" * 40)

    # Ensure directories exist
    os.makedirs(POSTS_DIR, exist_ok=True)

    # Load existing data
    existing = load_blog_data()
    today = datetime.date.today().isoformat()

    # Check if we already posted today
    if any(post.get("date") == today for post in existing):
        print(f"✅ Article already exists for {today}. Skipping.")
        return

    # Pick topic
    topic = pick_topic(existing)
    print(f"📝 Topic: {topic['title_seed']}")
    print(f"🏷️  Tag: {topic['tag']}")

    # Generate article
    print("🤖 Calling Gemini API...")
    article = generate_article(topic)
    print(f"✅ Generated: {article['title']}")

    # Create slug
    slug = slugify(article["title"])

    # Render HTML
    html = render_post(article, topic, today)
    post_path = os.path.join(POSTS_DIR, f"{slug}.html")
    with open(post_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📄 Saved: posts/{slug}.html")

    # Update blog-data.json
    new_entry = {
        "slug": slug,
        "title": article["title"],
        "excerpt": article.get("excerpt", ""),
        "tag": topic["tag"],
        "date": today,
        "readTime": article.get("read_time", "5 min read"),
        "gradient": random.choice(GRADIENTS),
    }
    existing.append(new_entry)
    save_blog_data(existing)
    print(f"📊 Updated blog-data.json ({len(existing)} articles total)")

    # Update Sitemap for SEO
    update_sitemap(existing)
    print("🗺️  Updated sitemap.xml for Google discovery")

    print("✅ Done!")


if __name__ == "__main__":
    main()
