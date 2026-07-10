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

    # Connect to the database (this creates it if it doesn't exist)
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
    except sqlite3.Error as e:
        logger.error(e)

if __name__ == "__main__":
    main()