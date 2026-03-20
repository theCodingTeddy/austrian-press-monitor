import sqlite3
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'media_analysis.db'

def setup_database():
    """Initializes the SQLite database and creates the required tables."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Connecting to database at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. financial_events table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS financial_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL, -- "RTR" or "PDF"
        organization TEXT NOT NULL, -- e.g., "BKA", "Klimaministerium"
        media_outlet TEXT NOT NULL,
        amount REAL NOT NULL,
        start_date TEXT,
        end_date TEXT,
        topic TEXT
    )
    ''')
    
    # 2. news_articles table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS news_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        outlet TEXT NOT NULL, -- e.g., "Der Standard"
        date TEXT,
        headline TEXT,
        full_text TEXT NOT NULL,
        url TEXT UNIQUE NOT NULL
    )
    ''')
    
    # 3. analysis_results table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS analysis_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER,
        entity_mentioned TEXT NOT NULL,
        sentiment_score REAL NOT NULL,
        confidence REAL,
        FOREIGN KEY (article_id) REFERENCES news_articles(id)
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database schema initialized successfully.")

if __name__ == '__main__':
    setup_database()
