#!/usr/bin/env python3
"""
fetch_transcripts.py
────────────────────
Fetch YouTube transcripts via Supadata API and organize them into:
  /research/youtube-transcripts/<channel_or_author>/<video_id>_<title>.md
  /research/sources.md  (auto-updated index)

Usage:
  python fetch_transcripts.py --url "https://youtube.com/watch?v=VIDEO_ID"
  python fetch_transcripts.py --file urls.txt          # one URL per line
  python fetch_transcripts.py --channel CHANNEL_HANDLE --max 10

Requirements:
  pip install requests python-slugify

Environment variable (required):
  SUPADATA_API_KEY=your_key_here
  → Get a free key at https://supadata.ai
"""

import os
import re
import sys
import json
import argparse
import textwrap
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

try:
    from slugify import slugify
except ImportError:
    def slugify(text, max_length=60):
        """Minimal fallback slugifier."""
        text = re.sub(r"[^\w\s-]", "", text.lower())
        text = re.sub(r"[\s_-]+", "-", text).strip("-")
        return text[:max_length]

# ─── Configuration ────────────────────────────────────────────────────────────

API_KEY = os.environ.get("SUPADATA_API_KEY", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
BASE_URL = "https://api.supadata.ai/v1"
REPO_ROOT = Path(".")          # Change to your repo root if needed
RESEARCH_DIR = REPO_ROOT / "research"
TRANSCRIPTS_DIR = RESEARCH_DIR / "youtube-transcripts"
SOURCES_FILE = RESEARCH_DIR / "sources.md"


# ─── Supadata helpers ─────────────────────────────────────────────────────────

def supadata_headers():
    if not API_KEY:
        sys.exit(
            "❌  SUPADATA_API_KEY not set.\n"
            "    Export it: export SUPADATA_API_KEY=your_key\n"
            "    Get a free key at: https://supadata.ai"
        )
    return {"x-api-key": API_KEY, "Content-Type": "application/json"}


def fetch_transcript(video_url: str) -> dict:
    """Fetch transcript + metadata for a single YouTube URL."""
    video_id = extract_video_id(video_url)
    if not video_id:
        print(f"  ⚠️  Could not parse video ID from: {video_url}")
        return {}

    print(f"  📡 Fetching transcript for {video_id} …")
    resp = requests.get(
        f"{BASE_URL}/youtube/transcript",
        headers=supadata_headers(),
        params={"videoId": video_id, "text": "true"},
        timeout=30,
    )

    if resp.status_code == 404:
        print(f"  ⚠️  No transcript available for {video_id} (video may have captions disabled)")
        return {}
    if resp.status_code == 429:
        print("  ⚠️  Rate limited – wait a moment and try again")
        return {}
    resp.raise_for_status()

    data = resp.json()

    # Also fetch video metadata
    meta = fetch_video_metadata(video_id)

    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": meta.get("title", video_id),
        "channel": meta.get("channelTitle", "Unknown"),
        "published_at": meta.get("publishedAt", ""),
        "description": meta.get("description", "")[:500],
        "transcript": data.get("content", ""),
        "lang": data.get("lang", "en"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_video_metadata(video_id: str) -> dict:
    """Fetch video title, channel, date from YouTube Data API v3."""
    if not YOUTUBE_API_KEY:
        print("  ⚠️  YOUTUBE_API_KEY not set — channel/title will show as Unknown")
        return {}
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "id": video_id,
                "part": "snippet",
                "key": YOUTUBE_API_KEY,
            },
            timeout=15,
        )
        if resp.ok:
            items = resp.json().get("items", [])
            if items:
                snippet = items[0]["snippet"]
                return {
                    "title": snippet.get("title", video_id),
                    "channelTitle": snippet.get("channelTitle", "Unknown"),
                    "publishedAt": snippet.get("publishedAt", ""),
                    "description": snippet.get("description", ""),
                }
    except Exception as e:
        print(f"  ⚠️  YouTube API error: {e}")
    return {}


def fetch_channel_videos(channel_handle: str, max_results: int = 10) -> list[str]:
    """Return a list of video URLs from a channel (most recent first)."""
    print(f"  📡 Fetching video list for @{channel_handle} …")
    resp = requests.get(
        f"{BASE_URL}/youtube/channel/videos",
        headers=supadata_headers(),
        params={"channelHandle": channel_handle, "limit": max_results},
        timeout=30,
    )
    resp.raise_for_status()
    videos = resp.json().get("videos", [])
    return [f"https://www.youtube.com/watch?v={v['videoId']}" for v in videos]


# ─── File writers ──────────────────────────────────────────────────────────────

def save_transcript(data: dict) -> Path:
    """Save transcript as a Markdown file. Returns the saved path."""
    channel_slug = slugify(data["channel"], max_length=40)
    title_slug = slugify(data["title"], max_length=60)
    filename = f"{data['video_id']}_{title_slug}.md"

    out_dir = TRANSCRIPTS_DIR / channel_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename

    published = data["published_at"][:10] if data["published_at"] else "unknown"

    content = textwrap.dedent(f"""\
        # {data['title']}

        | Field       | Value |
        |-------------|-------|
        | **Channel** | {data['channel']} |
        | **Video ID**| [{data['video_id']}]({data['url']}) |
        | **Published**| {published} |
        | **Language**| {data['lang']} |
        | **Fetched** | {data['fetched_at']} |

        ## Description

        {data['description']}

        ---

        ## Transcript

        {data['transcript']}
    """)

    out_path.write_text(content, encoding="utf-8")
    print(f"  ✅  Saved → {out_path}")
    return out_path


def update_sources_md(entries: list[dict]):
    """Append new entries to /research/sources.md."""
    SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Read existing content (create header if new file)
    if SOURCES_FILE.exists():
        existing = SOURCES_FILE.read_text(encoding="utf-8")
    else:
        existing = textwrap.dedent("""\
            # Research Sources

            A curated index of all experts, videos, and posts tracked in this repository.

            ---

            ## YouTube Videos

            | Date | Channel | Title | Link | Transcript |
            |------|---------|-------|------|------------|
        """)

    new_rows = []
    for e in entries:
        if not e:
            continue
        published = e["published_at"][:10] if e.get("published_at") else "—"
        channel_slug = slugify(e["channel"], max_length=40)
        title_slug = slugify(e["title"], max_length=60)
        transcript_path = f"youtube-transcripts/{channel_slug}/{e['video_id']}_{title_slug}.md"
        row = (
            f"| {published} | {e['channel']} | {e['title']} "
            f"| [▶ Watch]({e['url']}) | [📄 Transcript]({transcript_path}) |"
        )
        # Avoid duplicates
        if e["video_id"] not in existing:
            new_rows.append(row)

    if new_rows:
        updated = existing.rstrip() + "\n" + "\n".join(new_rows) + "\n"
        SOURCES_FILE.write_text(updated, encoding="utf-8")
        print(f"\n  📝  sources.md updated with {len(new_rows)} new entry/entries → {SOURCES_FILE}")
    else:
        print("\n  ℹ️  sources.md — no new entries (all already present)")


# ─── Utilities ────────────────────────────────────────────────────────────────

def extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from any common URL format."""
    patterns = [
        r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # Maybe it's already just an ID
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return url.strip()
    return None


def read_urls_file(path: str) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch YouTube transcripts via Supadata → save to /research/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Single YouTube URL or video ID")
    group.add_argument("--file", metavar="URLS_FILE", help="Text file with one URL per line")
    group.add_argument("--channel", metavar="HANDLE", help="YouTube channel handle (without @)")

    parser.add_argument("--max", type=int, default=10, metavar="N",
                        help="Max videos to fetch from a channel (default: 10)")
    parser.add_argument("--repo", default=".", metavar="PATH",
                        help="Path to your repo root (default: current directory)")
    args = parser.parse_args()

    # Update global paths if --repo is set
    global REPO_ROOT, RESEARCH_DIR, TRANSCRIPTS_DIR, SOURCES_FILE
    REPO_ROOT = Path(args.repo).resolve()
    RESEARCH_DIR = REPO_ROOT / "research"
    TRANSCRIPTS_DIR = RESEARCH_DIR / "youtube-transcripts"
    SOURCES_FILE = RESEARCH_DIR / "sources.md"

    # Collect URLs
    if args.url:
        urls = [args.url]
    elif args.file:
        urls = read_urls_file(args.file)
        print(f"📂  Loaded {len(urls)} URLs from {args.file}")
    else:
        urls = fetch_channel_videos(args.channel, args.max)
        print(f"📋  Found {len(urls)} videos from @{args.channel}")

    print(f"\n🚀  Processing {len(urls)} video(s)…\n")

    results = []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}")
        try:
            data = fetch_transcript(url)
            if data:
                save_transcript(data)
                results.append(data)
        except requests.HTTPError as e:
            print(f"  ❌  HTTP error: {e}")
        except Exception as e:
            print(f"  ❌  Unexpected error: {e}")
        print()

    update_sources_md(results)

    print(f"\n🎉  Done! {len(results)}/{len(urls)} transcripts saved.")
    print(f"    Repo structure updated under: {RESEARCH_DIR}")


if __name__ == "__main__":
    main()
