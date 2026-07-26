import sys
from pathlib import Path

# Add the scripts directory to the python path
scripts_path = Path(__file__).parent.parent / 'scripts'
sys.path.append(str(scripts_path))

import sqlite3
import pytest
from unittest.mock import patch
from setup_db import main, DB_PATH

def test_setup_db_creates_tables(tmp_path):
    """Test that main() creates the database and required tables."""
    
    # Mock DB_PATH to a temporary location
    temp_db_path = tmp_path / "test_db.db"
    
    with patch("setup_db.DB_PATH", temp_db_path):
        main()
        
        # Check if DB file was created
        assert temp_db_path.exists()
        
        # Connect and check for tables
        con = sqlite3.connect(temp_db_path)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='media_spending'")
        table_exists = cur.fetchone()
        
        assert table_exists is not None
        
        # Check columns
        cur.execute("PRAGMA table_info(media_spending)")
        columns = [info[1] for info in cur.fetchall()]
        expected_columns = ['id', 'ministry', 'medium', 'euro', 'half_year', 'policy_bucket']
        
        assert columns == expected_columns
        
        con.close()
