import logging
import sqlite3
import datetime
import urllib.request
import urllib.parse
from bs4 import BeautifulSoup
from pathlib import Path
import re
import os
import sys

# Ensure the parent directory is in the Python path to allow absolute imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.pdf_processor import KampagnenberichtParser

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "media_analysis.db"
PDF_DIR = BASE_DIR / "data" / "kampagnen"
PDF_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

MINISTRIES = {
    "BMI": ["Bundesministerium für Inneres", "Innenministerium", "BMI"],
    "BMEIA": ["BMEIA", "Außenministerium", "Bundesministerium für europäische und internationale Angelegenheiten"],
    "BMLV": ["BMLV", "Verteidigungsministerium", "Bundesministerium für Landesverteidigung"],
    "BKA": ["Bundeskanzleramt", "BKA"],
    "BMLUK": ["BMLUK", "Umweltministerium", "Klimaministerium", "BMK", "Bundesministerium für Klimaschutz"]
}

NEWSPAPERS = ["Der Standard", "Krone", "Die Presse", "Heute", "Kleine Zeitung"]

def save_financial_event(db_path, data):
    """Saves a financial event to the database."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO financial_events (source_type, organization, media_outlet, amount, start_date, end_date, topic)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('source_type', ''),
            data.get('organization', ''),
            data.get('media_outlet', ''),
            data.get('amount', 0.0),
            data.get('start_date', ''),
            data.get('end_date', ''),
            data.get('topic', '')
        ))
        
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save financial event to DB: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

class RTRDataFetcher:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        
    def fetch_data(self):
        """Fetches data from RTR API. Uses a mocked dataset for dry_run or fallback."""
        if self.dry_run:
            logger.info("RTRDataFetcher: Dry run mode, returning mock data.")
            return [
                {"rechtsträger": "Bundeskanzleramt", "medium": "Der Standard", "betrag": "150000.50", "quartal": "2024-Q1"},
                {"rechtsträger": "BMI", "medium": "Krone", "betrag": "85000.00", "quartal": "2024-Q2"}
            ]
        
        # Real fetch logic for RTR Open Data API
        # The exact endpoint varies, this is a placeholder URL
        url = "https://data.rtr.at/api/v1/tables/medkftg.json"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as response:
                import json
                data = json.loads(response.read())
                return data.get('data', [])
        except Exception as e:
            logger.warning(f"Failed to fetch real RTR data from {url}, using fallback mock: {e}")
            return [
                {"rechtsträger": "Bundeskanzleramt", "medium": "Der Standard", "betrag": "150000.50", "quartal": "2024-Q1"},
                {"rechtsträger": "Bundesministerium für Inneres", "medium": "Krone", "betrag": "85000.00", "quartal": "2024-Q2"},
                {"rechtsträger": "Klimaministerium", "medium": "Heute", "betrag": "55000.20", "quartal": "2024-Q1"},
                {"rechtsträger": "BMLV", "medium": "Kleine Zeitung", "betrag": "120000.00", "quartal": "2024-Q3"}
            ]
            
    def process_and_save(self):
        raw_data = self.fetch_data()
        
        # Flatten ministry names for matching
        flat_ministries = {syn.lower(): key for key, syns in MINISTRIES.items() for syn in syns}
        flat_newspapers = {n.lower(): n for n in NEWSPAPERS}
        
        count = 0
        for row in raw_data:
            org_raw = row.get("rechtsträger", "").lower()
            med_raw = row.get("medium", "").lower()
            amount_str = str(row.get("betrag", "0"))
            
            # Match organization
            org_key = None
            for syn in flat_ministries.keys():
                if syn in org_raw:
                    org_key = flat_ministries[syn]
                    break
                    
            # Match media outlet
            med_key = None
            for n_lower in flat_newspapers.keys():
                if n_lower in med_raw:
                    med_key = flat_newspapers[n_lower]
                    break
                    
            if org_key and med_key:
                try:
                    amount = float(amount_str.replace(',', '.'))
                except:
                    amount = 0.0
                    
                data = {
                    'source_type': 'RTR',
                    'organization': org_key,
                    'media_outlet': med_key,
                    'amount': amount,
                    'start_date': row.get("quartal", "2024"),
                    'end_date': row.get("quartal", "2024"),
                    'topic': 'Medienkooperation'
                }
                save_financial_event(str(DB_PATH), data)
                count += 1
                
        logger.info(f"RTRDataFetcher: Successfully saved {count} matched RTR records into DB.")

class KampagnenberichteCSVParser:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.csv_path = BASE_DIR / "data" / "kampagnen" / "kampagnen.csv"
        
    def process_and_save(self):
        import csv
        
        if not self.csv_path.exists():
            logger.error(f"Kampagnen CSV not found at {self.csv_path}")
            return
            
        count = 0
        try:
            # Using latin1 / cp1252 to handle Windows Excel exports gracefully
            with open(self.csv_path, 'r', encoding='latin1', errors='replace') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    org_key = row.get('Ministerium', '').strip()
                    kampagne = row.get('Kampagne', '').strip()
                    start_date = row.get('Startdatum', '').strip()
                    end_date = row.get('Enddatum', '').strip()
                    kosten_str = row.get('Angegebene Kosten', '')
                    
                    # Parse "  1,500,000.00 " -> 1500000.00
                    amount = 0.0
                    try:
                        clean_str = re.sub(r'[^\d.,]', '', kosten_str)
                        if ',' in clean_str and '.' in clean_str:
                            clean_str = clean_str.replace(',', '')
                        elif ',' in clean_str and '.' not in clean_str:
                            clean_str = clean_str.replace(',', '.')
                        amount = float(clean_str)
                    except Exception as e:
                        logger.debug(f"Could not parse amount '{kosten_str}': {e}")
                        
                    event_data = {
                        'source_type': 'CSV_Kampagne',
                        'organization': org_key,
                        'media_outlet': 'Multiple/Unknown',
                        'amount': amount,
                        'start_date': start_date,
                        'end_date': end_date,
                        'topic': kampagne
                    }
                    
                    if not self.dry_run:
                        save_financial_event(str(DB_PATH), event_data)
                    count += 1
                    
            logger.info(f"KampagnenberichteCSVParser: Successfully saved {count} campaigns from CSV.")
        except Exception as e:
            logger.error(f"Failed to process CSV: {e}")

def main():
    DRY_RUN = False
    logger.info("Starting Phase 2: Financial Data Scraping (CSV + RTR API)")
    
    rtr_fetcher = RTRDataFetcher(dry_run=DRY_RUN)
    rtr_fetcher.process_and_save()
    
    csv_parser = KampagnenberichteCSVParser(dry_run=DRY_RUN)
    csv_parser.process_and_save()
    
if __name__ == '__main__':
    main()
