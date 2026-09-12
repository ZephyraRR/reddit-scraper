# reddit-scraper

Full-thread Reddit research scraper. Reddit's live search and most scrapers
get blocked; this instead searches Google via [searchapi.io](https://www.searchapi.io/)
for `site:reddit.com` queries, filters the results down to actual post URLs,
then pulls each post's full content (post body + entire nested comment tree,
including "load more comments" branches) through Reddit's own `.json` API
using an authenticated session cookie.

Each post is saved as its own `.txt` file (title, subreddit, author, score,
body, then the full comment tree) inside `output/<query-name>/`. No database,
no config beyond two small local files — point it at a list of search queries
and it hands you back plain-text threads ready to read or feed into an LLM.

**[Project page →](https://zephyrarr.github.io/reddit-scraper/)**

## Setup

```bash
pip install -r requirements.txt
```

1. **searchapi.io key** — sign up at searchapi.io, then either set an env var:
   ```bash
   export SEARCHAPI_KEY="your-key-here"        # bash
   $env:SEARCHAPI_KEY = "your-key-here"          # PowerShell
   ```
   or create a `.env` file in this directory (never committed):
   ```
   SEARCHAPI_KEY=your-key-here
   ```
2. **Reddit session cookie** — log into reddit.com in a real browser, open
   DevTools → Network, copy the full `Cookie` request header from any
   request to reddit.com, and paste it (one line, no quotes) into a file
   named `reddit_cookie.txt` in this directory. **Treat this like a
   password — never commit it or share it.** It's already gitignored.
3. Copy `queries.example.txt` to `queries.txt` and edit it with your own
   search queries (one per line, `#` for comments).

## Run

```bash
python reddit_scraper.py
```

Output lands in `output/<sanitized-query>/001 - <post title>.txt`, etc.

### Options

```bash
python reddit_scraper.py --queries my_queries.txt --output my_output --max-pages 3 --cookie my_cookie.txt
```

| Flag | Default | Meaning |
|---|---|---|
| `--queries` | `queries.txt` | Path to the query list |
| `--output` | `output/` | Where scraped threads are saved |
| `--cookie` | `reddit_cookie.txt` | Path to the Reddit cookie file |
| `--max-pages` | `6` | Google result pages to pull per query |

## Tuning

At the top of `reddit_scraper.py`:
- `MAX_PAGES` — how many Google result pages to pull per query (default 6, overridable with `--max-pages`).
- `MAX_MORE_ITERATIONS` — how many rounds of "load more comments" to follow.
- `REQUEST_DELAY` / `SEARCH_DELAY` — throttling between Reddit/Google calls.
  Keep these reasonable to avoid rate-limit blocks (403/429) or tripping
  Reddit's bot detection on the logged-in account whose cookie you're using.

## Notes

- `queries.txt`, `reddit_cookie.txt`, `.env`, `output/`, and `run_log.txt` are
  gitignored since they're either secrets or run-specific scraped data.
- This scrapes content visible to a logged-in Reddit account for research
  purposes. Respect Reddit's terms of service and rate limits, and don't
  redistribute scraped content.

## License

MIT — see [LICENSE](LICENSE).
