import sys
from pathlib import Path

# Add the scripts directory to the python path
scripts_path = Path(__file__).parent.parent / 'scripts'
sys.path.append(str(scripts_path))

import datetime
import sqlite3
import urllib.error
import pandas as pd
import pytest
from unittest.mock import patch

import setup_db
import news_scraper
import sitemap_scraper
from sitemap_scraper import (
    select_outlets,
    get_terms_by_day,
    parse_sitemap,
    select_sitemaps,
    get_id_chunks,
    get_sitemap_entries,
    filter_article_urls,
    get_sort_key,
    normalize_lastmod,
    list_sitemaps,
    check_articles,
    check_article,
    get_next_url,
    Throttle,
    OUTLETS,
    FAILED,
    NOT_FOUND,
    NO_MATCH,
    NO_DATE,
    OUTSIDE_PERIOD,
    OTHER_DOMAIN,
    EXCLUDED,
)
from news_scraper import SAVED, DUPLICATE, INVALID

LONG_TEXT = "Der Innenminister Karner hat heute die Polizei besucht. " * 10
DAY = datetime.date(2020, 1, 10)
TERMS_BY_DAY = {DAY: ['Innenminister', 'Minister A']}

#------------------------- FIXTURES -------------------------#

@pytest.fixture
def conn():
    """An in-memory database with all tables."""
    conn = sqlite3.connect(':memory:')
    setup_db.create_tables(conn.cursor())
    yield conn
    conn.close()

@pytest.fixture
def no_wait():
    """A throttle that never waits, and a reset pause flag."""
    news_scraper.pause_requested = False
    yield Throttle(interval=0)
    news_scraper.pause_requested = False

def make_sitemap(locs, tag='url'):
    entries = "".join(f"<{tag}><loc>{loc}</loc><lastmod>2020-01-10T08:00:00Z</lastmod></{tag}>" for loc in locs)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'.encode()

def add_urls(conn, rows):
    """Adds (url, medium) pairs to sitemap_urls, in the given order."""
    conn.executemany('INSERT INTO sitemap_urls (url, medium, lastmod, sort_key) VALUES (?, ?, NULL, ?)',
                     [(url, medium, i) for i, (url, medium) in enumerate(rows)])
    conn.commit()

def article(url, date="2020-01-10 08:00:00", text=LONG_TEXT, headline="Schlagzeile"):
    return {"url": url, "headline": headline, "full_text": text, "date": date, "section": "Politik"}

#------------------------- DATABASE -------------------------#

def test_create_tables_keeps_existing_articles(tmp_path):
    db_path = tmp_path / "old.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE news_articles(id INTEGER PRIMARY KEY AUTOINCREMENT, medium TEXT NOT NULL, date TEXT, "
                "headline TEXT, full_text TEXT NOT NULL, url TEXT UNIQUE NOT NULL, section TEXT)")
    con.execute("INSERT INTO news_articles (medium, full_text, url) VALUES ('ORF', 'Text', 'https://orf.at/stories/1/')")
    con.commit()
    con.close()

    with patch("setup_db.DB_PATH", db_path):
        setup_db.main()

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0] == 1
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'sitemap_articles', 'sitemap_urls', 'sitemap_files'} <= tables
    con.close()

#------------------------- CONFIGURATION -------------------------#

def test_outlets_have_unique_keys():
    keys = [cfg['key'] for cfg in OUTLETS.values()]
    assert len(keys) == len(set(keys))

def test_outlets_match_google_news_scraper():
    # The medium names have to be the same in both tables
    assert list(OUTLETS) == list(news_scraper.OUTLETS)

def test_select_outlets():
    assert list(select_outlets(['krone', 'orf'])) == ['ORF', 'Kronen Zeitung']
    assert select_outlets(None) == OUTLETS
    with pytest.raises(ValueError):
        select_outlets(['bild'])

def test_get_terms_by_day():
    df_min = pd.DataFrame({'name': ['Minister A', 'Minister B'],
                           'start': pd.to_datetime(['2020-01-01', '2020-01-16']), 'end': pd.to_datetime(['2020-01-15', '2020-12-31'])})
    df_syn = pd.DataFrame({'synonym': ['BMI'], 'start': pd.to_datetime(['2020-01-01']), 'end': pd.to_datetime(['2020-12-31'])})

    terms = get_terms_by_day(df_min, df_syn, datetime.date(2020, 1, 14), datetime.date(2020, 1, 17))

    assert list(terms) == [datetime.date(2020, 1, d) for d in range(14, 18)]
    assert terms[datetime.date(2020, 1, 15)] == ['BMI', 'Minister A']
    assert terms[datetime.date(2020, 1, 16)] == ['BMI', 'Minister B']

#------------------------- LISTING -------------------------#

def test_parse_sitemap_with_namespace():
    xml = make_sitemap(["https://a.at/1", "https://a.at/2"])
    assert parse_sitemap(xml) == [("https://a.at/1", "2020-01-10T08:00:00Z"), ("https://a.at/2", "2020-01-10T08:00:00Z")]

def test_parse_sitemap_index():
    xml = make_sitemap(["https://a.at/sitemap-2020-01.xml"], tag='sitemap').replace(b'urlset', b'sitemapindex')
    assert parse_sitemap(xml)[0][0] == "https://a.at/sitemap-2020-01.xml"

def test_select_sitemaps_monthly():
    pattern = OUTLETS['Kronen Zeitung']['sitemap_date']
    locs = [f"https://www.krone.at/sitemap-articles-{m}.xml" for m in ['2019-12', '2020-01', '2025-12', '2026-01']]
    selected = select_sitemaps(locs, pattern, datetime.date(2020, 1, 1), datetime.date(2025, 12, 31))
    assert selected == locs[1:3]

def test_select_sitemaps_weekly_includes_week_overlapping_start():
    pattern = OUTLETS['Kurier']['sitemap_date']
    locs = [f"https://kurier.at/sitemaps_article_{d}.xml" for d in ['2019-12-23', '2019-12-30', '2025-12-29', '2026-01-05']]
    selected = select_sitemaps(locs, pattern, datetime.date(2020, 1, 1), datetime.date(2025, 12, 31))
    assert selected == locs[1:3]

def test_select_sitemaps_without_dates_keeps_all():
    locs = ["https://www.heute.at/storage/sitemaps/sitemap_0.xml", "https://www.heute.at/storage/sitemaps/sitemap_1.xml"]
    assert select_sitemaps(locs, None, datetime.date(2020, 1, 1), datetime.date(2025, 12, 31)) == locs

def test_id_chunks_cover_range_once():
    chunks = get_id_chunks("https://orf.at/stories/{id}/", 100, 25150)
    cfg = {'id_sequence': ("https://orf.at/stories/{id}/", 100, 25150)}
    urls = [url for chunk in chunks for url, _ in get_sitemap_entries(cfg, chunk)]
    assert len(chunks) == 3
    assert len(urls) == len(set(urls)) == 25050
    assert urls[0] == "https://orf.at/stories/100/"
    assert urls[-1] == "https://orf.at/stories/25149/"

def test_filter_article_urls_politics_section():
    entries = [("https://kurier.at/politik/inland/a/1", None), ("https://kurier.at/sport/b/2", None)]
    assert [u for u, _ in filter_article_urls(OUTLETS['Kurier'], entries)] == ["https://kurier.at/politik/inland/a/1"]

@pytest.mark.parametrize("url, kept", [
    ("https://www.heute.at/s/neue-regierung-100161895", True),
    ("https://www.heute.at/s/alter-artikel-55245671", False),
    ("https://www.heute.at/s/artikel-aus-2026-120160000", False),
    ("https://www.heute.at/g/bildergalerie-100161895", False),
])
def test_filter_article_urls_id_range(url, kept):
    assert bool(filter_article_urls(OUTLETS['Heute'], [(url, None)])) == kept

def test_filter_article_urls_removes_duplicates():
    entries = [("https://www.krone.at/1", "2020-01-01"), ("https://www.krone.at/1", "2020-01-02")]
    assert filter_article_urls(OUTLETS['Kronen Zeitung'], entries) == [("https://www.krone.at/1", "2020-01-01")]

def test_sort_key_is_stable_and_spreads_urls():
    assert get_sort_key("https://orf.at/stories/1/") == get_sort_key("https://orf.at/stories/1/")
    keys = [get_sort_key(f"https://orf.at/stories/{i}/") for i in range(100)]
    assert keys != sorted(keys)
    assert all(0 <= k < 2 ** 63 for k in keys)

def test_list_sitemaps_rotates_outlets_and_resumes(conn, no_wait):
    outlets = {'A': {'index': 'a'}, 'B': {'index': 'b'}}
    files = {'A': ['a1', 'a2'], 'B': ['b1']}
    order = []

    def entries(cfg, file_url):
        order.append(file_url)
        return [(f"https://{file_url}.at/x", None)]

    with patch.object(sitemap_scraper, 'get_sitemap_files', side_effect=lambda cfg, s, e: files[cfg['index'].upper()]), \
         patch.object(sitemap_scraper, 'get_sitemap_entries', side_effect=entries):
        list_sitemaps(conn, outlets, no_wait)
        assert order == ['a1', 'b1', 'a2']

        # Sitemaps that were already read are skipped in the next session
        files['A'].append('a3')
        list_sitemaps(conn, outlets, no_wait)
        assert order == ['a1', 'b1', 'a2', 'a3']

    assert conn.execute("SELECT COUNT(*) FROM sitemap_urls").fetchone()[0] == 4

def test_list_sitemaps_retries_failed_sitemap_next_session(conn, no_wait):
    outlets = {'A': {'index': 'a'}}
    with patch.object(sitemap_scraper, 'get_sitemap_files', return_value=['a1']), \
         patch.object(sitemap_scraper, 'get_sitemap_entries', side_effect=OSError("timeout")):
        list_sitemaps(conn, outlets, no_wait)
    assert conn.execute("SELECT COUNT(*) FROM sitemap_files").fetchone()[0] == 0

#------------------------- CHECKING ARTICLES -------------------------#

def run_check(conn, url, content=None, error=None, lastmod=None, medium='ORF'):
    """Runs check_article with a mocked download that returns the given article or raises the given error."""
    def fake_fetch(u, timeout=30):
        if error:
            raise error
        return b"<html></html>", content['url']
    with patch.object(sitemap_scraper, 'fetch', side_effect=fake_fetch), \
         patch.object(sitemap_scraper, 'parse_article', return_value=content):
        return check_article(conn, medium, OUTLETS[medium], url, lastmod, TERMS_BY_DAY, browser=None)

def test_check_article_saves_matching_article(conn):
    url = "https://orf.at/stories/1/"
    assert run_check(conn, url, article(url)) == SAVED
    row = conn.execute("SELECT medium, url, terms, section FROM sitemap_articles").fetchone()
    assert row == ('ORF', url, 'Innenminister', 'Politik')

def test_check_article_saves_duplicate_only_once(conn):
    url = "https://orf.at/stories/1/"
    run_check(conn, url, article(url))
    assert run_check(conn, url, article(url)) == DUPLICATE

def test_check_article_rejects_short_text(conn):
    url = "https://orf.at/stories/1/"
    assert run_check(conn, url, article(url, text="Der Innenminister.")) == INVALID

def test_check_article_no_match(conn):
    url = "https://orf.at/stories/1/"
    assert run_check(conn, url, article(url, text="Das Wetter wird morgen schön. " * 20)) == NO_MATCH

def test_check_article_only_uses_terms_of_the_day(conn):
    url = "https://orf.at/stories/1/"
    text = "Minister B hat heute etwas angekündigt. " * 10
    assert run_check(conn, url, article(url, text=text)) == NO_MATCH

def test_check_article_outside_period(conn):
    url = "https://orf.at/stories/1/"
    assert run_check(conn, url, article(url, date="2019-12-31 23:00:00")) == OUTSIDE_PERIOD

def test_check_article_uses_lastmod_without_page_date(conn):
    url = "https://orf.at/stories/1/"
    assert run_check(conn, url, article(url, date=""), lastmod="2020-01-10T08:00:00Z") == SAVED
    assert conn.execute("SELECT date FROM sitemap_articles").fetchone()[0] == "2020-01-10 08:00:00"

def test_check_article_without_any_date(conn):
    url = "https://orf.at/stories/1/"
    assert run_check(conn, url, article(url, date="")) == NO_DATE

def test_check_article_redirect_to_other_domain(conn):
    assert run_check(conn, "https://orf.at/stories/1/", article("https://wien.orf.at/stories/1/")) == OTHER_DOMAIN

def test_check_article_excluded_url(conn):
    url = "https://www.heute.at/s/x-100161895"
    assert run_check(conn, url, article("https://www.heute.at/a/x-100161895"), medium='Heute') == EXCLUDED

def test_check_article_not_found(conn):
    error = urllib.error.HTTPError("https://orf.at/stories/1/", 404, "Not Found", {}, None)
    assert run_check(conn, "https://orf.at/stories/1/", error=error) == NOT_FOUND

@pytest.mark.parametrize("error", [
    urllib.error.HTTPError("https://orf.at/stories/1/", 503, "Service Unavailable", {}, None),
    TimeoutError("timed out"),
])
def test_check_article_failed(conn, error):
    assert run_check(conn, "https://orf.at/stories/1/", error=error) == FAILED

@pytest.mark.parametrize("lastmod, expected", [
    ("2020-04-01T06:10:08.000Z", "2020-04-01 06:10:08"),
    ("2021-09-13T15:11:48+02:00", "2021-09-13 15:11:48"),
    ("2020-04-01", "2020-04-01 00:00:00"),
    ("kein Datum", None),
    (None, None),
])
def test_normalize_lastmod(lastmod, expected):
    assert normalize_lastmod(lastmod) == expected

def test_check_articles_rotates_outlets(conn, no_wait):
    add_urls(conn, [("a1", "A"), ("a2", "A"), ("a3", "A"), ("b1", "B"), ("c1", "C"), ("c2", "C")])
    order = []

    def fake_check(conn, medium, cfg, url, *args):
        order.append(url)
        return NO_MATCH

    with patch.object(sitemap_scraper, 'check_article', side_effect=fake_check):
        check_articles(conn, {'A': {}, 'B': {}, 'C': {}}, TERMS_BY_DAY, no_wait, browser=None)

    assert order == ['a1', 'b1', 'c1', 'a2', 'c2', 'a3']
    assert conn.execute("SELECT COUNT(*) FROM sitemap_urls WHERE status = ?", (NO_MATCH,)).fetchone()[0] == 6

def test_check_articles_respects_max_articles(conn, no_wait):
    add_urls(conn, [("a1", "A"), ("a2", "A"), ("b1", "B")])
    with patch.object(sitemap_scraper, 'check_article', return_value=NO_MATCH):
        check_articles(conn, {'A': {}, 'B': {}}, TERMS_BY_DAY, no_wait, browser=None, max_articles=2)
    assert get_next_url(conn, 'A') == ("a2", None)

def test_check_articles_stops_on_pause(conn, no_wait):
    add_urls(conn, [("a1", "A"), ("a2", "A")])

    def pause_after_check(*args):
        news_scraper.pause_requested = True
        return NO_MATCH

    with patch.object(sitemap_scraper, 'check_article', side_effect=pause_after_check):
        check_articles(conn, {'A': {}}, TERMS_BY_DAY, no_wait, browser=None)
    assert get_next_url(conn, 'A') == ("a2", None)

def test_check_articles_skips_failing_outlet_and_resets_its_failures(conn, no_wait):
    add_urls(conn, [(f"a{i}", "A") for i in range(10)] + [(f"b{i}", "B") for i in range(3)])

    def fake_check(conn, medium, cfg, url, *args):
        return FAILED if medium == 'A' else NO_MATCH

    with patch.object(sitemap_scraper, 'check_article', side_effect=fake_check):
        check_articles(conn, {'A': {}, 'B': {}}, TERMS_BY_DAY, no_wait, browser=None)

    # The failures of the blocked outlet are retried next session, the other outlet is finished
    assert conn.execute("SELECT COUNT(*) FROM sitemap_urls WHERE medium = 'A' AND status IS NULL").fetchone()[0] == 10
    assert get_next_url(conn, 'B') is None

def test_check_articles_keeps_isolated_failures(conn, no_wait):
    add_urls(conn, [("a1", "A"), ("a2", "A")])
    with patch.object(sitemap_scraper, 'check_article', side_effect=[FAILED, NO_MATCH]):
        check_articles(conn, {'A': {}}, TERMS_BY_DAY, no_wait, browser=None)
    assert conn.execute("SELECT status FROM sitemap_urls WHERE url = 'a1'").fetchone()[0] == FAILED

def test_throttle_waits_between_requests_to_same_outlet():
    news_scraper.pause_requested = False
    throttle = Throttle(interval=10)
    with patch.object(sitemap_scraper, 'wait', return_value=True) as mock_wait:
        throttle.wait_for_turn('A')
        throttle.wait_for_turn('B')
        mock_wait.assert_not_called()
        throttle.wait_for_turn('A')
        mock_wait.assert_called_once()
        assert 9 < mock_wait.call_args[0][0] <= 11
