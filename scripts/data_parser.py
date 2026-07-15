import sqlite3
from setup_db import logger, DATA_DIR, DB_PATH
from pathlib import Path
import pandas as pd

# TODO: add logs

def main():
    # Unmodified CSVs from the RTR website
    csv_pre2024 = DATA_DIR / 'rtr_csv' / 'mt-bekanntgabe.csv'
    csv_2024 = DATA_DIR / 'rtr_csv' / 'mt-bekanntgabe2024.csv'

    # Preprocessing of the 2020-2023 data
    df_pre2024 = (
        pd.read_csv(csv_pre2024, sep=';', encoding='utf-8', decimal=',')
        .pipe(remove_leermeldungen)
        .drop(columns=['bekanntgabe', 'leermeldung'])
        .pipe(remove_non_ministries)
        .pipe(unify_media_names, 'medium')
        .pipe(convert_to_half_years)
        .rename(columns={'rechtstraeger': 'ministry'})
    )
    
    # Preprocessing of the 2024-2025 data
    df_2024 = (
        pd.read_csv(csv_2024, sep=';', encoding='utf-8', decimal=',')
        .drop(columns=['rechtstraeger_id', 'bekanntgabe', 'kategorie', 'subkategorie',
                        'medium', 'kampagne', 'sujet', 'sujet_dateiname', 'sujet_mimetype'])
        .pipe(remove_non_ministries)
        .pipe(rename_agricultural_ministry)
        .pipe(unify_media_names, 'medieninhaber')
        .rename(columns={'rechtstraeger': 'ministry', 'medieninhaber': 'medium', 'halbjahr': 'half-year'})
    )

    # Merging both dataframes and adding the policy buckets column
    df_all_time = (
        merge_rtr_tables(df_pre2024, df_2024)
        .pipe(add_policy_buckets_col)
    )
    
    logger.info(f'Preprocessed data frame with the following columns:\n\t{df_all_time.columns.to_list()}')

    # Loading into SQLite database
    load_data_into_db(df_all_time)

#------------------------- HELPER FUNCTIONS -------------------------#

def remove_leermeldungen(df: pd.DataFrame) -> pd.DataFrame:
    """
    Removes 'Leermeldungen' (records where the advertising volume is 0) from the dataframe.
    """
    return df[df['leermeldung'] == 0]
    
def remove_non_ministries(df: pd.DataFrame) -> pd.DataFrame:
    """
    Removes records from the dataframe which do not contain 'Bundesministerium' or 'Bundeskanzleramt'.
    """
    return df[df['rechtstraeger'].str.contains('Bundesministerium') | df['rechtstraeger'].str.contains('Bundeskanzleramt')]

def convert_to_half_years(df: pd.DataFrame) -> pd.DataFrame:
    """
    Converts the given dataframe with quarterly data to a half-yearly one by summing the quarters up.
    The dataframe must contain the columns: rechtstraeger, quartal, medium, euro
    """

    df['half-year'] = df['quartal'].astype(str).str[:4] + df['quartal'].astype(str).str[4].map({
        '1': '1', '2': '1', 
        '3': '2', '4': '2'
    })
    df.drop(columns=['quartal'], inplace=True)

    return df

def unify_media_names(df: pd.DataFrame, colname: str) -> pd.DataFrame:
    """
    Names selected newspapers in a unified way, according to the media synonym CSVs.
    Also removes newspapers which are not meant to be tracked from the dataframe.
    """

    # Read synonym CSV files
    media_synonyms_df_pre2024 = pd.read_csv(DATA_DIR / 'synonym_lists' / 'media_synonyms_pre2024.csv', sep=';', encoding='utf-8')
    media_synonyms_df_2024    = pd.read_csv(DATA_DIR / 'synonym_lists' / 'media_synonyms_2024.csv',    sep=';', encoding='utf-8')

    # Combine both synonym lists into one
    media_synonyms_df = pd.concat([media_synonyms_df_pre2024, media_synonyms_df_2024], ignore_index=True)
    
    # Creating a map from the synonym list (lowercase) to the preferred medium name
    media_to_synonym_map = dict(zip(media_synonyms_df['synonym'].str.lower(), media_synonyms_df['medium']))
    
    # Replacing the various media names with the preferred ones for uniformity
    df[colname] = df[colname].str.lower().map(media_to_synonym_map)

    # Removing all media which do not occur in the synonym lists
    df = df.dropna(subset=colname) # TODO: add .copy()?

    # Title-casing the newspaper names
    df[colname] = df[colname].str.title()

    # Turning 'ORF' uppercase
    df[colname] = df[colname].str.replace('Orf', 'ORF')
    
    return df
    
def add_policy_buckets_col(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds a policy bucket column to the dataframe by leveraging the 'ministers' table.
    """

    # Read CSV file which contains ministries and policy buckets
    ministers_df = pd.read_csv(DATA_DIR / 'synonym_lists' / 'ministers.csv', sep=',', encoding='utf-8')

    # Creating a map from the ministry to the policy bucket
    ministry_to_bucket_map = dict(zip(ministers_df['Ministry'], ministers_df['Bucket']))
    
    # Adding a new column containing the policy buckets
    df['policy_bucket'] = df['ministry'].map(ministry_to_bucket_map)

    return df

def rename_agricultural_ministry(df: pd.DataFrame) -> pd.DataFrame:
    """
    Renames 'BM für Land- und Forstwirtschaft, ...' into the correct name until H1 2022.
    """

    mask = df['halbjahr'].astype(str) <= '20221'
    df.loc[mask, 'rechtstraeger'] = df.loc[mask, 'rechtstraeger'].replace({
        'Bundesministerium für Land- und Forstwirtschaft, Regionen und Wasserwirtschaft'
        : 'Bundesministerium für Landwirtschaft, Regionen und Tourismus'})
    
    return df

def merge_rtr_tables(df1: pd.DataFrame, df2: pd.DataFrame) -> pd.DataFrame:
    """
    Merges the two given RTR tables into one, supposed that they have the same columns.
    """
    return pd.concat([df1, df2], ignore_index=True)

def load_data_into_db(df: pd.DataFrame) -> None:
    """
    Loads the cleaned and merged data frame into the SQLite database.
    """

    try:
        # Connect to the database
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        
        # Load dataframe into SQLite database
        df.to_sql('media_spending', con, if_exists='replace', index=False)
        con.commit()
        con.close()

    except sqlite3.Error as e:
        logger.error(f'An error occured while loading spending data into the database: {e}')
    
    logger.info('Media spending data loaded successfully.')

if __name__ == '__main__':
    main()