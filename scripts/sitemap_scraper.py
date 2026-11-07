from setup_db import logger, DB_PATH, create_tables
from schemas import NewsArticle
import news_scraper
from news_scraper import (USER_AGENT, EXCLUDED_URLS, SAVED, DUPLICATE, INVALID, DB_ERROR,
                          load_and_prepare_data, is_valid_context, parse_article, extract_article, request_pause, wait)

import pandas as pd
from pydantic import ValidationError
import sqlite3
from typing import Tuple, List, Dict, Optional
import argparse
from collections import Counter
import datetime
import hashlib
import random
import re
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright

DRY_RUN = False

START_DATE = datetime.date(2020, 1, 1)
END_DATE = datetime.date(2025, 12, 31)

# Minimum number of seconds between two requests to the same outlet. The outlets are requested in rotation,
# so with all outlets active the waiting time is usually already over when an outlet is next in line
MIN_OUTLET_INTERVAL = 5
# An outlet is skipped for the rest of the session after this many failed requests in a row (e.g. when it blocks us)
MAX_CONSECUTIVE_ERRORS = 5
# Weekly sitemaps (Kurier) are named after the first day of the week, so the last one starting before the period is included
SITEMAP_DATE_TOLERANCE = datetime.timedelta(days=6)
# Number of article IDs per chunk for outlets whose URLs are generated from consecutive IDs
ID_CHUNK_SIZE = 10000

# How the article URLs of each outlet are found. Keys:
#   key:          short name for the --outlets argument
#   index:        sitemap index listing the outlet's article sitemaps
#   sitemap_date: pattern for the year, month (and day) in the name of an article sitemap, used to select the sitemaps
#                 of the period. Sitemaps without a date in their name are all used
#   id_sequence:  URL template and range of consecutive article IDs, used instead of a sitemap
#   article:      pattern which a URL has to match to count as an article. An "id" group can be limited with id_range
#   politics:     pattern for the politics section. Only outlets with the section in their URLs can be filtered this way
#   browser:      whether the articles have to be loaded with Playwright (e.g. because of a consent wall)
OUTLETS = {
    "ORF": {
        "key": "orf",
        # orf.at has no sitemap (and the Wayback Machine times out for it), but its story IDs are consecutive.
        # 3149280 was published on 31 December 2019 and 3415880 on 1 January 2026 (found by checking the dates of a few stories)
        "id_sequence": ("https://orf.at/stories/{id}/", 3149280, 3415880),
    },
    "Kronen Zeitung": {
        "key": "krone",
        "index": "https://www.krone.at/sitemap-articles.xml",
        "sitemap_date": r"sitemap-articles-(?P<year>\d{4})-(?P<month>\d{2})\.xml$",
    },
    "Heute": {
        "key": "heute",
        # The sitemaps of Heute are numbered instead of dated. Articles from before the end of April 2020 have IDs below
        # 100000000 and all carry the date of a website relaunch in September 2021, so they cannot be dated.
        # Articles from 2026 start at roughly 120152850 (found by checking the dates of a few articles)
        "index": "https://www.heute.at/storage/sitemaps/sitemap.xml",
        "article": r"^https://www\.heute\.at/s/.+-(?P<id>\d+)$",
        "id_range": (100000000, 120153000),
    },
    "Österreich – oe24": {
        "key": "oe24",
        "index": "https://www.oe24.at/sitemap-article.xml",
        "sitemap_date": r"sitemap-article-(?P<year>\d{4})-(?P<month>\d{2})\.xml$",
        "politics": r"^https://www\.oe24\.at/(oesterreich/politik|welt/weltpolitik)/",
    },
    "Kurier": {
        "key": "kurier",
        "index": "https://kurier.at/sitemaps_article.xml",
        "sitemap_date": r"sitemaps_article_(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\.xml$",
        "politics": r"^https://kurier\.at/politik/",
    },
    "Kleine Zeitung": {
        "key": "kleine",
        "index": "https://www.kleinezeitung.at/sitemap-articles.xml",
        "sitemap_date": r"sitemap-articles/(?P<year>\d{4})/(?P<month>\d{2})\.xml$",
    },
    "Der Standard": {
        "key": "standard",
        "index": "https://www.derstandard.at/sitemaps/sitemap.xml",
        "sitemap_date": r"sitemap-(?P<year>\d{4})-(?P<month>\d{2})\.xml$",
        # Live tickers are located under /jetzt/
        "article": r"^https://www\.derstandard\.at/story/",
        "browser": True,
    },
    "Die Presse": {
        "key": "presse",
        "index": "https://www.diepresse.com/sitemap",
        "sitemap_date": r"/sitemap/(?P<year>\d{4})/(?P<month>\d{2})$",
    },
}

# Possible statuses of an article URL besides the results of save_article
NOT_FOUND = 'not_found'
FAILED = 'failed'
NO_TEXT = 'no_text'
OTHER_DOMAIN = 'other_domain'
EXCLUDED = 'excluded'
NO_DATE = 'no_date'
OUTSIDE_PERIOD = 'outside_period'
NO_MATCH = 'no_match'


def main(max_articles: Optional[int] = None, outlet_keys: Optional[List[str]] = None, list_only: bool = False,
         retry_failed: bool = False, dry_run: bool = DRY_RUN):
    """
    Runs the scraper in two steps, both of which can be paused with Ctrl+C and resumed by running the script again:
    1. Collects the article URLs of the period from the outlets' sitemaps (only the politics section where possible).
    2. Downloads the articles and saves those mentioning a minister or ministry that was in office at the time.
    The outlets are requested in rotation (one sitemap or article per outlet and turn), so every outlet is covered
    even if the scraper is stopped early, and the articles are checked in a fixed random order, so that they are
    spread over the whole period.

    Args:
        max_articles: The maximum number of articles to check in this session. Checks all remaining ones if None.
        outlet_keys: The keys of the outlets to scrape (e.g. ["orf", "krone"]). Scrapes all outlets if None.
        list_only: Whether to only collect the article URLs.
        retry_failed: Whether to check articles again that could not be loaded in previous sessions.
        dry_run: Whether to use a temporary in-memory database, only read one sitemap and check two articles per outlet.
    """

    outlets = select_outlets(outlet_keys)

    logger.info("Loading data from CSVs...")
    df_min, df_syn = load_and_prepare_data()
    terms_by_day = get_terms_by_day(df_min, df_syn, START_DATE, END_DATE)

    if dry_run:
        logger.info("DRY RUN ENABLED. Using a temporary database, reading one sitemap and checking two articles per outlet.")
        conn = sqlite3.connect(':memory:')
        max_articles = 2 * len(outlets) if max_articles is None else max_articles
    else:
        conn = sqlite3.connect(DB_PATH)
    create_tables(conn.cursor())
    conn.commit()

    if retry_failed:
        conn.execute('UPDATE sitemap_urls SET status = NULL WHERE status = ?', (FAILED,))
        conn.commit()

    news_scraper.pause_requested = False
    previous_handler = signal.signal(signal.SIGINT, request_pause)
    throttle = Throttle()
    browser = LazyBrowser()

    try:
        list_sitemaps(conn, outlets, throttle, max_files=1 if dry_run else None)
        if not list_only and not news_scraper.pause_requested:
            check_articles(conn, outlets, terms_by_day, throttle, browser, max_articles)
    finally:
        signal.signal(signal.SIGINT, previous_handler)
        browser.close()
        log_progress(conn, outlets)
        conn.close()

    if news_scraper.pause_requested:
        logger.info("Scraper paused. Progress is saved in the database, run the script again to resume.")
    else:
        logger.info("Scraping session finished.")

#------------------------- HELPER FUNCTIONS -------------------------#

class Throttle:
    """
    Keeps track of when each outlet was last requested, so that it is requested at most every MIN_OUTLET_INTERVAL seconds.
    """

    def __init__(self, interval: float = MIN_OUTLET_INTERVAL):
        self.interval = interval
        self.last_request: Dict[str, float] = {}

    def wait_for_turn(self, medium: str) -> bool:
        """
        Waits until the outlet may be requested again. Returns False if a pause was requested in the meantime.
        """

        remaining = self.last_request.get(medium, -self.interval) + self.interval - time.monotonic()
        if remaining > 0 and not wait(remaining + random.uniform(0, 1)):
            return False
        self.last_request[medium] = time.monotonic()
        return not news_scraper.pause_requested


class LazyBrowser:
    """
    Starts Playwright only when an outlet that needs it is actually requested.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None

    def page(self):
        if self._page is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._page = self._browser.new_context(user_agent=USER_AGENT).new_page()
        return self._page

    def close(self):
        try:
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception as e:
            logger.debug(f"Could not close browser cleanly: {e}")


def select_outlets(outlet_keys: Optional[List[str]]) -> Dict[str, Dict]:
    """
    Returns the configurations of the outlets with the given keys, or of all outlets if no keys are given.
    """

    if not outlet_keys:
        return OUTLETS
    unknown = set(outlet_keys) - {cfg['key'] for cfg in OUTLETS.values()}
    if unknown:
        raise ValueError(f"Unknown outlets: {sorted(unknown)}. Available: {[cfg['key'] for cfg in OUTLETS.values()]}")
    return {medium: cfg for medium, cfg in OUTLETS.items() if cfg['key'] in outlet_keys}


def get_terms_by_day(df_min: pd.DataFrame, df_syn: pd.DataFrame, start: datetime.date, end: datetime.date) -> Dict[datetime.date, List[str]]:
    """
    Returns the names of the ministers and the ministry synonyms that were valid on each day of the period.
    """

    terms_by_day = {}
    day = start
    while day <= end:
        ts = pd.Timestamp(day)
        active_min = df_min[(df_min['start'] <= ts) & (df_min['end'] >= ts)]
        active_syn = df_syn[(df_syn['start'] <= ts) & (df_syn['end'] >= ts)]
        terms_by_day[day] = sorted(set(active_min['name']).union(set(active_syn['synonym'])))
        day += datetime.timedelta(days=1)
    return terms_by_day


def fetch(url: str, timeout: float = 30) -> Tuple[bytes, str]:
    """
    Downloads the given URL and returns its content and the final URL after redirects.
    """

    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Language': 'de-AT,de;q=0.9'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(), response.geturl()


def parse_sitemap(xml: bytes) -> List[Tuple[str, Optional[str]]]:
    """
    Returns the (loc, lastmod) pairs of a sitemap or sitemap index.
    """

    root = ET.fromstring(xml)
    entries = []
    for element in root:
        loc = element.findtext('{*}loc')
        if loc:
            entries.append((loc.strip(), element.findtext('{*}lastmod')))
    return entries


def select_sitemaps(locs: List[str], pattern: Optional[str], start: datetime.date, end: datetime.date) -> List[str]:
    """
    Returns the sitemaps whose names lie within the period, or all of them if the outlet has no dated sitemaps.
    """

    if not pattern:
        return list(locs)

    selected = []
    for loc in locs:
        m = re.search(pattern, loc)
        if not m:
            continue
        year, month = int(m['year']), int(m['month'])
        if m.groupdict().get('day'):
            day = datetime.date(year, month, int(m['day']))
            if start - SITEMAP_DATE_TOLERANCE <= day <= end:
                selected.append(loc)
        elif (start.year, start.month) <= (year, month) <= (end.year, end.month):
            selected.append(loc)
    return selected


def get_id_chunks(template: str, low: int, high: int) -> List[str]:
    """
    Splits the ID range into chunks, which are stored like sitemaps, e.g. "https://orf.at/stories/{id}/#3149280-3159280".
    """

    return [f"{template}#{start}-{min(start + ID_CHUNK_SIZE, high)}" for start in range(low, high, ID_CHUNK_SIZE)]


def get_sitemap_files(cfg: Dict, start: datetime.date, end: datetime.date) -> List[str]:
    """
    Returns the sitemaps (or ID chunks) that list the outlet's articles of the period.
    """

    if 'id_sequence' in cfg:
        return get_id_chunks(*cfg['id_sequence'])
    xml, _ = fetch(cfg['index'])
    return select_sitemaps([loc for loc, _ in parse_sitemap(xml)], cfg.get('sitemap_date'), start, end)


def get_sitemap_entries(cfg: Dict, file_url: str) -> List[Tuple[str, Optional[str]]]:
    """
    Downloads a sitemap and returns its (url, lastmod) pairs. For ID chunks, the URLs are generated without a request.
    """

    if 'id_sequence' in cfg:
        template, ids = file_url.rsplit('#', 1)
        low, high = map(int, ids.split('-'))
        return [(template.format(id=i), None) for i in range(low, high)]
    content, _ = fetch(file_url, timeout=120)
    return parse_sitemap(content)


def filter_article_urls(cfg: Dict, entries: List[Tuple[str, Optional[str]]]) -> List[Tuple[str, Optional[str]]]:
    """
    Keeps the URLs which are articles (of the politics section, if the outlet can be filtered by it).
    Each URL is returned only once.
    """

    results = {}
    for url, lastmod in entries:
        if 'article' in cfg:
            m = re.search(cfg['article'], url)
            if not m:
                continue
            if 'id_range' in cfg:
                low, high = cfg['id_range']
                if not low <= int(m['id']) < high:
                    continue

        if 'politics' in cfg and not re.search(cfg['politics'], url):
            continue
        if EXCLUDED_URLS.match(url):
            continue
        results.setdefault(url, lastmod)
    return list(results.items())


def get_sort_key(url: str) -> int:
    """
    Returns a pseudo-random but fixed number for the URL, so that the articles are checked in a random order
    which stays the same across sessions.
    """

    return int.from_bytes(hashlib.sha1(url.encode('utf-8')).digest()[:7], 'big')


def list_sitemaps(conn: sqlite3.Connection, outlets: Dict[str, Dict], throttle: Throttle, max_files: Optional[int] = None):
    """
    Step 1: Adds the article URLs from all sitemaps that have not been read yet to sitemap_urls, one sitemap per outlet and turn.
    Sitemaps that cannot be downloaded are tried again in the next session.
    """

    pending = {}
    for medium, cfg in outlets.items():
        if 'id_sequence' not in cfg and not throttle.wait_for_turn(medium):
            return
        try:
            files = get_sitemap_files(cfg, START_DATE, END_DATE)
        except Exception as e:
            logger.error(f"[{medium}] Could not read the sitemap index, skipping the outlet for now: {e}")
            continue
        listed = {row[0] for row in conn.execute('SELECT url FROM sitemap_files WHERE medium = ?', (medium,))}
        todo = [f for f in files if f not in listed]
        logger.info(f"[{medium}] {len(files) - len(todo)} of {len(files)} sitemaps already read.")
        todo = todo[:max_files]
        if todo:
            pending[medium] = todo

    errors = Counter()
    total = sum(len(files) for files in pending.values())
    done = 0
    while pending:
        for medium in list(pending):
            cfg = outlets[medium]
            if 'id_sequence' not in cfg and not throttle.wait_for_turn(medium):
                return
            if news_scraper.pause_requested:
                return
            file_url = pending[medium].pop(0)
            done += 1
            try:
                entries = get_sitemap_entries(cfg, file_url)
            except Exception as e:
                errors[medium] += 1
                logger.error(f"[{done}/{total}] [{medium}] Could not read {file_url}: {e}")
                if errors[medium] >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(f"[{medium}] {errors[medium]} sitemaps failed in a row, skipping the outlet for the rest of the session.")
                    pending[medium] = []
            else:
                errors[medium] = 0
                urls = filter_article_urls(cfg, entries)
                conn.executemany('INSERT OR IGNORE INTO sitemap_urls (url, medium, lastmod, sort_key) VALUES (?, ?, ?, ?)',
                                 [(url, medium, lastmod, get_sort_key(url)) for url, lastmod in urls])
                conn.execute('INSERT OR REPLACE INTO sitemap_files (url, medium, url_count, listed_at) VALUES (?, ?, ?, ?)',
                             (file_url, medium, len(urls), now()))
                conn.commit()
                logger.info(f"[{done}/{total}] [{medium}] Kept {len(urls)} of {len(entries)} URLs from {file_url}")

            if not pending[medium]:
                del pending[medium]


def check_articles(conn: sqlite3.Connection, outlets: Dict[str, Dict], terms_by_day: Dict[datetime.date, List[str]],
                   throttle: Throttle, browser: LazyBrowser, max_articles: Optional[int] = None):
    """
    Step 2: Checks the pending article URLs, one article per outlet and turn, and saves the ones mentioning a minister
    or ministry. If an outlet fails several times in a row, it is skipped for the rest of the session and its failed
    articles are tried again in the next session.
    """

    active = [medium for medium in outlets if get_next_url(conn, medium)]
    stats = Counter()
    recent_failures: Dict[str, List[str]] = {medium: [] for medium in active}
    checked = 0

    while active:
        for medium in list(active):
            if max_articles is not None and checked >= max_articles:
                return log_stats(stats)
            row = get_next_url(conn, medium)
            if row is None:
                active.remove(medium)
                continue
            if not throttle.wait_for_turn(medium):
                return log_stats(stats)

            url, lastmod = row
            checked += 1
            status = check_article(conn, medium, outlets[medium], url, lastmod, terms_by_day, browser)
            set_status(conn, url, status)
            stats[status] += 1
            logger.info(f"[{checked}] [{medium}] {status}: {url}")

            if status == FAILED:
                recent_failures[medium].append(url)
                if len(recent_failures[medium]) >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(f"[{medium}] {len(recent_failures[medium])} articles failed in a row, skipping the outlet for the rest of the session.")
                    for failed_url in recent_failures[medium]:
                        set_status(conn, failed_url, None)
                    active.remove(medium)
            else:
                recent_failures[medium] = []

    log_stats(stats)


def get_next_url(conn: sqlite3.Connection, medium: str) -> Optional[Tuple[str, Optional[str]]]:
    """
    Returns the next unchecked (url, lastmod) pair of the outlet, or None if all of its articles have been checked.
    """

    return conn.execute('SELECT url, lastmod FROM sitemap_urls WHERE medium = ? AND status IS NULL ORDER BY sort_key LIMIT 1',
                        (medium,)).fetchone()


def set_status(conn: sqlite3.Connection, url: str, status: Optional[str]):
    conn.execute('UPDATE sitemap_urls SET status = ?, checked_at = ? WHERE url = ?', (status, now() if status else None, url))
    conn.commit()


def check_article(conn: sqlite3.Connection, medium: str, cfg: Dict, url: str, lastmod: Optional[str],
                  terms_by_day: Dict[datetime.date, List[str]], browser: LazyBrowser) -> str:
    """
    Downloads an article, checks whether it lies within the period and mentions a minister or ministry that was in office
    at the time, and saves it if so. Returns the resulting status of the URL.
    """

    try:
        if cfg.get('browser'):
            content = extract_article(url, browser.page())
            if not content:
                return FAILED
        else:
            html, final_url = fetch(url)
            content = parse_article(html.decode('utf-8', errors='replace'), final_url)
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return NOT_FOUND
        logger.warning(f"[{medium}] Could not load {url}: {e}")
        return FAILED
    except Exception as e:
        logger.warning(f"[{medium}] Could not load {url}: {e}")
        return FAILED

    if not content.get('full_text'):
        return NO_TEXT
    if get_domain(content['url']) != get_domain(url):
        return OTHER_DOMAIN
    if EXCLUDED_URLS.match(content['url']):
        return EXCLUDED

    date = content.get('date') or normalize_lastmod(lastmod)
    if not date:
        return NO_DATE
    try:
        day = datetime.date.fromisoformat(date[:10])
    except ValueError:
        return NO_DATE
    if day not in terms_by_day:
        return OUTSIDE_PERIOD
    content['date'] = date

    # The search term might only occur in the headline, so it is checked as well
    text = f"{content.get('headline', '')}\n{content['full_text']}"
    matched = [term for term in terms_by_day[day] if is_valid_context(text, term)]
    if not matched:
        return NO_MATCH

    logger.info(f"[{medium}] Article mentions {matched}: {content.get('headline', '')[:60]}")
    return save_article(conn, medium, content, matched)


def get_domain(url: str) -> str:
    """
    Returns the domain of a URL without "www.", e.g. "orf.at".
    """

    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith('www.') else host


def normalize_lastmod(lastmod: Optional[str]) -> Optional[str]:
    """
    Turns the lastmod date of a sitemap (e.g. 2020-04-01T06:10:08.000Z) into the date format of the database.
    It is only used if the article page has no date.
    """

    if not lastmod:
        return None
    try:
        return datetime.datetime.fromisoformat(lastmod.strip()).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def save_article(conn: sqlite3.Connection, medium: str, content: Dict[str, str], terms: List[str]) -> str:
    """
    Validates the article and saves it to sitemap_articles together with the terms it mentions.
    Returns SAVED, DUPLICATE (if the URL already exists), INVALID (if the validation failed) or DB_ERROR.
    """

    try:
        article = NewsArticle.model_validate({
            'medium': medium,
            'url': content['url'],
            'date': content.get('date'),
            'headline': content.get('headline'),
            'full_text': content.get('full_text', ''),
            'section': content.get('section')
        })
    except ValidationError as e:
        logger.warning(f"Article failed validation and was not saved: {content['url']}\n{e}")
        return INVALID

    try:
        cursor = conn.execute('''
        INSERT OR IGNORE INTO sitemap_articles (medium, date, headline, full_text, url, section, terms)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (article.medium, article.date, article.headline, article.full_text, str(article.url), article.section, '; '.join(terms)))
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save article to DB: {e}")
        return DB_ERROR
    return SAVED if cursor.rowcount > 0 else DUPLICATE


def log_stats(stats: Counter):
    if stats:
        logger.info("Checked articles in this session: " + ", ".join(f"{count} {status}" for status, count in stats.most_common()))


def log_progress(conn: sqlite3.Connection, outlets: Dict[str, Dict]):
    """
    Logs how many articles of each outlet have been checked and saved so far.
    """

    for medium in outlets:
        total, checked = conn.execute('SELECT COUNT(*), COUNT(status) FROM sitemap_urls WHERE medium = ?', (medium,)).fetchone()
        saved = conn.execute('SELECT COUNT(*) FROM sitemap_articles WHERE medium = ?', (medium,)).fetchone()[0]
        share = f"{checked / total:.1%}" if total else "-"
        logger.info(f"[{medium}] {checked} of {total} articles checked ({share}), {saved} saved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrapes news articles mentioning Austrian ministries from the outlets' sitemaps. "
                                                 "Press Ctrl+C to pause, run again to resume.")
    parser.add_argument('-n', '--max-articles', type=int, default=None, help="Maximum number of articles to check in this session (default: all remaining).")
    parser.add_argument('--outlets', nargs='+', metavar='KEY', default=None,
                        help=f"Only scrape these outlets (default: all): {', '.join(cfg['key'] for cfg in OUTLETS.values())}.")
    parser.add_argument('--list-only', action='store_true', help="Only collect the article URLs from the sitemaps.")
    parser.add_argument('--retry-failed', action='store_true', help="Check articles again that could not be loaded in previous sessions.")
    parser.add_argument('--dry-run', action=argparse.BooleanOptionalAction, default=DRY_RUN,
                        help=f"Use a temporary database, read one sitemap and check two articles per outlet (default: {DRY_RUN}).")
    args = parser.parse_args()
    main(max_articles=args.max_articles, outlet_keys=args.outlets, list_only=args.list_only,
         retry_failed=args.retry_failed, dry_run=args.dry_run)
