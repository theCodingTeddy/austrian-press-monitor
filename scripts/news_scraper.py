from setup_db import logger, DATA_DIR, DB_PATH
from schemas import MinisterSchema, SynonymSchema, NewsArticle

import pandas as pd
from pydantic import ValidationError
import sqlite3
from typing import Tuple, List, Dict, Set, Optional
import argparse
from collections import Counter
import email.utils
import random
import signal
import time
import datetime
import dateparser
import re
import trafilatura
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright

DRY_RUN = False
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Student Research Project - Sentiment Analysis of Austrian Media"

MINISTERS_CSV = DATA_DIR / 'synonym_lists' / 'ministers.csv'
SYNONYMS_CSV = DATA_DIR / 'synonym_lists' / 'ministry_synonyms.csv'
PROGRESS_FILE = DATA_DIR / 'news_scraper_progress.json'

# Google News RSS is used for discovery, as the regular Google search throws CAPTCHAs at automated clients
RSS_URL = "https://news.google.com/rss/search?q={query}&hl=de&gl=AT&ceid=AT:de"
RSS_MAX_RESULTS = 100
# Google ignores the end of longer queries (from roughly 200 characters on), which drops the after:/before: operators
MAX_QUERY_LENGTH = 180
# RSS dates are in GMT, so articles published shortly after midnight might appear as the previous day
DATE_TOLERANCE = datetime.timedelta(days=1)
# Google temporarily blocks clients after many searches (HTTP 503), so failed requests are retried after these waiting times (in seconds)
RSS_RETRY_DELAYS = [60, 300, 900]
# Pages which are not news articles: ORF subdomains for the TV programme, the company itself, its streaming platform, presenter
# profiles and teletext, as well as image, gallery, tag and author pages of Heute (its articles are located under /s/)
EXCLUDED_URLS = re.compile(r"^https?://(tv|der|zukunft|on|stars|text)\.orf\.at/|^https?://www\.heute\.at/[agit]/")
CONSENT_BUTTON_TEXT = re.compile(r"^\s*(Accept all|Alle akzeptieren|I agree|Zustimmen|Einverstanden|Akzeptieren)\s*$", re.IGNORECASE)

OUTLETS = {
    "ORF": "orf.at",
    "Kronen Zeitung": "krone.at",
    "Heute": "heute.at",
    "Österreich – oe24": "oe24.at",
    "Kurier": "kurier.at",
    "Kleine Zeitung": "kleinezeitung.at",
    "Der Standard": "derstandard.at",
    "Die Presse": "diepresse.com"
}

# Possible results of save_article_to_db
SAVED = 'saved'
DUPLICATE = 'duplicate'
INVALID = 'invalid'
DB_ERROR = 'db_error'

pause_requested = False


def main(max_queries: Optional[int] = None, reset: bool = False, dry_run: bool = DRY_RUN):
    """
    Runs the scraper. Progress is saved after every finished query, so the search can be paused with
    Ctrl+C and resumed by running the script again.

    Args:
        max_queries: The maximum number of (outlet, query) pairs to run in this session. Runs all remaining ones if None.
        reset: Whether to discard the saved progress and start from scratch.
        dry_run: Whether to limit the queries and skip saving articles and progress.
    """

    logger.info("Loading data from CSVs...")
    df_min, df_syn = load_and_prepare_data()

    start_date = datetime.datetime(2020, 1, 1)
    end_date = datetime.datetime(2025, 12, 31)

    logger.info("Generating queries...")
    queries = get_queries(df_min, df_syn, start_date, end_date)
    outlets = OUTLETS

    if dry_run:
        logger.info("DRY RUN ENABLED. Limiting queries to the first outlet, not saving to DB and not saving progress.")
        queries = queries[:10]
        outlets = dict(list(OUTLETS.items())[:1])

    logger.info(f"Generated {len(queries)} total query batches.")

    done = set() if reset or dry_run else load_progress()
    stats = Counter()
    tasks = [(outlet_name, site_domain, q) for outlet_name, site_domain in outlets.items() for q in queries]
    pending = [t for t in tasks if get_query_key(t[1], t[2]) not in done]
    if max_queries is not None:
        pending = pending[:max_queries]
    logger.info(f"{len(tasks) - len(pending)} of {len(tasks)} searches already done or skipped, running {len(pending)} now.")

    global pause_requested
    pause_requested = False
    previous_handler = signal.signal(signal.SIGINT, request_pause)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        try:
            for i, (outlet_name, site_domain, q) in enumerate(pending, start=1):
                terms = q['terms']
                logger.info(f"[{i}/{len(pending)}] Searching {site_domain} for {terms} between {q['start_str']} and {q['end_str']}")

                links = fetch_google_news_rss(
                    site=site_domain,
                    terms=terms,
                    after=q['start_str'],
                    before=q['end_str']
                )
                if links is None:
                    logger.error("Google News could not be reached, pausing the search. Run the script again later to resume.")
                    pause_requested = True
                    break
                logger.info(f" +-- Found {len(links)} links")

                for link, rss_headline, rss_date in links:
                    if pause_requested:
                        break

                    # Safety net in case Google ignores the date range of the query
                    if not is_in_date_range(rss_date, q['start_str'], q['end_str']):
                        logger.info(f" +--+--- Skipped article from {rss_date} outside of the searched period: {rss_headline[:50]}")
                        stats['outside_period'] += 1
                        continue

                    logger.info(f" +--+--- Extracting: {link}")
                    content = extract_article(link, page)

                    if not content or not content.get('full_text'):
                        stats['extraction_failed'] += 1
                    else:
                        host = urllib.parse.urlparse(content['url']).netloc
                        if site_domain not in host:
                            logger.info(f" +--+--- Skipped article from another domain: {content['url']}")
                            stats['other_domain'] += 1
                            continue
                        if EXCLUDED_URLS.match(content['url']):
                            logger.info(f" +--+--- Skipped page without news content: {content['url']}")
                            stats['excluded_url'] += 1
                            continue

                        content['headline'] = content.get('headline') or rss_headline
                        content['date'] = choose_date(content.get('date'), rss_date, q['start_str'], q['end_str'])

                        # The search term might only occur in the headline, so it is checked as well
                        text = f"{content['headline']}\n{content['full_text']}"
                        valid = any(is_valid_context(text, term) for term in terms)

                        if valid:
                            logger.info(f" +--+--- Valid article found: {content.get('headline', '')[:50]}")
                            stats['valid'] += 1
                            if not dry_run:
                                stats[save_article_to_db(outlet_name, content['url'], content)] += 1
                        else:
                            logger.info(f" +--+--- Article rejected by context filter: {content['url']}")
                            stats['context_filter'] += 1

                # An interrupted query is not marked as done, so it is repeated in full when resuming
                if pause_requested:
                    break

                done.add(get_query_key(site_domain, q))
                if not dry_run:
                    save_progress(done)

                time.sleep(random.uniform(2, 5))
        finally:
            signal.signal(signal.SIGINT, previous_handler)
            try:
                browser.close()
            except Exception as e:
                logger.debug(f"Could not close browser cleanly: {e}")

    logger.info(f"Skipped {stats['outside_period']} links outside of the searched period, {stats['extraction_failed']} could not be extracted, "
                f"{stats['other_domain']} were from another domain, {stats['excluded_url']} had no news content and {stats['context_filter']} were rejected by the context filter.")
    if dry_run:
        logger.info(f"Found {stats['valid']} valid articles. Nothing was saved, because DRY RUN is enabled (use --no-dry-run to save).")
    else:
        logger.info(f"Found {stats['valid']} valid articles: {stats[SAVED]} saved, {stats[DUPLICATE]} already in the DB, "
                    f"{stats[INVALID]} failed validation, {stats[DB_ERROR]} could not be saved due to a DB error.")

    if pause_requested:
        logger.info(f"Search paused. Progress saved to {PROGRESS_FILE}, run the script again to resume.")
    else:
        logger.info("Search session finished.")

#------------------------- HELPER FUNCTIONS -------------------------#

def load_and_prepare_data() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Loads minister and synonym data, validates it, and prepares it for use in the scraper.
    """

    df_min = pd.read_csv(MINISTERS_CSV)
    df_syn = pd.read_csv(SYNONYMS_CSV)

    MinisterSchema.validate(df_min)
    SynonymSchema.validate(df_syn)

    # A bucket that only occurs in one of the files is most likely a typo
    min_buckets, syn_buckets = set(df_min['bucket']), set(df_syn['bucket'])
    if min_buckets != syn_buckets:
        raise ValueError(f"Buckets do not match between {MINISTERS_CSV.name} and {SYNONYMS_CSV.name}: "
                         f"only in ministers: {sorted(min_buckets - syn_buckets)}, only in synonyms: {sorted(syn_buckets - min_buckets)}")

    df_min['start'] = pd.to_datetime(df_min['start_of_period'], format='%m/%d/%y')
    df_min['end'] = pd.to_datetime(df_min['end_of_period'], format='%m/%d/%y')

    df_syn['start'] = pd.to_datetime(df_syn['start_of_period'], format='%m/%d/%y')
    df_syn['end'] = pd.to_datetime(df_syn['end_of_period'], format='%m/%d/%y')

    return df_min, df_syn

def get_queries(df_min: pd.DataFrame, df_syn: pd.DataFrame, start_date: datetime.datetime, end_date: datetime.datetime) -> List[Dict]:
    """
    Generates a list of queries to search for news articles using Google News, within the specified date range.
    Buckets and terms are sorted so that the batches are identical across runs, which is needed for resuming.
    """

    queries = []
    current = start_date
    while current < end_date:
        year = current.year + (current.month // 12)
        month = (current.month % 12) + 1
        next_month_start = datetime.datetime(year, month, 1)
        next_month = next_month_start - datetime.timedelta(days=1)
        if next_month > end_date:
            next_month = end_date

        buckets = sorted(set(df_min['bucket'].unique()).union(set(df_syn['bucket'].unique())))

        for bucket in buckets:
            active_min = df_min[(df_min['bucket'] == bucket) & (df_min['start'] <= next_month) & (df_min['end'] >= current)]
            active_syn = df_syn[(df_syn['bucket'] == bucket) & (df_syn['start'] <= next_month) & (df_syn['end'] >= current)]

            terms = set(active_min['name'].unique()).union(set(active_syn['synonym'].unique()))
            terms = sorted(terms)

            start_str = current.strftime("%Y-%m-%d")
            end_str = next_month.strftime("%Y-%m-%d")
            for batch in batch_terms(terms, start_str, end_str):
                queries.append({
                    'bucket': bucket,
                    'start_str': start_str,
                    'end_str': end_str,
                    'terms': batch
                })

        current = next_month + datetime.timedelta(days=1)

    return queries


def build_query(site: str, terms: List[str], after: str, before: str) -> str:
    """
    Builds a Google News search query for the given site, terms and period (both dates inclusive).
    """

    # Google's before: operator is exclusive, so we move it by one day to include the last day
    before_exclusive = (datetime.datetime.strptime(before, "%Y-%m-%d") + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    terms_query = " OR ".join([f'"{t}"' for t in terms])
    return f'site:{site} ({terms_query}) after:{after} before:{before_exclusive}'


def batch_terms(terms: List[str], after: str, before: str) -> List[List[str]]:
    """
    Splits the terms into batches whose queries stay below MAX_QUERY_LENGTH for every outlet,
    since Google drops the date range of longer queries.
    """

    longest_site = max(OUTLETS.values(), key=len)
    batches = []
    batch = []
    for term in terms:
        if batch and len(build_query(longest_site, batch + [term], after, before)) > MAX_QUERY_LENGTH:
            batches.append(batch)
            batch = []
        batch.append(term)
        if len(batch) == 1 and len(build_query(longest_site, batch, after, before)) > MAX_QUERY_LENGTH:
            logger.warning(f"Term is too long to stay below {MAX_QUERY_LENGTH} characters, the date range might be ignored: {term}")
    if batch:
        batches.append(batch)
    return batches


def is_in_date_range(date: str, start: str, end: str) -> bool:
    """
    Checks if the given date (YYYY-MM-DD HH:MM:SS) lies within the period from start to end (both YYYY-MM-DD),
    allowing for DATE_TOLERANCE on both sides. Returns True if the date is missing.
    """

    if not date:
        return True
    dt = datetime.datetime.strptime(date[:10], "%Y-%m-%d")
    start_dt = datetime.datetime.strptime(start, "%Y-%m-%d") - DATE_TOLERANCE
    end_dt = datetime.datetime.strptime(end, "%Y-%m-%d") + DATE_TOLERANCE
    return start_dt <= dt <= end_dt


def choose_date(page_date: str, rss_date: str, start: str, end: str) -> str:
    """
    Returns the publication date found on the article page if it lies within the searched period, and the date
    from the RSS feed otherwise. Pages without a proper date (e.g. TV programme pages) can make trafilatura fall back
    to a year mentioned somewhere on the page, which then turns into January 1st of that year.
    """

    if page_date and is_in_date_range(page_date, start, end):
        return page_date
    if page_date and rss_date:
        logger.info(f" +--+--- Date {page_date} from the page is outside of the searched period, using {rss_date} from the RSS feed instead")
    return rss_date or page_date


def get_query_key(site: str, query: Dict) -> str:
    """
    Returns a string that uniquely identifies a search of the given site with the given query.
    """

    return f"{site}|{query['start_str']}|{query['end_str']}|{'|'.join(sorted(query['terms']))}"


def load_progress() -> Set[str]:
    """
    Loads the keys of all searches that were finished in previous runs.
    """

    if not PROGRESS_FILE.exists():
        return set()
    try:
        with open(PROGRESS_FILE, encoding='utf-8') as f:
            return set(json.load(f))
    except Exception as e:
        logger.error(f"Could not read progress file {PROGRESS_FILE}, starting from scratch: {e}")
        return set()


def save_progress(done: Set[str]):
    """
    Saves the keys of all finished searches, so the search can be resumed later.
    """

    tmp_file = PROGRESS_FILE.with_suffix('.tmp')
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(sorted(done), f, ensure_ascii=False, indent=2)
    tmp_file.replace(PROGRESS_FILE)


def request_pause(_signum, _frame):
    """
    SIGINT handler: the first Ctrl+C pauses the search after the current article, the second one aborts immediately.
    """

    global pause_requested
    if pause_requested:
        raise KeyboardInterrupt
    pause_requested = True
    logger.warning("Pause requested, stopping after the current article. Press Ctrl+C again to abort immediately.")


def wait(seconds: float) -> bool:
    """
    Sleeps for the given number of seconds, but stops early if a pause was requested. Returns False in that case.
    """

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if pause_requested:
            return False
        time.sleep(min(1, end - time.monotonic()))
    return not pause_requested


def fetch_google_news_rss(site: str, terms: List[str], after: str, before: str) -> Optional[List[Tuple[str, str, str]]]:
    """
    Searches Google News RSS for news articles. Unlike the regular Google search, the RSS feed does not
    throw CAPTCHAs. The returned links point to news.google.com and redirect to the actual article.

    Args:
        site: The website to search for news articles.
        terms: A list of terms to search for news articles.
        after: The start date for news articles (inclusive).
        before: The end date for news articles (inclusive).

    Returns:
        A list of (link, headline, date) tuples, where headline and date come from the RSS feed,
        or None if the feed could not be fetched, even after retrying.
    """

    query = build_query(site, terms, after, before)
    url = RSS_URL.format(query=urllib.parse.quote_plus(query))

    results = []
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    root = None
    for delay in [0] + RSS_RETRY_DELAYS:
        if delay:
            logger.warning(f"Retrying in {delay // 60} minute(s)...")
            if not wait(delay):
                return None
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                root = ET.fromstring(response.read())
            break
        except Exception as e:
            logger.error(f"RSS fetch failed for {terms} on {site} [{after} to {before}]: {e}")
    if root is None:
        return None

    items = root.findall('.//item')
    if len(items) >= RSS_MAX_RESULTS:
        logger.warning(f"Google News returned {len(items)} results, which is its limit. Some articles might be missing.")

    for item in items:
        link = item.findtext('link')
        if not link:
            continue

        # RSS titles look like "<headline> - <outlet>"
        headline = item.findtext('title', '')
        source = item.findtext('source', '')
        if source and headline.endswith(f" - {source}"):
            headline = headline[:-len(f" - {source}")]

        date = ''
        pub_date = item.findtext('pubDate')
        if pub_date:
            try:
                date = email.utils.parsedate_to_datetime(pub_date).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                logger.debug(f"Could not parse RSS date: {pub_date}")

        results.append((link, headline, date))

    return results


def accept_consent(page, attempts: int = 10) -> bool:
    """
    Clicks the accept button of a cookie consent dialog, which might be located in an iframe.
    Returns True if a button was clicked.
    """

    for _ in range(attempts):
        for frame in page.frames:
            try:
                btn = frame.locator('button, [role="button"]').filter(has_text=CONSENT_BUTTON_TEXT).first
                if btn.count() > 0:
                    btn.click()
                    return True
            except Exception as e:
                logger.debug(f"Could not click consent button: {e}")
        page.wait_for_timeout(500)
    return False


def wait_while_url_contains(page, text: str, timeout: float = 15):
    """
    Waits until the page URL no longer contains the given text, e.g. during JavaScript redirects.
    """

    deadline = time.time() + timeout
    while text in page.url and time.time() < deadline:
        page.wait_for_timeout(250)


def contains_term(text: str, keyword: str) -> bool:
    """
    Checks if the given keyword occurs in the text. Abbreviations (e.g. "BMI") have to match a whole word
    with the exact capitalization, while all other terms are matched case-insensitively, also as part of
    a word (e.g. "Innenministerium" in "Innenministeriums").
    """

    if keyword.isupper():
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword.lower() in text.lower()

def is_valid_context(text: str, keyword: str) -> bool:
    """
    Checks if the given keyword occurs in the text and if the text is contextually relevant to it.
    """

    if not text or not contains_term(text, keyword):
        return False

    text_lower = text.lower()
    kw_lower = keyword.lower()

    if kw_lower == "bmi":
        context_words = ['polizei', 'innenminister', 'karner', 'nehammer', 'sicherheit', 'regierung', 'ministerium', 'beamte', 'asyl']
        if not any(word in text_lower for word in context_words):
            return False
        if 'übergewicht' in text_lower or 'body mass index' in text_lower or 'adipositas' in text_lower:
            return False

    if kw_lower == "bka":
        context_words = ['bundeskanzler', 'regierung', 'kanzler', 'minister']
        if not any(word in text_lower for word in context_words):
            return False

    # TODO: improve context checking by adding more keywords

    return True

def extract_article(url: str, page) -> Dict[str, str]:
    """
    Opens a Google News link with Playwright, follows the redirect to the newspaper's website (accepting
    cookie dialogs on the way) and returns a dict containing the final URL, headline, full text, date and section.
    If the extraction fails, it returns an empty dict.
    """

    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)

        if "consent.google.com" in page.url:
            accept_consent(page)

        # Google News links redirect to the article via JavaScript
        wait_while_url_contains(page, "google.com")
        if "google.com" in page.url:
            logger.warning(f"Google News link did not redirect: {url}")
            return {}
        page.wait_for_load_state('domcontentloaded')

        # Some outlets (e.g. Der Standard) only show a teaser until the consent dialog is accepted
        if "/consent/" in page.url and accept_consent(page):
            wait_while_url_contains(page, "/consent/", timeout=10)
            page.wait_for_load_state('domcontentloaded')

        final_url = page.url
        html = page.content()
    except Exception as e:
        logger.error(f"Failed to load {url}: {e}")
        return {}

    return parse_article(html, final_url)

def parse_article(html: str, final_url: str) -> Dict[str, str]:
    """
    Extracts headline, full text, date and section from the HTML of an article page with trafilatura.
    Returns a dict containing these and the URL, or an empty dict if the extraction fails.
    """

    result = trafilatura.extract(
        html,
        url=final_url,
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        output_format='json'
    )

    if result:
        try:
            data = json.loads(result)
            dt = data.get('date')
            if dt:
                parsed = dateparser.parse(dt)
                if parsed:
                    data['date'] = parsed.strftime("%Y-%m-%d %H:%M:%S")
            return {
                "url": final_url,
                "headline": data.get('title') or '',
                "full_text": data.get('text') or '',
                "date": data.get('date') or '',
                # Read from the page's metadata (e.g. "Politik"). Not every outlet provides it, the ORF for example does not
                "section": data.get('categories') or ''
            }
        except Exception as e:
            logger.error(f"Failed to parse Trafilatura JSON for {final_url}: {e}")

    return {}

def save_article_to_db(outlet: str, url: str, article_data: Dict[str, str]) -> str:
    """
    Validates the provided article data (including outlet, URL, headline, body text, date and section) and saves it to the database.
    Returns SAVED, DUPLICATE (if the URL already exists), INVALID (if the validation failed) or DB_ERROR.
    """

    try:
        article = NewsArticle.model_validate({
            'medium': outlet,
            'url': url,
            'date': article_data.get('date'),
            'headline': article_data.get('headline'),
            'full_text': article_data.get('full_text', ''),
            'section': article_data.get('section')
        })
    except ValidationError as e:
        logger.warning(f"Article failed validation and was not saved: {url}\n{e}")
        return INVALID

    result = DB_ERROR
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
        INSERT OR IGNORE INTO news_articles (medium, date, headline, full_text, url, section)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            article.medium,
            article.date,
            article.headline,
            article.full_text,
            str(article.url),
            article.section
        ))

        if cursor.rowcount > 0:
            logger.info(f"Saved new article to DB: {url}")
            result = SAVED
        else:
            logger.info(f"Article already exists in DB: {url}")
            result = DUPLICATE

        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save article to DB: {e}")
        result = DB_ERROR
    finally:
        if conn is not None:
            conn.close()

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrapes news articles mentioning Austrian ministries. Press Ctrl+C to pause, run again to resume.")
    parser.add_argument('-n', '--max-queries', type=int, default=None, help="Maximum number of searches to run in this session (default: all remaining).")
    parser.add_argument('--reset', action='store_true', help="Discard saved progress and start from scratch.")
    parser.add_argument('--dry-run', action=argparse.BooleanOptionalAction, default=DRY_RUN,
                        help=f"Limit the queries and do not save anything (default: {DRY_RUN}).")
    args = parser.parse_args()
    main(max_queries=args.max_queries, reset=args.reset, dry_run=args.dry_run)
