import sys
from pathlib import Path

# Add the scripts directory to the python path
scripts_path = Path(__file__).parent.parent / 'scripts'
sys.path.append(str(scripts_path))

import datetime
import sqlite3
import pandas as pd
import pandera.errors
import pytest
from pydantic import ValidationError
from unittest.mock import patch, MagicMock

import setup_db
import news_scraper
from news_scraper import (
    is_valid_context,
    contains_term,
    get_queries,
    get_query_key,
    load_progress,
    save_progress,
    request_pause,
    fetch_google_news_rss,
    load_and_prepare_data,
    save_article_to_db,
    build_query,
    batch_terms,
    is_in_date_range,
    MAX_QUERY_LENGTH,
    OUTLETS,
    SAVED,
    DUPLICATE,
    INVALID
)
from schemas import MinisterSchema, SynonymSchema, NewsArticle

LONG_TEXT = "Der Innenminister Karner hat heute die Polizei besucht. " * 10

#------------------------- FIXTURES -------------------------#

@pytest.fixture
def ministers_df():
    df = pd.DataFrame({
        'name': ['Minister A', 'Minister B'],
        'party': ['ÖVP', 'SPÖ'],
        'start_of_period': ['1/1/20', '1/16/20'],
        'end_of_period': ['1/15/20', '12/31/20'],
        'ministry': ['Ministry X', 'Ministry X'],
        'bucket': ['Interior', 'Interior']
    })
    df['start'] = pd.to_datetime(df['start_of_period'], format='%m/%d/%y')
    df['end'] = pd.to_datetime(df['end_of_period'], format='%m/%d/%y')
    return df

@pytest.fixture
def synonyms_df():
    df = pd.DataFrame({
        'bucket': ['Interior'] * 6,
        'synonym': ['BMI', 'Innenministerium', 'Innenminister', 'Innenressort', 'Inneres', 'Feb Only'],
        'start_of_period': ['1/1/20'] * 5 + ['2/1/20'],
        'end_of_period': ['12/31/20'] * 5 + ['2/29/20']
    })
    df['start'] = pd.to_datetime(df['start_of_period'], format='%m/%d/%y')
    df['end'] = pd.to_datetime(df['end_of_period'], format='%m/%d/%y')
    return df

@pytest.fixture
def temp_db(tmp_path):
    """Creates an empty database with all tables and points the scraper to it."""
    db_path = tmp_path / "test_db.db"
    with patch("setup_db.DB_PATH", db_path):
        setup_db.main()
    with patch("news_scraper.DB_PATH", db_path):
        yield db_path

def make_rss(items):
    """Builds a Google News RSS response from (title, source, pubDate, link) tuples."""
    xml_items = "".join(
        f"<item><title>{t}</title><link>{l}</link><pubDate>{d}</pubDate><source url='https://x.at'>{s}</source></item>"
        for t, s, d, l in items
    )
    response = MagicMock()
    response.read.return_value = f"<?xml version='1.0'?><rss><channel>{xml_items}</channel></rss>".encode()
    response.__enter__.return_value = response
    return response

#------------------------- CONTEXT FILTER -------------------------#

def test_is_valid_context_bmi_valid():
    text = "Das BMI hat heute bestätigt, dass Innenminister Karner die Polizei besucht."
    assert is_valid_context(text, "BMI") == True
    
def test_is_valid_context_bmi_invalid_health():
    text = "Ein hoher Body Mass Index (BMI) und Übergewicht sind schlecht für die Gesundheit."
    assert is_valid_context(text, "BMI") == False

def test_is_valid_context_bmi_invalid_generic():
    text = "Laut BMI ist das ein komplett irrelevanter Text ohne Kontextwörter."
    assert is_valid_context(text, "BMI") == False
    
def test_is_valid_context_bka_valid():
    text = "Das BKA teilte mit, dass der Bundeskanzler heute eine Pressekonferenz gibt."
    assert is_valid_context(text, "BKA") == True
    
def test_is_valid_context_bka_invalid():
    text = "Das BKA (Bundeskriminalamt in Deutschland) ermittelt."
    # Since none of the context words (bundeskanzler, regierung, kanzler, minister) are there, should be false
    assert is_valid_context(text, "BKA") == False
    
def test_is_valid_context_generic_term():
    # Any text that contains the term and doesn't trigger the explicit BMI/BKA exclusions should be valid
    text = "Der Arbeitsminister plant neue Reformen."
    assert is_valid_context(text, "Arbeitsminister") == True

def test_is_valid_context_empty_text():
    assert is_valid_context("", "Arbeitsminister") == False

def test_is_valid_context_term_missing():
    # Articles which do not mention the term at all are rejected, even if they would pass the context check
    text = "Der Bundeskanzler gab heute eine Pressekonferenz."
    assert is_valid_context(text, "Arbeitsminister") == False
    assert is_valid_context(text, "BKA") == False

def test_contains_term_case_insensitive_for_words():
    assert contains_term("Das INNENMINISTERIUM teilte mit", "Innenministerium") == True
    # Also matches inflected forms, which contain the term as part of a word
    assert contains_term("Ein Sprecher des Innenministeriums", "Innenministerium") == True
    assert contains_term("Klaudia Tanner besuchte die Truppe", "Klaudia Tanner") == True

def test_contains_term_whole_word_for_abbreviations():
    assert contains_term("Laut BMI gibt es neue Zahlen", "BMI") == True
    assert contains_term("Ein BMF-Sprecher bestätigte", "BMF") == True
    # Abbreviations must not match parts of other words or differently capitalized text
    assert contains_term("Die BMIX-Gruppe", "BMI") == False
    assert contains_term("Der bmi-Rechner zeigt das Gewicht", "BMI") == False
    assert contains_term("Das BMLVS ist nicht gemeint", "BMLV") == False

#------------------------- QUERY GENERATION -------------------------#

def test_get_queries_monthly_windows(ministers_df, synonyms_df):
    queries = get_queries(ministers_df, synonyms_df, datetime.datetime(2020, 1, 1), datetime.datetime(2020, 3, 31))
    windows = sorted({(q['start_str'], q['end_str']) for q in queries})
    assert windows == [('2020-01-01', '2020-01-31'), ('2020-02-01', '2020-02-29'), ('2020-03-01', '2020-03-31')]

def test_get_queries_clips_end_date(ministers_df, synonyms_df):
    queries = get_queries(ministers_df, synonyms_df, datetime.datetime(2020, 1, 1), datetime.datetime(2020, 1, 20))
    assert {q['end_str'] for q in queries} == {'2020-01-20'}

def test_get_queries_december_rollover(ministers_df, synonyms_df):
    synonyms_df['end'] = pd.Timestamp(2021, 12, 31)
    queries = get_queries(ministers_df, synonyms_df, datetime.datetime(2020, 12, 1), datetime.datetime(2021, 1, 31))
    assert {(q['start_str'], q['end_str']) for q in queries} == {('2020-12-01', '2020-12-31'), ('2021-01-01', '2021-01-31')}

def test_get_queries_only_active_terms(ministers_df, synonyms_df):
    queries = get_queries(ministers_df, synonyms_df, datetime.datetime(2020, 1, 1), datetime.datetime(2020, 3, 31))
    terms_by_month = {}
    for q in queries:
        terms_by_month.setdefault(q['start_str'], set()).update(q['terms'])

    # Both ministers were in office in January, only Minister B afterwards
    assert {'Minister A', 'Minister B'} <= terms_by_month['2020-01-01']
    assert 'Minister A' not in terms_by_month['2020-02-01']
    # "Feb Only" is only valid in February
    assert 'Feb Only' in terms_by_month['2020-02-01']
    assert 'Feb Only' not in terms_by_month['2020-01-01'] | terms_by_month['2020-03-01']

def test_get_queries_batches_are_sorted_and_limited(ministers_df, synonyms_df):
    queries = get_queries(ministers_df, synonyms_df, datetime.datetime(2020, 1, 1), datetime.datetime(2020, 1, 31))
    all_terms = [t for q in queries for t in q['terms']]
    for site in OUTLETS.values():
        assert all(len(build_query(site, q['terms'], q['start_str'], q['end_str'])) <= MAX_QUERY_LENGTH for q in queries)
    assert all_terms == sorted(all_terms)
    assert len(all_terms) == len(set(all_terms))

def test_build_query():
    query = build_query("orf.at", ["Karner", "BMI"], "2024-03-01", "2024-03-31")
    # before: is exclusive, so the day after the end of the period is used
    assert query == 'site:orf.at ("Karner" OR "BMI") after:2024-03-01 before:2024-04-01'

def test_batch_terms_respects_max_length():
    terms = [f"Bundesministerium für Testangelegenheiten Nummer {i}" for i in range(10)]
    batches = batch_terms(terms, "2020-01-01", "2020-01-31")
    longest_site = max(OUTLETS.values(), key=len)

    assert len(batches) > 1
    assert [t for b in batches for t in b] == terms
    assert all(len(build_query(longest_site, b, "2020-01-01", "2020-01-31")) <= MAX_QUERY_LENGTH for b in batches)

def test_batch_terms_combines_short_terms():
    assert batch_terms(["BMI", "BMJ", "BMF"], "2020-01-01", "2020-01-31") == [["BMI", "BMJ", "BMF"]]

def test_batch_terms_keeps_overlong_term_alone():
    long_term = "x" * MAX_QUERY_LENGTH
    assert batch_terms(["BMI", long_term, "BMJ"], "2020-01-01", "2020-01-31") == [["BMI"], [long_term], ["BMJ"]]

@pytest.mark.parametrize("date, expected", [
    ("2020-04-15 12:00:00", True),
    ("2020-04-01 00:00:00", True),
    ("2020-04-30 23:59:00", True),
    # One day of tolerance on both sides, since RSS dates are in GMT
    ("2020-03-31 23:30:00", True),
    ("2020-05-01 00:30:00", True),
    ("2020-03-30 12:00:00", False),
    ("2023-06-15 12:00:00", False),
    ("", True),
])
def test_is_in_date_range(date, expected):
    assert is_in_date_range(date, "2020-04-01", "2020-04-30") == expected

def test_get_queries_deterministic(ministers_df, synonyms_df):
    # Resuming relies on the queries being identical across runs, even if the input order changes
    args = (datetime.datetime(2020, 1, 1), datetime.datetime(2020, 6, 30))
    shuffled_min = ministers_df.sample(frac=1, random_state=1)
    shuffled_syn = synonyms_df.sample(frac=1, random_state=1)
    assert get_queries(ministers_df, synonyms_df, *args) == get_queries(shuffled_min, shuffled_syn, *args)

def test_get_query_key_ignores_term_order():
    q1 = {'start_str': '2020-01-01', 'end_str': '2020-01-31', 'terms': ['A', 'B']}
    q2 = {'start_str': '2020-01-01', 'end_str': '2020-01-31', 'terms': ['B', 'A']}
    assert get_query_key('orf.at', q1) == get_query_key('orf.at', q2)
    assert get_query_key('orf.at', q1) != get_query_key('krone.at', q1)

#------------------------- PAUSING AND RESUMING -------------------------#

def test_progress_roundtrip(tmp_path):
    with patch("news_scraper.PROGRESS_FILE", tmp_path / "progress.json"):
        assert load_progress() == set()
        save_progress({'a', 'b'})
        assert load_progress() == {'a', 'b'}

def test_load_progress_corrupted_file(tmp_path):
    progress_file = tmp_path / "progress.json"
    progress_file.write_text("{not valid json")
    with patch("news_scraper.PROGRESS_FILE", progress_file):
        assert load_progress() == set()

def test_request_pause():
    news_scraper.pause_requested = False
    request_pause(None, None)
    assert news_scraper.pause_requested == True
    # A second Ctrl+C aborts immediately
    with pytest.raises(KeyboardInterrupt):
        request_pause(None, None)
    news_scraper.pause_requested = False

#------------------------- GOOGLE NEWS RSS -------------------------#

@patch("urllib.request.urlopen")
def test_fetch_google_news_rss_parses_items(mock_urlopen):
    mock_urlopen.return_value = make_rss([
        ("Karner besucht Favoriten - Der Standard", "Der Standard", "Tue, 19 Mar 2024 07:00:00 GMT", "https://news.google.com/rss/articles/1"),
        ("Ohne Suffix", "Der Standard", "", "https://news.google.com/rss/articles/2"),
    ])
    results = fetch_google_news_rss("derstandard.at", ["Karner"], "2024-03-01", "2024-03-31")

    assert results == [
        ("https://news.google.com/rss/articles/1", "Karner besucht Favoriten", "2024-03-19 07:00:00"),
        ("https://news.google.com/rss/articles/2", "Ohne Suffix", ""),
    ]

@patch("urllib.request.urlopen")
def test_fetch_google_news_rss_includes_last_day(mock_urlopen):
    mock_urlopen.return_value = make_rss([])
    fetch_google_news_rss("orf.at", ["Karner", "BMI"], "2024-03-01", "2024-03-31")

    requested_url = mock_urlopen.call_args[0][0].full_url
    assert "before%3A2024-04-01" in requested_url
    assert "after%3A2024-03-01" in requested_url
    assert "site%3Aorf.at" in requested_url

@patch("urllib.request.urlopen", side_effect=OSError("network down"))
def test_fetch_google_news_rss_network_error(mock_urlopen):
    assert fetch_google_news_rss("orf.at", ["Karner"], "2024-03-01", "2024-03-31") == []

#------------------------- DATA VALIDATION -------------------------#

def test_load_and_prepare_data_real_csvs():
    df_min, df_syn = load_and_prepare_data()
    assert not df_min.empty and not df_syn.empty
    assert (df_min['start'] <= df_min['end']).all()

def test_load_and_prepare_data_bucket_mismatch(ministers_df, synonyms_df):
    ministers_raw = ministers_df.drop(columns=['start', 'end']).assign(bucket='Interiro')
    synonyms_raw = synonyms_df.drop(columns=['start', 'end'])
    with patch("pandas.read_csv", side_effect=[ministers_raw, synonyms_raw]):
        with pytest.raises(ValueError, match="Interiro"):
            load_and_prepare_data()

def test_minister_schema_rejects_bad_date_format(ministers_df):
    df = ministers_df.drop(columns=['start', 'end'])
    df.loc[0, 'start_of_period'] = '2020-01-01'
    with pytest.raises(pandera.errors.SchemaError):
        MinisterSchema.validate(df)

def test_synonym_schema_rejects_reversed_period(synonyms_df):
    df = synonyms_df.drop(columns=['start', 'end'])
    df.loc[0, ['start_of_period', 'end_of_period']] = ['12/31/20', '1/1/20']
    with pytest.raises(pandera.errors.SchemaError):
        SynonymSchema.validate(df)

def test_news_article_valid():
    article = NewsArticle(medium="ORF", url="https://orf.at/stories/1", date="2024-03-19 07:00:00", headline="  Titel  ", full_text=LONG_TEXT)
    assert article.headline == "Titel"

def test_news_article_empty_date_and_headline_become_none():
    article = NewsArticle(medium="ORF", url="https://orf.at/stories/1", date="", headline="", full_text=LONG_TEXT)
    assert article.date is None
    assert article.headline is None

@pytest.mark.parametrize("field, value", [
    ("url", "not a url"),
    ("date", "19.03.2024"),
    ("full_text", "Bitte akzeptieren Sie Cookies."),
    ("medium", ""),
])
def test_news_article_invalid(field, value):
    data = dict(medium="ORF", url="https://orf.at/stories/1", date="2024-03-19 07:00:00", headline="Titel", full_text=LONG_TEXT)
    data[field] = value
    with pytest.raises(ValidationError):
        NewsArticle(**data)

#------------------------- DATABASE -------------------------#

def test_save_article_to_db(temp_db):
    article = {'headline': 'Titel', 'full_text': LONG_TEXT, 'date': '2024-03-19 07:00:00'}
    assert save_article_to_db("ORF", "https://orf.at/stories/1", article) == SAVED

    con = sqlite3.connect(temp_db)
    rows = con.execute("SELECT medium, date, headline, full_text, url FROM news_articles").fetchall()
    con.close()
    assert rows == [("ORF", "2024-03-19 07:00:00", "Titel", LONG_TEXT.strip(), "https://orf.at/stories/1")]

def test_save_article_to_db_ignores_duplicates(temp_db):
    article = {'headline': 'Titel', 'full_text': LONG_TEXT, 'date': ''}
    assert save_article_to_db("ORF", "https://orf.at/stories/1", article) == SAVED
    assert save_article_to_db("ORF", "https://orf.at/stories/1", article) == DUPLICATE

    con = sqlite3.connect(temp_db)
    assert con.execute("SELECT COUNT(*), MAX(date) FROM news_articles").fetchone() == (1, None)
    con.close()

def test_save_article_to_db_rejects_invalid_article(temp_db):
    article = {'headline': 'Cookies', 'full_text': 'Bitte akzeptieren Sie Cookies.', 'date': ''}
    assert save_article_to_db("ORF", "https://orf.at/stories/1", article) == INVALID

    con = sqlite3.connect(temp_db)
    assert con.execute("SELECT COUNT(*) FROM news_articles").fetchone() == (0,)
    con.close()
