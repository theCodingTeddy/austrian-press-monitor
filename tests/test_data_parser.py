import sys
from pathlib import Path

# Add the scripts directory to the python path
scripts_path = Path(__file__).parent.parent / 'scripts'
sys.path.append(str(scripts_path))

import pandas as pd
import pytest
from unittest.mock import patch
from data_parser import (
    convert_to_half_years,
    rename_agricultural_ministry,
    unify_media_names,
    add_policy_buckets_col
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
        'Ministry': ['Min A', 'Min B'],
        'Bucket': ['Bucket A', 'Bucket B']
    })
    mock_read_csv.return_value = mock_ministers
    
    df = pd.DataFrame({
        'ministry': ['Min A', 'Min B', 'Min A']
    })
    
    result = add_policy_buckets_col(df)
    
    assert 'policy_bucket' in result.columns
    assert result['policy_bucket'].tolist() == ['Bucket A', 'Bucket B', 'Bucket A']
