"""
Reddit Scraper
==============
1. Pretrazuje Google (preko searchapi.io) za svaki query iz queries.txt, do MAX_PAGES stranica.
2. Filtrira rezultate koji vode na Reddit objave (r/.../comments/...).
3. Za svaku objavu preuzima KOMPLETAN sadrzaj (post + svi komentari, ukljucujuci "load more
   comments" grane) preko Reddit-ovog .json API-ja, koristeci ulogovanu sesiju (cookie).
4. Cuva svaku objavu u poseban .txt fajl, u podfolder po query-ju, unutar ./output.

Pokretanje:
    python reddit_scraper.py

Query-jevi se citaju iz queries.txt (jedan po liniji). Ako fajl ne postoji, pravi se
automatski sa test query-jem.

Kolacic za Reddit sesiju se cita iz reddit_cookie.txt (jedan red, ceo "Cookie" header
iz pravog ulogovanog browsera). Taj fajl je osetljiv kao lozinka - ne deliti ga.
"""

import json
import os
import re
import sys
import time
import traceback
import urllib.parse

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SEARCHAPI_KEY = os.environ.get("SEARCHAPI_KEY", "")
SEARCHAPI_URL = "https://www.searchapi.io/api/v1/search"

MAX_PAGES = 6                 # koliko Google stranica po query-ju (max)
MAX_MORE_ITERATIONS = 6       # koliko puta da "dopuni" load-more komentare
REQUEST_DELAY = 5.0           # pauza izmedju reddit zahteva (sekunde)
SEARCH_DELAY = 1.0            # pauza izmedju google search zahteva (sekunde)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QUERIES_FILE = os.path.join(SCRIPT_DIR, "queries.txt")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
COOKIE_FILE = os.path.join(SCRIPT_DIR, "reddit_cookie.txt")

BLOCK_MARKERS = (
    "blocked by network security",
    "accounts are required to access old reddit",
    "whoa there",
)

REDDIT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-GB,en;q=0.6",
}


def load_queries():
    if not os.path.exists(QUERIES_FILE):
        default_query = 'site:reddit.com r/VideoEditing "critique my video"'
        with open(QUERIES_FILE, "w", encoding="utf-8") as f:
            f.write(default_query + "\n")
        print(f"Napravljen {QUERIES_FILE} sa test query-jem.")
        return [default_query]

    with open(QUERIES_FILE, "r", encoding="utf-8") as f:
        queries = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    return queries


def load_cookie():
    if not os.path.exists(COOKIE_FILE):
        print(f"[!] Nema {COOKIE_FILE}. Ubaci Reddit 'Cookie' header (iz ulogovanog browsera) u taj fajl.")
        sys.exit(1)
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        cookie = f.read().strip()
    if not cookie:
        print(f"[!] {COOKIE_FILE} je prazan.")
        sys.exit(1)
    return cookie


def sanitize_filename(name, max_len=150):
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.strip().strip(".")
    if not name:
        name = "untitled"
    return name[:max_len]


def searchapi_google(query, max_pages=MAX_PAGES):
    """Vraca listu linkova sa Google pretrage, do max_pages stranica (staje ranije ako nema vise rezultata)."""
    all_links = []
    for page in range(1, max_pages + 1):
        params = {
            "engine": "google",
            "q": query,
            "api_key": SEARCHAPI_KEY,
            "page": page,
        }
        try:
            resp = requests.get(SEARCHAPI_URL, params=params, timeout=30)
        except requests.RequestException as e:
            print(f"    [!] Greska pri pretrazi (page {page}): {e}")
            break

        if resp.status_code != 200:
            print(f"    [!] SearchAPI status {resp.status_code} na strani {page}: {resp.text[:300]}")
            break

        data = resp.json()
        results = data.get("organic_results", [])
        if not results:
            print(f"    Nema vise rezultata posle strane {page - 1}.")
            break

        for r in results:
            link = r.get("link")
            if link:
                all_links.append(link)

        print(f"    Strana {page}: {len(results)} rezultata")
        time.sleep(SEARCH_DELAY)

    return all_links


def extract_reddit_post_urls(links):
    """Filtrira samo linkove ka konkretnim Reddit objavama (ne subreddit home, ne user profili)."""
    seen = set()
    post_urls = []
    for link in links:
        if "reddit.com/r/" not in link or "/comments/" not in link:
            continue
        clean = link.split("?")[0].rstrip("/")
        clean = re.sub(r"https?://(www\.|old\.|new\.|m\.)?reddit\.com", "https://old.reddit.com", clean)
        if clean not in seen:
            seen.add(clean)
            post_urls.append(clean)
    return post_urls


def fetch_json(session, url, params=None, retries=5):
    """GET na Reddit .json URL preko ulogovane sesije (cookie), sa retry/backoff na blokade."""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=20)
        except requests.RequestException as e:
            print(f"      [!] Mrezna greska ({attempt}/{retries}): {e}")
            time.sleep(5 * attempt)
            continue

        text = resp.text
        lowered = text.lower()

        if resp.status_code in (429, 403) or any(marker in lowered for marker in BLOCK_MARKERS):
            wait = 15 * attempt
            print(f"      [!] Reddit blokada/status {resp.status_code}, cekam {wait}s pa pokusavam ponovo...")
            time.sleep(wait)
            continue

        if resp.status_code != 200:
            print(f"      [!] Status {resp.status_code} za {url}")
            return None

        try:
            return resp.json()
        except ValueError:
            print(f"      [!] Odgovor nije validan JSON: {text[:150].strip()}")
            time.sleep(5 * attempt)
            continue

    return None


def flatten_comment_tree(children, flat, more_stubs):
    """Rekurzivno prolazi kroz pocetno ucitano stablo komentara i puni flat dict + more_stubs listu."""
    for child in children:
        kind = child.get("kind")
        data = child.get("data", {})

        if kind == "more":
            ids = data.get("children") or []
            more_stubs.extend(ids)
            continue

        if kind != "t1":
            continue

        fullname = data.get("name")
        if not fullname:
            continue

        flat[fullname] = {
            "parent_id": data.get("parent_id"),
            "author": data.get("author") or "[deleted]",
            "body": data.get("body") or "",
            "score": data.get("score", 0),
        }

        replies = data.get("replies")
        if isinstance(replies, dict):
            nested = replies.get("data", {}).get("children", [])
            flatten_comment_tree(nested, flat, more_stubs)


def fetch_more_children(session, link_fullname, children_ids):
    """Poziva Reddit-ov morechildren API da dovuce 'load more comments' grane."""
    all_things = []
    chunk_size = 100
    for i in range(0, len(children_ids), chunk_size):
        chunk = children_ids[i:i + chunk_size]
        params = {
            "api_type": "json",
            "link_id": link_fullname,
            "children": ",".join(chunk),
            "limit_children": "false",
            "raw_json": 1,
        }
        data = fetch_json(session, "https://old.reddit.com/api/morechildren.json", params=params)
        time.sleep(REQUEST_DELAY)
        if not data:
            continue
        things = data.get("json", {}).get("data", {}).get("things", [])
        all_things.extend(things)
    return all_things


def gather_all_comments(session, post_json, link_fullname):
    """Vraca (flat_dict, children_map) sa APSOLUTNO svim komentarima, ukljucujuci load-more grane."""
    flat = {}
    more_stubs = []
    flatten_comment_tree(post_json[1]["data"]["children"], flat, more_stubs)

    iteration = 0
    while more_stubs and iteration < MAX_MORE_ITERATIONS:
        iteration += 1
        batch = list(dict.fromkeys(more_stubs))  # dedup, ocuvaj redosled
        more_stubs = []
        print(f"      Ucitavam dodatne komentare (load more), iteracija {iteration}, {len(batch)} stavki...")
        things = fetch_more_children(session, link_fullname, batch)
        for thing in things:
            kind = thing.get("kind")
            data = thing.get("data", {})
            if kind == "more":
                ids = data.get("children") or []
                more_stubs.extend(ids)
                continue
            if kind != "t1":
                continue
            fullname = data.get("name")
            if not fullname or fullname in flat:
                continue
            flat[fullname] = {
                "parent_id": data.get("parent_id"),
                "author": data.get("author") or "[deleted]",
                "body": data.get("body") or "",
                "score": data.get("score", 0),
            }

    children_map = {}
    for fullname, c in flat.items():
        children_map.setdefault(c["parent_id"], []).append(fullname)

    return flat, children_map


def render_comments(link_fullname, flat, children_map, depth=0):
    lines = []
    for fullname in children_map.get(link_fullname, []):
        c = flat[fullname]
        indent = "    " * depth
        lines.append(f"{indent}[u/{c['author']} | score: {c['score']}]")
        for body_line in c["body"].split("\n"):
            lines.append(f"{indent}{body_line}")
        lines.append("")
        lines.extend(render_comments(fullname, flat, children_map, depth + 1))
    return lines


def scrape_post(session, url):
    json_url = url + ".json"
    post_json = fetch_json(session, json_url)
    time.sleep(REQUEST_DELAY)

    if not post_json or len(post_json) < 2:
        print(f"      [!] Nije uspelo preuzimanje objave: {url}")
        return None

    try:
        post_data = post_json[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        print(f"      [!] Neocekivan format objave: {url}")
        return None

    link_fullname = post_data.get("name")
    flat, children_map = gather_all_comments(session, post_json, link_fullname)

    lines = []
    lines.append(f"Title: {post_data.get('title', 'untitled')}")
    lines.append(f"Subreddit: r/{post_data.get('subreddit', '')}")
    lines.append(f"Author: u/{post_data.get('author', '[deleted]')}")
    lines.append(f"Score: {post_data.get('score', 0)}")
    lines.append(f"Num comments (reported): {post_data.get('num_comments', 0)}")
    lines.append(f"Num comments (scraped): {len(flat)}")
    lines.append(f"URL: {url}")
    lines.append("=" * 90)
    lines.append("")

    selftext = post_data.get("selftext") or ""
    if selftext:
        lines.append(selftext)
    else:
        url_field = post_data.get("url")
        if url_field and url_field != url:
            lines.append(f"[Link post] {url_field}")
    lines.append("")
    lines.append("=" * 90)
    lines.append("KOMENTARI")
    lines.append("=" * 90)
    lines.append("")
    lines.extend(render_comments(link_fullname, flat, children_map))

    return {
        "title": post_data.get("title", "untitled"),
        "text": "\n".join(lines),
    }


def save_post_file(post, out_dir, index):
    os.makedirs(out_dir, exist_ok=True)
    safe_title = sanitize_filename(post["title"])
    filename = f"{index:03d} - {safe_title}.txt"
    filepath = os.path.join(out_dir, filename)

    counter = 1
    base = filepath
    while os.path.exists(filepath):
        filepath = base[:-4] + f"_{counter}.txt"
        counter += 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(post["text"])
    return filepath


def main():
    if not SEARCHAPI_KEY:
        print("[!] Nedostaje SEARCHAPI_KEY environment varijabla.")
        sys.exit(1)

    queries = load_queries()
    if not queries:
        print("Nema query-ja u queries.txt.")
        sys.exit(1)

    cookie = load_cookie()
    session = requests.Session()
    session.headers.update(REDDIT_HEADERS)
    session.headers["Cookie"] = cookie

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for query in queries:
        print(f"\n=== Query: {query} ===")
        query_dir = os.path.join(OUTPUT_DIR, sanitize_filename(query, max_len=80))

        links = searchapi_google(query, MAX_PAGES)
        post_urls = extract_reddit_post_urls(links)
        print(f"Pronadjeno {len(post_urls)} jedinstvenih Reddit objava.")

        for i, url in enumerate(post_urls, start=1):
            print(f"  [{i}/{len(post_urls)}] {url}")
            try:
                post = scrape_post(session, url)
                if post:
                    filepath = save_post_file(post, query_dir, i)
                    print(f"      Sacuvano: {filepath}")
            except Exception as e:
                print(f"      [!] Neocekivana greska: {e}")
                traceback.print_exc()
                continue

    print("\nGotovo.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("[!] FATALNA GRESKA:")
        traceback.print_exc()
        sys.exit(1)
