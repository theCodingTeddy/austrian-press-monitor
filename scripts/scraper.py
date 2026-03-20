import logging
import sqlite3
import datetime
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import re
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple
from pathlib import Path
from playwright.sync_api import sync_playwright

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Student Research Project - Sentiment Analysis of Austrian Media"

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "media_analysis.db"

def normalize_date(date_str: str) -> str:
    """Parses various date string formats and normalizes them to YYYY-MM-DD HH:MM:SS"""
    if not date_str:
        return ""
    date_str = date_str.strip()
    try:
        from datetime import datetime
        import re
        
        import email.utils
        
        # Handle "Heute" format e.g. "29.10.2025, 17:00"
        if re.match(r'^\d{2}\.\d{2}\.\d{4},\s*\d{2}:\d{2}$', date_str):
            dt = datetime.strptime(date_str, "%d.%m.%Y, %H:%M")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
            
        # Handle RFC-2822 format out of Google News RSS (e.g. Thu, 15 Feb 2024 10:00:00 GMT)
        if "," in date_str and "GMT" in date_str:
            dt = email.utils.parsedate_to_datetime(date_str)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
            
        # Handle strict ISO formats including those missing seconds or with timezones
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        # Fallback to the raw string if parsing completely fails
        return date_str

def save_article_to_db(db_path, outlet, url, article_data):
    """Saves the extracted article to the database."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Insert or ignore since url is UNIQUE
        cursor.execute('''
        INSERT OR IGNORE INTO news_articles (outlet, date, headline, full_text, url)
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
        if 'conn' in locals():
            conn.close()

def fetch_google_news_rss(site_domain: str, keywords: List[str], start_date: str, end_date: str) -> List[Tuple[str, str]]:
    """Uses Google News RSS to find articles matching the keywords on the given domain."""
    links_data = set()
    # Batch keywords by groups of 3 to avoid making 15+ requests per newspaper
    for i in range(0, len(keywords), 3):
        batch = keywords[i:i+3]
        query_parts = " OR ".join([f'"{kw}"' for kw in batch])
        query = f'({query_parts}) site:{site_domain} after:{start_date} before:{end_date}'
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=de&gl=AT&ceid=AT:de"
        
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                for item in root.findall('.//item'):
                    link = item.find('link')
                    pub_date = item.find('pubDate')
                    if link is not None and link.text:
                        pdate = pub_date.text if pub_date is not None else ""
                        links_data.add((link.text, pdate))
        except Exception as e:
            logger.error(f"RSS fetch failed for {batch} on {site_domain}: {e}")
            
        time.sleep(1) # Be nice to Google
        
    return list(links_data)

def generic_playwright_extract(url: str, dummy_name: str, dry_run: bool) -> Dict[str, str]:
    """Generic Playwright extraction that bypasses cookie banners by reading generic meta/title tags."""
    if dry_run: return {"headline": f"{dummy_name} Dummy", "full_text": "Content", "date": "2024-01-01"}
    article_data = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        try:
            # Wait until DOM is loaded, Google News links redirect to actual article
            page.goto(url, wait_until='domcontentloaded', timeout=15000)
            
            # Check if we hit Google's consent wall
            if "consent.google.com" in page.url or "continue" in page.title() or "fortfahren" in page.title():
                # Try to click accept
                try:
                    btn = page.locator('button, [role="button"]').filter(has_text=re.compile(r"(Accept all|Alle akzeptieren|I agree|Zustimmen)", re.IGNORECASE)).first
                    if btn.count() > 0:
                        btn.click()
                        page.wait_for_load_state('domcontentloaded', timeout=10000)
                except Exception as e:
                    logger.debug(f"Google consent click failed: {e}")
            
            # Wait an extra second for redirect chains if any
            page.wait_for_timeout(2000)
            
            # Extract Title via head to avoid cookie banners
            title = page.title()
            article_data['headline'] = title.split(' - ')[0].split(' | ')[0].strip()
            
            # Extract standard date meta tags
            date_str = ""
            # List of tuples: (selector, attribute or None for inner_text)
            date_selectors = [
                ('meta[property="article:published_time"]', 'content'),
                ('meta[name="article:published_time"]', 'content'), # Die Presse
                ('meta[name="date"]', 'content'),
                ('meta[name="pubdate"]', 'content'),
                ('meta[property="og:article:published_time"]', 'content'),
                ('meta[name="publication_date"]', 'content'),
                ('meta[itemprop="datePublished"]', 'content'),
                ('time.article-pubdate', 'datetime'), # Der Standard
                ('.author-time', None), # Heute
            ]
            
            for selector, attr in date_selectors:
                loc = page.locator(selector)
                if loc.count() > 0:
                    val = loc.first.get_attribute(attr) if attr else loc.first.inner_text()
                    if val and val.strip():
                        date_str = normalize_date(val.strip())
                        break
                        
            article_data['date'] = date_str
                
            # Extract paragraphs. Filter short ones to ignore generic cookie banner text
            paras = page.locator('p').all_inner_texts()
            # Also look for article tag if available
            article_paras = page.locator('article p').all_inner_texts()
            if article_paras: paras = article_paras
                
            article_data['full_text'] = "\n".join([p for p in paras if len(p.strip()) > 40])
            
        except Exception as e:
            logger.error(f"{dummy_name} extract error for {url}: {e}")
        finally:
            browser.close()
    return article_data

class BaseScraper(ABC):
    """Abstract base class for news scrapers."""
    
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        logger.info(f"Initialized {self.__class__.__name__}. Dry Run mode enabled: {self.dry_run}")
        
    @abstractmethod
    def get_article_links(self, start_date: str, end_date: str, keywords: List[str]) -> List[Tuple[str, str]]:
        pass
        
    @abstractmethod
    def extract_article_content(self, url: str) -> Dict[str, str]:
        pass
        
class DerStandardScraper(BaseScraper):
    def get_article_links(self, start_date: str, end_date: str, keywords: List[str]) -> List[Tuple[str, str]]:
        if self.dry_run: return [("https://www.derstandard.at/story/dummy-1", "")]
        return fetch_google_news_rss("derstandard.at", keywords, start_date, end_date)
        
    def extract_article_content(self, url: str) -> Dict[str, str]:
        return generic_playwright_extract(url, "Der Standard", self.dry_run)

class KroneScraper(BaseScraper):
    def get_article_links(self, start_date: str, end_date: str, keywords: List[str]) -> List[Tuple[str, str]]:
        if self.dry_run: return [("https://www.krone.at/dummy-krone", "")]
        return fetch_google_news_rss("krone.at", keywords, start_date, end_date)
        
    def extract_article_content(self, url: str) -> Dict[str, str]:
        return generic_playwright_extract(url, "Krone", self.dry_run)

class PresseScraper(BaseScraper):
    def get_article_links(self, start_date: str, end_date: str, keywords: List[str]) -> List[Tuple[str, str]]:
        if self.dry_run: return [("https://www.diepresse.com/dummy-presse", "")]
        return fetch_google_news_rss("diepresse.com", keywords, start_date, end_date)
        
    def extract_article_content(self, url: str) -> Dict[str, str]:
        return generic_playwright_extract(url, "Die Presse", self.dry_run)

class HeuteScraper(BaseScraper):
    def get_article_links(self, start_date: str, end_date: str, keywords: List[str]) -> List[Tuple[str, str]]:
        if self.dry_run: return [("https://www.heute.at/dummy-heute", "")]
        return fetch_google_news_rss("heute.at", keywords, start_date, end_date)
        
    def extract_article_content(self, url: str) -> Dict[str, str]:
        return generic_playwright_extract(url, "Heute", self.dry_run)

class KleineZeitungScraper(BaseScraper):
    def get_article_links(self, start_date: str, end_date: str, keywords: List[str]) -> List[Tuple[str, str]]:
        if self.dry_run: return [("https://www.kleinezeitung.at/dummy-kleine", "")]
        return fetch_google_news_rss("kleinezeitung.at", keywords, start_date, end_date)
        
    def extract_article_content(self, url: str) -> Dict[str, str]:
        return generic_playwright_extract(url, "Kleine Zeitung", self.dry_run)

def main():
    MINISTRIES = {
        "BMI": ["Bundesministerium für Inneres", "Innenministerium", "BMI"],
        "BMEIA": ["BMEIA", "Außenministerium", "Bundesministerium für europäische und internationale Angelegenheiten"],
        "BMLV": ["BMLV", "Verteidigungsministerium", "Bundesministerium für Landesverteidigung"],
        "BKA": ["Bundeskanzleramt", "BKA"],
        "BMLUK": ["BMLUK", "Umweltministerium", "Klimaministerium", "BMK", "Bundesministerium für Klimaschutz"]
    }
    
    START_DATE = "2024-01-01"
    END_DATE = "2026-03-20"
    
    # Flatten the keywords for search
    all_keywords = []
    for keywords in MINISTRIES.values():
        all_keywords.extend(keywords)
        
    DRY_RUN = False
    
    scrapers = {
        "Der Standard": DerStandardScraper(dry_run=DRY_RUN),
        "Krone": KroneScraper(dry_run=DRY_RUN),
        "Die Presse": PresseScraper(dry_run=DRY_RUN),
        "Heute": HeuteScraper(dry_run=DRY_RUN),
        "Kleine Zeitung": KleineZeitungScraper(dry_run=DRY_RUN)
    }
    
    logger.info(f"Starting Scraping Run from {START_DATE} to {END_DATE}")
    
    for outlet_name, scraper in scrapers.items():
        logger.info(f"--- Processing outlet: {outlet_name} ---")
        links = scraper.get_article_links(START_DATE, END_DATE, all_keywords)
        logger.info(f"Discovered {len(links)} links for {outlet_name}")
        
        # Iterate through all discovered links
        for url, rss_date in links:
            content = scraper.extract_article_content(url)
            if content and content.get("full_text"):
                logger.info(f"Successfully scraped: {content.get('headline')[:50]}...")
                
                # Apply the RSS fallback date if the native extraction couldn't pierce the cookie wall 
                final_date_str = content.get('date') or rss_date
                content['date'] = normalize_date(final_date_str)
                
                save_article_to_db(str(DB_PATH), outlet_name, url, content)

if __name__ == '__main__':
    main()
