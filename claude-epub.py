#!/usr/bin/env python3
"""
Builds offline EPUB books from:
  1. https://www.anthropic.com/engineering   -> anthropic-engineering.epub
  2. https://claude.dev/  (blog)             -> claude-dev-blog.epub
  3. https://code.claude.com/docs            -> claude-code-docs.epub

Requirements:
  pandoc (https://pandoc.org)      e.g. brew install pandoc
  pip3 install requests beautifulsoup4 readability-lxml lxml_html_clean

Usage:
  python3 claude_epub.py                 # all three books
  python3 claude_epub.py engineering     # just one: engineering | blog | docs
"""
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from readability import Document

UA = {"User-Agent": "Mozilla/5.0 (offline-reader script)"}
DELAY = 0.5  # seconds between requests, be polite

session = requests.Session()
session.headers.update(UA)


def get(url):
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 200:
            return r.text
        print(f"  ! {r.status_code} {url}")
    except requests.RequestException as e:
        print(f"  ! {e.__class__.__name__} {url}")
    return None


# ---------------------------------------------------------------- discovery

def links_from_page(page_url, pattern):
    html = get(page_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        u = urljoin(page_url, a["href"]).split("#")[0].split("?")[0]
        if re.search(pattern, urlparse(u).path) and u not in out:
            out.append(u)
    return out


def links_from_sitemap(sitemap_url, pattern, depth=0):
    xml = get(sitemap_url)
    if not xml or depth > 2:
        return []
    try:
        root = ET.fromstring(xml.encode())
    except ET.ParseError:
        return []
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    out = []
    # sitemap index -> recurse into child sitemaps
    for loc in root.iter(f"{ns}sitemap"):
        child = loc.find(f"{ns}loc")
        if child is not None:
            out += links_from_sitemap(child.text.strip(), pattern, depth + 1)
    for loc in root.iter(f"{ns}url"):
        child = loc.find(f"{ns}loc")
        if child is not None:
            u = child.text.strip()
            if re.search(pattern, urlparse(u).path):
                out.append(u)
    return out


def links_from_feed(feed_url, pattern):
    xml = get(feed_url)
    if not xml:
        return []
    return [u for u in re.findall(r"https?://[^\s<\"']+", xml)
            if re.search(pattern, urlparse(u).path)]


def unique(seq):
    seen, out = set(), []
    for u in seq:
        k = u.rstrip("/")
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out


# ---------------------------------------------------------------- HTML articles

def clean_article(url, html):
    doc = Document(html)
    title = doc.short_title() or url
    soup = BeautifulSoup(doc.summary(html_partial=True), "html.parser")
    # make image/link URLs absolute so pandoc can embed images
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[0].split()[0]
        if src:
            img["src"] = urljoin(url, src)
        for attr in ("srcset", "sizes", "loading", "data-src"):
            img.attrs.pop(attr, None)
    for a in soup.find_all("a", href=True):
        a["href"] = urljoin(url, a["href"])
    for tag in soup.find_all(["script", "style", "noscript", "iframe", "svg", "button"]):
        tag.decompose()
    # demote headings so each article is exactly one chapter
    for level in range(5, 0, -1):
        for h in soup.find_all(f"h{level}"):
            h.name = f"h{level + 1}"
    # drop a duplicated title at the top of the body
    first = soup.find(["h2"])
    if first and first.get_text(strip=True) == title.strip():
        first.decompose()
    return title, str(soup)


def build_html_book(name, urls, out_file, book_title):
    print(f"\n[{name}] {len(urls)} articles")
    parts = []
    for i, u in enumerate(urls, 1):
        print(f"  {i:>3}/{len(urls)} {u}")
        html = get(u)
        time.sleep(DELAY)
        if not html:
            continue
        title, body = clean_article(u, html)
        parts.append(
            f'<h1>{BeautifulSoup(title, "html.parser").get_text()}</h1>\n'
            f'<p><em><a href="{u}">{u}</a></em></p>\n{body}\n'
        )
    if not parts:
        print("  nothing downloaded, skipping")
        return
    combined = "<html><body>\n" + "\n".join(parts) + "\n</body></html>"
    run_pandoc(combined, "html", out_file, book_title)


# ---------------------------------------------------------------- Markdown docs

MDX_TAG = re.compile(r"</?[A-Z][A-Za-z0-9]*(\s[^<>]*)?/?>")


def clean_mdx(md):
    out, in_fence = [], False
    for line in md.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence:
            line = MDX_TAG.sub("", line)  # <Tip>, <Steps>, <Tab title="x"> ...
        out.append(line)
    return "\n".join(out)


def build_docs_book(base, out_file, book_title):
    print("\n[docs]")
    full = get(f"{base}/llms-full.txt")
    if full and len(full) > 10000:
        md = full
        print(f"  using llms-full.txt ({len(full)//1024} KB)")
    else:
        index = get(f"{base}/llms.txt") or ""
        urls = unique(re.findall(r"\((https?://[^)\s]+\.md)\)", index))
        urls = [u for u in urls if "/docs/en/" in u] or urls
        print(f"  llms-full.txt missing, fetching {len(urls)} pages")
        pages = []
        for i, u in enumerate(urls, 1):
            print(f"  {i:>3}/{len(urls)} {u}")
            t = get(u)
            time.sleep(DELAY)
            if t:
                if not t.lstrip().startswith("# "):
                    t = f"# {u.rsplit('/', 1)[-1][:-3]}\n\n{t}"
                pages.append(t)
        md = "\n\n".join(pages)
    if not md.strip():
        print("  nothing downloaded, skipping")
        return
    # strip YAML front matter blocks that Mintlify sometimes keeps per page
    md = re.sub(r"(?m)^---\n(?:[a-zA-Z_]+:.*\n)+---\n", "", md)
    run_pandoc(clean_mdx(md), "markdown", out_file, book_title)


# ---------------------------------------------------------------- pandoc

def pandoc_split_flag():
    try:
        v = subprocess.run(["pandoc", "--version"], capture_output=True, text=True).stdout
        major = int(re.search(r"pandoc\S* (\d+)", v).group(1))
    except Exception:
        sys.exit("pandoc not found. Install it: https://pandoc.org/installing.html")
    return "--split-level=1" if major >= 3 else "--epub-chapter-level=1"


def run_pandoc(text, fmt, out_file, title):
    with tempfile.NamedTemporaryFile("w", suffix=f".{fmt[:4]}", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        src = f.name
    cmd = ["pandoc", src, "-f", fmt if fmt == "html" else "markdown-yaml_metadata_block",
           "-t", "epub3", "-o", out_file, "--toc", "--toc-depth=2",
           pandoc_split_flag(), "--metadata", f"title={title}",
           "--metadata", "lang=en", "--resource-path=."]
    print(f"  pandoc -> {out_file} (downloading images, may take a while)")
    r = subprocess.run(cmd, capture_output=True, text=True)
    warnings = [l for l in r.stderr.splitlines() if l.strip()]
    if warnings:
        print(f"  pandoc: {len(warnings)} warnings (usually missing images, safe to ignore)")
    if r.returncode != 0:
        print(r.stderr[-2000:])
        print(f"  ! pandoc failed, source kept at {src}")
    else:
        Path(src).unlink(missing_ok=True)
        print(f"  done: {Path(out_file).resolve()}")


# ---------------------------------------------------------------- main

def engineering():
    # the listing page links its articles under /engineering/ or /research/
    page_pat = r"^/(engineering|research)/[^/]+/?$"
    urls = unique(
        links_from_page("https://www.anthropic.com/engineering", page_pat)
        + links_from_sitemap("https://www.anthropic.com/sitemap.xml",
                             r"^/engineering/[^/]+/?$")
    )
    urls = [u for u in urls if not u.rstrip("/").endswith(("/engineering", "/research"))]
    build_html_book("engineering", urls, "anthropic-engineering.epub",
                    "Anthropic Engineering")


def blog():
    pat = r"^/blog/[^/]+/?$"
    urls = unique(
        links_from_page("https://claude.dev/", pat)
        + links_from_sitemap("https://claude.dev/sitemap.xml", pat)
        + links_from_sitemap("https://claude.dev/sitemap-index.xml", pat)
        + links_from_feed("https://claude.dev/rss.xml", pat)
        + links_from_feed("https://claude.dev/feed.xml", pat)
    )
    build_html_book("blog", urls, "claude-dev-blog.epub", "claude.dev Blog")


def docs():
    build_docs_book("https://code.claude.com/docs", "claude-code-docs.epub",
                    "Claude Code Docs")


if __name__ == "__main__":
    jobs = {"engineering": engineering, "blog": blog, "docs": docs}
    chosen = sys.argv[1:] or list(jobs)
    for j in chosen:
        if j not in jobs:
            sys.exit(f"unknown target '{j}', use: {' | '.join(jobs)}")
        jobs[j]()
