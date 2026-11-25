# jace-news

Headlines for the world news band on jacetrading.com: metals, trade and industry news from public news feeds.

- `scripts/jace_news.py` collects headlines every hour (GitHub Actions, `.github/workflows/news.yml`) and writes `news.json`.
- Only the headline, the outlet name and the link are stored. No article text, no images. The site shows them as links to the original publisher.
- Korean titles come from the free MyMemory translation API and are cached in `ko_cache.json`.
- The site reads `news.json` straight from this repository, so new headlines need no site deploy.

Format: `{"updated": "2026-11-25T18:24+09:00", "items": [{"s": "outlet", "t": "English title", "u": "link", "k": "Korean title"}]}`
