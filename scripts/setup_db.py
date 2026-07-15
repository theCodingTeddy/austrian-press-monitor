import logging
from pathlib import Path
import sqlite3

# Set up logging output
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define file paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'rtr_db' / 'austrian_media_data.db'

def main():
    """
    Creates the database and sets up all necessary tables.
    """

    try:
        # Connect to the database (this creates it if it doesn't exist)
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()

        logger.info(f'Database initialized at path:\n\t{DB_PATH}')

        # Create necessary tables
        cur.execute('''CREATE TABLE IF NOT EXISTS media_spending(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ministry TEXT NOT NULL,
            medium TEXT NOT NULL,
            euro REAL NOT NULL,
            half_year INTEGER NOT NULL,
            policy_bucket TEXT NOT NULL
        )''')

        con.commit()
        con.close()
        
    except sqlite3.Error as e:
        logger.error(f'An error occured during database setup: {e}')

    logger.info('Tables created and database closed successfully.')

if __name__ == '__main__':
    main()