import sys
from pathlib import Path

# Add the scripts directory to the python path
scripts_path = Path(__file__).parent.parent / 'scripts'
sys.path.append(str(scripts_path))

import sqlite3
import pandas as pd
import pytest
from unittest.mock import patch
import setup_db
from data_parser import (
    convert_to_half_years,
    rename_agricultural_ministry,
    unify_media_names,
    add_policy_buckets_col,
    load_data_into_db
)

def test_convert_to_half_years():
    df = pd.DataFrame({
        'rechtstraeger': ['A', 'B', 'C', 'D'],
        'quartal': [20221, 20222, 20223, 20224],
        'medium': ['X', 'Y', 'Z', 'W'],
        'euro': [100, 200, 300, 400]
    })
    result = convert_to_half_years(df)
    
    assert 'quartal' not in result.columns
    assert 'half-year' in result.columns
    assert result['half-year'].tolist() == ['20221', '20221', '20222', '20222']

def test_rename_agricultural_ministry():
    old_name = 'Bundesministerium für Land- und Forstwirtschaft, Regionen und Wasserwirtschaft'
    correct_name = 'Bundesministerium für Landwirtschaft, Regionen und Tourismus'
    
    df = pd.DataFrame({
        'halbjahr': ['20211', '20212', '20221', '20222', '20231'],
        'rechtstraeger': [old_name, old_name, old_name, old_name, old_name]
    })
    
    result = rename_agricultural_ministry(df)
    
    # Only rows with halbjahr <= '20221' should be renamed
    expected = [correct_name, correct_name, correct_name, old_name, old_name]
    assert result['rechtstraeger'].tolist() == expected

@patch("pandas.read_csv")
def test_unify_media_names(mock_read_csv):
    # Mock the two synonym DataFrames
    mock_pre2024 = pd.DataFrame({'synonym': ['krone', 'standard', 'orf'], 'medium': ['Kronen Zeitung', 'Der Standard', 'ORF']})
    mock_2024 = pd.DataFrame({'synonym': ['kurier'], 'medium': ['Kurier']})
    
    mock_read_csv.side_effect = [mock_pre2024, mock_2024]
    
    df = pd.DataFrame({
        'medium': ['Krone', 'STANDARD', 'kurier', 'unknown_medium', 'orf']
    })
    
    result = unify_media_names(df, 'medium')
    
    # 'unknown_medium' should be dropped because it is not in the synonyms
    # The names should be title-cased, but ORF should be capitalized if it existed.
    
    expected_media = ['Kronen Zeitung', 'Der Standard', 'Kurier', 'ORF']
    assert len(result) == 4
    assert result['medium'].tolist() == expected_media

@patch("pandas.read_csv")
def test_add_policy_buckets_col(mock_read_csv):
    mock_ministers = pd.DataFrame({
        'ministry': ['Min A', 'Min B'],
        'bucket': ['Bucket A', 'Bucket B']
    })
    mock_read_csv.return_value = mock_ministers
    
    df = pd.DataFrame({
        'ministry': ['Min A', 'Min B', 'Min A']
    })
    
    result = add_policy_buckets_col(df)
    
    assert 'policy_bucket' in result.columns
    assert result['policy_bucket'].tolist() == ['Bucket A', 'Bucket B', 'Bucket A']

def test_load_data_into_db_keeps_schema(tmp_path):
    temp_db_path = tmp_path / "test_db.db"
    df = pd.DataFrame({
        'ministry': ['Min A', 'Min B'],
        'medium': ['ORF', 'Kurier'],
        'euro': [100.0, 200.0],
        'half-year': ['20221', '20222'],
        'policy_bucket': ['Bucket A', 'Bucket B']
    })

    with patch("setup_db.DB_PATH", temp_db_path), patch("data_parser.DB_PATH", temp_db_path):
        setup_db.main()
        # Loading twice must replace the data instead of duplicating it
        load_data_into_db(df)
        load_data_into_db(df)

    con = sqlite3.connect(temp_db_path)
    columns = [info[1] for info in con.execute("PRAGMA table_info(media_spending)").fetchall()]
    rows = con.execute("SELECT ministry, medium, euro, half_year, policy_bucket FROM media_spending").fetchall()
    con.close()

    assert columns == ['id', 'ministry', 'medium', 'euro', 'half_year', 'policy_bucket']
    assert rows == [('Min A', 'ORF', 100.0, 20221, 'Bucket A'), ('Min B', 'Kurier', 200.0, 20222, 'Bucket B')]
