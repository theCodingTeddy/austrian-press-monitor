import os
import pandas as pd
import sqlite3

# Define file paths
csv_pre2024 = "data/rtr_csv/mt-bekanntgabe.csv"
csv_2024 = "data/rtr_csv/mt-bekanntgabe2024.csv"
output_dir = "output/visualizations/spending_tables"
db_path = "data/rtr_csv/medientransparenz.db"

def main():
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Parse pre-2024 dataset
    # Columns needed: rechtstraeger, quartal, leermeldung, medium, euro
    df_pre = pd.read_csv(
        csv_pre2024, 
        sep=';', 
        usecols=["rechtstraeger", "quartal", "leermeldung", "medium", "euro"],
        dtype={'quartal': str, 'leermeldung': float}
    )
    
    # Clean up euro column: handle strings with comma as decimal separator, fill NaNs
    df_pre['euro'] = df_pre['euro'].astype(str).str.replace(',', '.').str.replace(' ', '')
    df_pre['euro'] = pd.to_numeric(df_pre['euro'], errors='coerce').fillna(0.0)
    
    # Where leermeldung == 1, set euro to 0
    df_pre.loc[df_pre['leermeldung'] == 1, 'euro'] = 0.0
    
    # Connect to SQLite DB
    conn = sqlite3.connect(db_path)
    
    # Export to SQL
    df_pre.to_sql("pre2024", conn, if_exists="replace", index=False)
    
    # 2. Parse 2024 dataset
    # Columns needed: rechtstraeger, halbjahr, medieninhaber, euro
    df_2024 = pd.read_csv(
        csv_2024, 
        sep=';', 
        usecols=["rechtstraeger", "halbjahr", "medieninhaber", "euro"],
        dtype={'halbjahr': str}
    )
    
    # Clean up euro column
    df_2024['euro'] = df_2024['euro'].astype(str).str.replace(',', '.').str.replace(' ', '')
    df_2024['euro'] = pd.to_numeric(df_2024['euro'], errors='coerce').fillna(0.0)
    
    # Export to SQL
    df_2024.to_sql("post2024", conn, if_exists="replace", index=False)
    
    # 3. Create four tables and export to CSV
    
    # Table 1: top spending ministries pre-2024
    top_spending_ministries_pre = pd.read_sql_query("""
        SELECT rechtstraeger, SUM(euro) as total_spent
        FROM pre2024
        WHERE rechtstraeger LIKE "%ministerium%"
        GROUP BY rechtstraeger
        ORDER BY total_spent DESC
    """, conn)
    top_spending_ministries_pre.to_csv(os.path.join(output_dir, "top_spending_ministries_pre2024.csv"), index=False)
    
    # Table 2: 10 newspapers (medium) pre-2024
    top10_media_pre = pd.read_sql_query("""
        SELECT medium, SUM(euro) as total_received
        FROM pre2024
        WHERE medium IS NOT NULL AND medium != ''
        GROUP BY medium
        ORDER BY total_received DESC
        LIMIT 10
    """, conn)
    top10_media_pre.to_csv(os.path.join(output_dir, "top_10_media_pre2024.csv"), index=False)
    
    # Table 3: top spending ministries 2024
    top_spending_ministries_2024 = pd.read_sql_query("""
        SELECT rechtstraeger, SUM(euro) as total_spent
        FROM post2024
        WHERE rechtstraeger LIKE "%ministerium%"
        GROUP BY rechtstraeger 
        ORDER BY total_spent DESC
    """, conn)
    top_spending_ministries_2024.to_csv(os.path.join(output_dir, "top_spending_ministries_2024.csv"), index=False)
    
    # Table 4: 10 newspapers (medieninhaber) 2024
    top10_media_2024 = pd.read_sql_query("""
        SELECT medieninhaber, SUM(euro) as total_received
        FROM post2024
        WHERE medieninhaber IS NOT NULL AND medieninhaber != ''
        GROUP BY medieninhaber
        ORDER BY total_received DESC
        LIMIT 10
    """, conn)
    top10_media_2024.to_csv(os.path.join(output_dir, "top_10_media_2024.csv"), index=False)
    
    conn.close()
    print("Data parsing and export completed successfully.")

if __name__ == "__main__":
    main()
