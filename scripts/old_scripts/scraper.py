import logging
import sqlite3
import datetime
import time
import random
import urllib.parse
from pathlib import Path
from typing import List, Dict, Set, Tuple

import pandas as pd
import pandera.pandas as pa
import trafilatura
import dateparser
from playwright.sync_api import sync_playwright

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DRY_RUN = True
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Student Research Project - Sentiment Analysis of Austrian Media"

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "rtr_db" / "austrian_media_data.db"
MINISTERS_CSV = BASE_DIR / "data" / "synonym_lists" / "ministers.csv"
SYNONYMS_CSV = BASE_DIR / "data" / "synonym_lists" / "ministry_synonyms.csv"

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

minister_schema = pa.DataFrameSchema({
    "Name": pa.Column(str),
    "Party": pa.Column(str, required=False),
    "Start of Period": pa.Column(str),
    "End of Period": pa.Column(str),
    "Ministry": pa.Column(str, required=False),
    "Bucket": pa.Column(str),
})

synonym_schema = pa.DataFrameSchema({
    "Bucket": pa.Column(str),
    "Synonym": pa.Column(str),
    "Start of Period": pa.Column(str),
    "End of Period": pa.Column(str),
})

def load_and_prepare_data() -> Tuple[pd.DataFrame, pd.DataFrame]:
    df_min = pd.read_csv(MINISTERS_CSV)
    df_syn = pd.read_csv(SYNONYMS_CSV)
    
    minister_schema.validate(df_min)
    synonym_schema.validate(df_syn)
    
    df_min['start'] = pd.to_datetime(df_min['Start of Period'], format='%m/%d/%y')
    df_min['end'] = pd.to_datetime(df_min['End of Period'], format='%m/%d/%y')
    
    df_syn['start'] = pd.to_datetime(df_syn['Start of Period'], format='%m/%d/%y')
    df_syn['end'] = pd.to_datetime(df_syn['End of Period'], format='%m/%d/%y')
    
    return df_min, df_syn

def get_queries(df_min: pd.DataFrame, df_syn: pd.DataFrame, start_date: datetime.datetime, end_date: datetime.datetime) -> List[Dict]:
    queries = []
    current = start_date
    while current < end_date:
        year = current.year + (current.month // 12)
        month = (current.month % 12) + 1
        next_month_start = datetime.datetime(year, month, 1)
        next_month = next_month_start - datetime.timedelta(days=1)
        if next_month > end_date:
            next_month = end_date
            
        buckets = set(df_min['Bucket'].unique()).union(set(df_syn['Bucket'].unique()))
        
        for bucket in buckets:
            active_min = df_min[(df_min['Bucket'] == bucket) & (df_min['start'] <= next_month) & (df_min['end'] >= current)]
            active_syn = df_syn[(df_syn['Bucket'] == bucket) & (df_syn['start'] <= next_month) & (df_syn['end'] >= current)]
            
            terms = set(active_min['Name'].unique()).union(set(active_syn['Synonym'].unique()))
            terms = list(terms)
            
            # Batch terms to avoid Google search limits
            batch_size = 5
            for i in range(0, len(terms), batch_size):
                batch = terms[i:i+batch_size]
                if batch:
                    queries.append({
                        'bucket': bucket,
                        'start_str': current.strftime("%Y-%m-%d"),
                        'end_str': next_month.strftime("%Y-%m-%d"),
                        'terms': batch
                    })
                    
        current = next_month + datetime.timedelta(days=1)
        
    return queries

def search_google_playwright(site: str, terms: List[str], after: str, before: str, page, max_results: int = 10) -> Set[str]:
    links = set()
    terms_query = " OR ".join([f'"{t}"' for t in terms])
    query = f'site:{site} ({terms_query}) after:{after} before:{before}'
    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://www.google.com/search?q={encoded_query}&hl=de"
    
    try:
        page.goto(url, wait_until='domcontentloaded')
        
        if "consent.google.com" in page.url:
            try:
                import re
                btn = page.locator('button, [role="button"]').filter(has_text=re.compile(r"(Accept all|Alle akzeptieren|I agree|Zustimmen)", re.IGNORECASE)).first
                if btn.count() > 0:
                    btn.click()
                    page.wait_for_load_state('domcontentloaded', timeout=5000)
            except Exception as e:
                logger.debug(f"Could not bypass consent: {e}")
                
        # Handle simple captcha / robot check warning
        if "sorry/index" in page.url:
            logger.warning("Google threw a CAPTCHA. Pausing...")
            time.sleep(10)
            return links

        a_tags = page.locator('#search a').all()
        for a in a_tags:
            href = a.get_attribute('href')
            if href and site in href and "google.com" not in href:
                links.add(href)
                if len(links) >= max_results:
                    break
    except Exception as e:
        logger.error(f"Error searching Google for {site}: {e}")
        
    return links

def is_valid_context(text: str, keyword: str) -> bool:
    if not text:
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
            
    return True

def extract_article(url: str) -> Dict[str, str]:
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return {}
        
    result = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=False,
        output_format='json'
    )
    
    if result:
        import json
        try:
            data = json.loads(result)
            dt = data.get('date')
            if dt:
                parsed = dateparser.parse(dt)
                if parsed:
                    data['date'] = parsed.strftime("%Y-%m-%d %H:%M:%S")
            return {
                "headline": data.get('title', ''),
                "full_text": data.get('text', ''),
                "date": data.get('date', '')
            }
        except Exception as e:
            logger.error(f"Failed to parse Trafilatura JSON for {url}: {e}")
            
    return {}

def save_article_to_db(outlet: str, url: str, article_data: Dict[str, str]):
    if not article_data.get('full_text'):
        return
        
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT OR IGNORE INTO news_articles (medium, date, headline, full_text, url)
        VALUES (?, ?, ?, ?, ?)
        ''', (
            outlet,
            article_data.get('date', ''),
            article_data.get('headline', ''),
            article_data.get('full_text', ''),
            url
        ))
        
        if cursor.rowcount > 0:
            logger.info(f"Saved new article to DB: {url}")
        else:
            logger.debug(f"Article already exists in DB: {url}")
            
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save article to DB: {e}")
    finally:
        if conn is not None:
            conn.close()

def main():
    logger.info("Loading data from CSVs...")
    df_min, df_syn = load_and_prepare_data()
    
    start_date = datetime.datetime(2020, 1, 1)
    end_date = datetime.datetime(2025, 12, 31)
    
    logger.info("Generating queries...")
    queries = get_queries(df_min, df_syn, start_date, end_date)
        
    if DRY_RUN:
        logger.info("DRY RUN ENABLED. Limiting queries and not saving to DB.")
        queries = queries[:10]
        
    logger.info(f"Generated {len(queries)} total query batches.")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        
        for outlet_name, site_domain in OUTLETS.items():
            logger.info(f"--- Processing outlet: {site_domain} ---")
            
            for q in queries:
                terms = q['terms']
                logger.info(f"Searching {site_domain} for {terms} between {q['start_str']} and {q['end_str']}")
                
                links = search_google_playwright(
                    site=site_domain,
                    terms=terms,
                    after=q['start_str'],
                    before=q['end_str'],
                    page=page
                )
                
                for link in links:
                    logger.info(f"Extracting: {link}")
                    content = extract_article(link)
                    
                    if content and content.get('full_text'):
                        valid = False
                        for term in terms:
                            if is_valid_context(content['full_text'], term):
                                valid = True
                                break
                                
                        if valid:
                            logger.info(f"Valid article found: {content.get('headline', '')[:50]}")
                            if not DRY_RUN:
                                save_article_to_db(outlet_name, link, content)
                        else:
                            logger.info(f"Article rejected by context filter: {link}")
                            
                time.sleep(random.uniform(2, 5))
                
            if DRY_RUN:
                break
                
        browser.close()

if __name__ == '__main__':
    main()
