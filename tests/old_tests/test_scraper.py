import unittest
from unittest.mock import patch, MagicMock
import sqlite3
import tempfile
from pathlib import Path
from scripts.scraper import BaseScraper, DerStandardScraper, save_article_to_db

class TestDatabaseInsertion(unittest.TestCase):
    def setUp(self):
        # Create a temporary database and initialize schema
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.temp_db.name
        
        # Initialize the schema
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outlet TEXT NOT NULL,
            date TEXT,
            headline TEXT,
            full_text TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL
        )
        ''')
        conn.commit()
        conn.close()

    def tearDown(self):
        import os
        os.unlink(self.db_path)

    def test_save_article_to_db(self):
        article_data = {
            "headline": "Test Headline",
            "full_text": "This is a test article about BKA.",
            "date": "2024-05-01"
        }
        url = "https://www.derstandard.at/story/test-article"
        outlet = "Der Standard"
        
        # Call the function
        save_article_to_db(self.db_path, outlet, url, article_data)
        
        # Verify it was saved
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT outlet, Date, headline, full_text, url FROM news_articles WHERE url = ?", (url,))
        result = cursor.fetchone()
        self.assertIsNotNone(result)
        self.assertEqual(result[0], outlet)
        self.assertEqual(result[1], "2024-05-01")
        self.assertEqual(result[2], "Test Headline")
        self.assertEqual(result[3], "This is a test article about BKA.")
        self.assertEqual(result[4], url)
        conn.close()
        
    def test_duplicate_article_handling(self):
        article_data = {
            "headline": "Test Headline",
            "full_text": "Content",
            "date": "2024-05-01"
        }
        url = "https://test.com/123"
        outlet = "Test Outlet"
        
        save_article_to_db(self.db_path, outlet, url, article_data)
        # Attempt to save the same URL again should be handled gracefully
        save_article_to_db(self.db_path, outlet, url, article_data)
        
        # Verify only one exists
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM news_articles WHERE url = ?", (url,))
        count = cursor.fetchone()[0]
        self.assertEqual(count, 1)
        conn.close()

class TestScrapers(unittest.TestCase):
    @patch('scripts.scraper.sync_playwright')
    def test_der_standard_dry_run(self, mock_playwright):
        scraper = DerStandardScraper(dry_run=True)
        links = scraper.get_article_links("2024-01-01", "2024-01-31", ["BKA"])
        
        self.assertTrue(len(links) > 0)
        self.assertTrue(links[0].startswith("https://www.derstandard.at"))
        
        content = scraper.extract_article_content(links[0])
        self.assertIn("headline", content)
        self.assertIn("full_text", content)
        self.assertIn("date", content)

if __name__ == '__main__':
    unittest.main()
