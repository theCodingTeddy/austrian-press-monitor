from pathlib import Path
import pandas as pd

# Define file paths
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
DB_PATH = DATA_DIR / 'rtr_db' / 'austrian_media_data.db'

def main():
    csv_pre2024 = DATA_DIR / 'rtr_csv' / 'mt-bekanntgabe.csv'
    csv_2024 = DATA_DIR / 'rtr_csv' / 'mt-bekanntgabe2024.csv'

    df_pre2024 = (
        pd.read_csv(csv_pre2024, sep=';', encoding='utf-8', decimal=',')
        .drop(columns=[''])
    )

    df_2024 = (
        pd.read_csv(csv_2024, sep=';', encoding='utf-8', decimal=',')
        .drop(columns=[''])
    )


    parse_synonyms_csv()
    clean_rtr_data()
    rename_agricultural_ministry()
    unify_temporal_data()
    merge_rtr_tables()
    add_policy_buckets()
    load_data_into_db()

    
# remove
def parse_synonyms_csv():
    """
    Parses 'ministers.csv' and 'synonym_list.csv' into SQL tables,
    making sure that dates and "Umlaute" are read correctly.
    """
    pass

def clean_rtr_data():
    """
    Renames newspapers in a unified way and removes unwanted entries.
    """

    # remove unwanted newspapers
    # remove 'rechtstraeger' which do not contain "Bundesministerium" or "Bundeskanzleramt"
    # remove entries where 'leermeldung' is 1
    pass

def rename_agricultural_ministry():
    """
    Renames "BM für Land- und Forstwirtschaft, ..." into correct name before Q3 2022.
    """
    pass

def unify_temporal_data():
    """
    Unifies temporal data into half-years by summing quarters up.
    """
    pass

def merge_rtr_tables():
    """
    Merges the two RTR tables into one.
    """
    pass

def add_policy_buckets():
    """
    Adds a column to the RTR table for policy buckets by leveraging the 'ministers' table.
    """
    pass

def load_data_into_db():
    """
    Loads the cleaned and merged data frame into the SQLite database.
    """
    pass

if __name__ == "__main__":
    main()