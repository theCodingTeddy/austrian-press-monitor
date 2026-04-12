import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Setup directories
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "media_analysis.db"
OUTPUT_DIR = BASE_DIR / "output" / "visualizations"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Set visual style
sns.set_theme(style="whitegrid")

def fetch_data():
    """Extract tabular data securely from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    
    # Financial Data (Added dates to allow temporal monthly analysis)
    query_fin = "SELECT organization, amount, start_date, end_date FROM financial_events"
    df_fin = pd.read_sql_query(query_fin, conn)
    
    # NLP Sentiment & Mention Data mapped to dates and newspapers
    query_nlp = '''
    SELECT r.entity_mentioned, r.sentiment_score, a.date, a.outlet
    FROM analysis_results r
    JOIN news_articles a ON r.article_id = a.id
    WHERE r.entity_mentioned != 'NONE'
    '''
    df_nlp = pd.read_sql_query(query_nlp, conn)
    
    conn.close()
    
    # Data Cleaning / Formatting
    if not df_nlp.empty:
        # Convert date to datetime, coerce errors to NaT
        df_nlp['date'] = pd.to_datetime(df_nlp['date'], errors='coerce')
        # Drop rows with totally invalid dates that might corrupt time-series graphing
        df_nlp = df_nlp.dropna(subset=['date'])
        
        # **Extraneous Date Strict Formatting**:
        # Websites occasionally return malformed meta-tags mapping articles to '2007' or '2016'.
        # We enforce our explicit scraper scope bounds to clean the metrics completely.
        df_nlp = df_nlp[(df_nlp['date'] >= '2024-01-01') & (df_nlp['date'] <= '2026-01-31')]
        
        # Extract the Year-Month to perform temporal grouping
        df_nlp['year_month'] = df_nlp['date'].dt.to_period('M')
        
    return df_fin, df_nlp

def plot_spending(df_fin):
    """Generates the Total Budget Expenditure chart."""
    if df_fin.empty:
        return
        
    plt.figure(figsize=(10, 6))
    grouped = df_fin.groupby('organization')['amount'].sum().sort_values(ascending=False).reset_index()
    
    # Convert arbitrary scale to Millions for easier reading
    grouped['amount_millions'] = grouped['amount'] / 1000000.0
    
    ax = sns.barplot(data=grouped, x='organization', y='amount_millions', palette="viridis")
    plt.title('Total Media Campaign Spending per Ministry (2024-2025)', fontsize=14, pad=15)
    plt.ylabel('Amount (in Millions EUR)', fontsize=12)
    plt.xlabel('Ministry', fontsize=12)
    
    # Annotate bars
    for i, p in enumerate(ax.patches):
        ax.annotate(f"€{grouped['amount_millions'].iloc[i]:.2f}M", 
                    (p.get_x() + p.get_width() / 2., p.get_height()), 
                    ha='center', va='center', xytext=(0, 8), textcoords='offset points')
                    
    plt.tight_layout()
    file_path = OUTPUT_DIR / 'spending_by_ministry.png'
    plt.savefig(file_path, dpi=300)
    plt.close()
    logger.info(f"Generated Plot 1: {file_path}")

def plot_share_of_voice(df_nlp):
    """Generates a visualization of absolute mention volume per ministry over time."""
    if df_nlp.empty:
        return
        
    plt.figure(figsize=(12, 6))
    
    # Group by month and ministry
    timeline = df_nlp.groupby(['year_month', 'entity_mentioned']).size().reset_index(name='mentions')
    timeline['month_str'] = timeline['year_month'].astype(str)
    
    sns.lineplot(data=timeline, x='month_str', y='mentions', hue='entity_mentioned', marker='o', linewidth=2)
    
    plt.title('Share of Voice: Mentions over Time', fontsize=14, pad=15)
    plt.xticks(rotation=45)
    plt.ylabel('Number of Mentions', fontsize=12)
    plt.xlabel('Date (Month)', fontsize=12)
    plt.legend(title='Ministry', bbox_to_anchor=(1.05, 1), loc='upper left')
    
    plt.tight_layout()
    file_path = OUTPUT_DIR / 'share_of_voice_timeline.png'
    plt.savefig(file_path, dpi=300)
    plt.close()
    logger.info(f"Generated Plot 2: {file_path}")

def plot_sentiment_over_time(df_nlp):
    """Generates the rolling temporal Mean Sentiment per ministry."""
    if df_nlp.empty:
        return
        
    plt.figure(figsize=(12, 6))
    
    # Group by month mapping Average Sentiment
    timeline = df_nlp.groupby(['year_month', 'entity_mentioned'])['sentiment_score'].mean().reset_index()
    timeline['month_str'] = timeline['year_month'].astype(str)
    
    sns.lineplot(data=timeline, x='month_str', y='sentiment_score', hue='entity_mentioned', marker='s', linewidth=2)
    
    # Add a bold dashed 0.0 line to show explicit neutral baseline
    plt.axhline(0, color='red', linestyle='--', linewidth=1, alpha=0.5, label='Neutral Baseline')
    
    plt.title('Sentiment Tracking (Average Net Score over Time)', fontsize=14, pad=15)
    plt.xticks(rotation=45)
    plt.ylabel('Net Sentiment Score (-1.0 to 1.0)', fontsize=12)
    plt.xlabel('Date (Month)', fontsize=12)
    plt.legend(title='Ministry', bbox_to_anchor=(1.05, 1), loc='upper left')
    
    plt.tight_layout()
    file_path = OUTPUT_DIR / 'sentiment_timeline.png'
    plt.savefig(file_path, dpi=300)
    plt.close()
    logger.info(f"Generated Plot 3: {file_path}")

def plot_spend_vs_sentiment(df_fin, df_nlp):
    """Scatter plot calculating direct correlation between media expenditures and public sentiment metric."""
    if df_fin.empty or df_nlp.empty:
        return
        
    plt.figure(figsize=(9, 7))
    
    # Aggregate Spend
    spend = df_fin.groupby('organization')['amount'].sum().reset_index()
    spend['amount_millions'] = spend['amount'] / 1000000.0
    
    # Aggregate Average Global Sentiment
    sentiment = df_nlp.groupby('entity_mentioned')['sentiment_score'].mean().reset_index()
    sentiment.rename(columns={'entity_mentioned': 'organization'}, inplace=True)
    
    # Merge on the shared key
    merged = pd.merge(spend, sentiment, on='organization', how='inner')
    
    if merged.empty:
        logger.warning("No correlation mapping generated: No crossover between Finance/NLP organization keys.")
        return
        
    sns.scatterplot(data=merged, x='amount_millions', y='sentiment_score', hue='organization', s=200, palette="husl", style='organization')
    
    # Determine absolute correlation visually using regression
    try:
        import scipy.stats as stats
        slope, intercept, r_value, p_value, std_err = stats.linregress(merged['amount_millions'], merged['sentiment_score'])
        sns.regplot(data=merged, x='amount_millions', y='sentiment_score', scatter=False, color='grey', line_kws={"linestyle":"--"})
        corr_text = f"Correlation (R²): {r_value**2:.2f}"
        plt.annotate(corr_text, xy=(0.05, 0.95), xycoords='axes fraction', fontsize=11, bbox=dict(boxstyle="round", fc="w", alpha=0.8))
    except Exception as e:
        logger.debug(f"SciPy not installed or failed for regression layout: {e}")
        
    for i in range(merged.shape[0]):
        plt.text(x=merged.amount_millions[i] + 0.05, y=merged.sentiment_score[i] + 0.005, 
                 s=merged.organization[i], fontdict=dict(color='black', size=10))

    plt.title('Return on Investment: Campaign Spending vs Total Sentiment', fontsize=14, pad=15)
    plt.xlabel('Total Disclosed Campaign Expenses (Millions EUR)', fontsize=12)
    plt.ylabel('Overall Average Sentiment Score', fontsize=12)
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    
    plt.tight_layout()
    file_path = OUTPUT_DIR / 'spend_vs_sentiment.png'
    plt.savefig(file_path, dpi=300)
    plt.close()
    logger.info(f"Generated Plot 4: {file_path}")

def plot_granular_spend_vs_sentiment(df_fin, df_nlp):
    """Maps monthly campaign spend against monthly average sentiment securely per ministry."""
    if df_fin.empty or df_nlp.empty:
        return
        
    # 1. TEMPORAL FINANCIAL DISTRIBUTION
    # Campaigns stretch over months. We must divide the total 'amount' identically over those active months.
    monthly_spend_records = []
    
    for _, row in df_fin.iterrows():
        org = row['organization']
        amount = row['amount']
        
        # Attempt to parse strings natively, fallback to current month on fail
        try:
            start_dt = pd.to_datetime(row['start_date'], format="%d.%m.%Y")
            end_dt = pd.to_datetime(row['end_date'], format="%d.%m.%Y")
            
            # Map into Pandas Periods logic
            start_period = start_dt.to_period('M')
            end_period = end_dt.to_period('M')
            
            months_active = (end_period - start_period).n + 1
            if months_active <= 0: months_active = 1
            
            monthly_amount = amount / months_active
            
            for m in pd.period_range(start_period, end_period, freq='M'):
                monthly_spend_records.append({'organization': org, 'year_month': m, 'spend': monthly_amount})
                
        except Exception:
            # If dates fail to parse, log entirely to 2024-01 to avoid data destruction
            monthly_spend_records.append({'organization': org, 'year_month': pd.Period('2024-01', freq='M'), 'spend': amount})

    spend_df = pd.DataFrame(monthly_spend_records)
    spend_df = spend_df.groupby(['organization', 'year_month'])['spend'].sum().reset_index()
    
    # 2. TEMPORAL SENTIMENT 
    sentiment_df = df_nlp.groupby(['entity_mentioned', 'year_month'])['sentiment_score'].mean().reset_index()
    sentiment_df.rename(columns={'entity_mentioned': 'organization'}, inplace=True)
    
    # Map them securely over an outer join
    merged = pd.merge(spend_df, sentiment_df, on=['organization', 'year_month'], how='outer').fillna(0)
    merged['spend_millions'] = merged['spend'] / 1000000.0
    merged['month_str'] = merged['year_month'].astype(str)
    
    # Sort securely inside the temporal bounds
    merged = merged.sort_values('year_month')
    merged = merged[(merged['year_month'] >= '2024-01') & (merged['year_month'] <= '2026-01')]
    
    # 3. BUILD GRANULAR SUBPLOTS
    ministries = [m for m in df_fin['organization'].unique() if m != '']
    fig, axes = plt.subplots(nrows=len(ministries), ncols=1, figsize=(14, 4 * len(ministries)), sharex=False)
    
    if len(ministries) == 1:
        axes = [axes]
        
    for idx, org in enumerate(ministries):
        ax1 = axes[idx]
        data = merged[merged['organization'] == org]
        
        # If the dataframe is empty for this org natively, gracefully skip
        if data.empty:
            ax1.set_title(f"{org}: Filtered Data Unavailable")
            continue
            
        # Plot Spend (Bars)
        sns.barplot(data=data, x='month_str', y='spend_millions', alpha=0.6, color='b', ax=ax1, label='Monthly Spend (M €)')
        ax1.set_ylabel('Spend (Millions €)', color='b', fontsize=11)
        ax1.tick_params(axis='y', labelcolor='b')
        ax1.set_xlabel('')
        ax1.set_title(f'Marketing Spending vs. Media Sentiment: {org}', fontsize=14, fontweight='bold', pad=10)
        ax1.tick_params(axis='x', rotation=45)
        
        # Plot Sentiment (Line) - Dual Axis configuration 
        ax2 = ax1.twinx()
        sns.lineplot(data=data, x='month_str', y='sentiment_score', color='r', marker='o', ax=ax2, linewidth=3, label='Sentiment Score')
        ax2.axhline(0, color='r', linestyle='--', linewidth=1, alpha=0.5)
        ax2.set_ylabel('Sentiment Score (-1.0 to 1.0)', color='r', fontsize=11)
        ax2.tick_params(axis='y', labelcolor='r')
        
        # Fix overlapping specific legends gracefully
        lines, labels = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax2.legend(lines + lines2, labels + labels2, loc='upper left')

    plt.tight_layout()
    file_path = OUTPUT_DIR / 'granular_spend_vs_sentiment.png'
    plt.savefig(file_path, dpi=300)
    plt.close()
    logger.info(f"Generated Plot 5 (Granular Layout): {file_path}")

def plot_granular_spend_vs_mentions(df_fin, df_nlp):
    """Maps monthly campaign spend against absolute mention count securely per ministry."""
    if df_fin.empty or df_nlp.empty:
        return
        
    # 1. TEMPORAL FINANCIAL DISTRIBUTION
    monthly_spend_records = []
    
    for _, row in df_fin.iterrows():
        org = row['organization']
        amount = row['amount']
        
        try:
            start_dt = pd.to_datetime(row['start_date'], format="%d.%m.%Y")
            end_dt = pd.to_datetime(row['end_date'], format="%d.%m.%Y")
            start_period = start_dt.to_period('M')
            end_period = end_dt.to_period('M')
            
            months_active = (end_period - start_period).n + 1
            if months_active <= 0: months_active = 1
            monthly_amount = amount / months_active
            
            for m in pd.period_range(start_period, end_period, freq='M'):
                monthly_spend_records.append({'organization': org, 'year_month': m, 'spend': monthly_amount})
        except Exception:
            monthly_spend_records.append({'organization': org, 'year_month': pd.Period('2024-01', freq='M'), 'spend': amount})

    spend_df = pd.DataFrame(monthly_spend_records)
    spend_df = spend_df.groupby(['organization', 'year_month'])['spend'].sum().reset_index()
    
    # 2. TEMPORAL MENTIONS COUNT
    mentions_df = df_nlp.groupby(['entity_mentioned', 'year_month']).size().reset_index(name='mentions_count')
    mentions_df.rename(columns={'entity_mentioned': 'organization'}, inplace=True)
    
    # Map them securely over an outer join
    merged = pd.merge(spend_df, mentions_df, on=['organization', 'year_month'], how='outer').fillna(0)
    merged['spend_millions'] = merged['spend'] / 1000000.0
    merged['month_str'] = merged['year_month'].astype(str)
    
    # Sort securely inside the temporal bounds
    merged = merged.sort_values('year_month')
    merged = merged[(merged['year_month'] >= '2024-01') & (merged['year_month'] <= '2026-01')]
    
    # 3. BUILD GRANULAR SUBPLOTS
    ministries = [m for m in df_fin['organization'].unique() if m != '']
    fig, axes = plt.subplots(nrows=len(ministries), ncols=1, figsize=(14, 4 * len(ministries)), sharex=False)
    
    if len(ministries) == 1:
        axes = [axes]
        
    for idx, org in enumerate(ministries):
        ax1 = axes[idx]
        data = merged[merged['organization'] == org]
        
        if data.empty:
            ax1.set_title(f"{org}: Filtered Data Unavailable")
            continue
            
        # Plot Spend (Bars)
        sns.barplot(data=data, x='month_str', y='spend_millions', alpha=0.6, color='b', ax=ax1, label='Monthly Spend (M €)')
        ax1.set_ylabel('Spend (Millions €)', color='b', fontsize=11)
        ax1.tick_params(axis='y', labelcolor='b')
        ax1.set_xlabel('')
        ax1.set_title(f'Marketing Spending vs. Media Mention Volume: {org}', fontsize=14, fontweight='bold', pad=10)
        ax1.tick_params(axis='x', rotation=45)
        
        # Plot Mentions (Line) - Dual Axis configuration 
        ax2 = ax1.twinx()
        sns.lineplot(data=data, x='month_str', y='mentions_count', color='g', marker='^', ax=ax2, linewidth=3, label='Absolute Mentions')
        ax2.set_ylabel('Total Articles / Mentions', color='g', fontsize=11)
        ax2.tick_params(axis='y', labelcolor='g')
        
        # Build strict single limits if mentions_count peaks at 0 to avoid scaling bugs
        max_y = ax2.get_ylim()[1]
        if max_y < 5: ax2.set_ylim(0, 5)
        
        lines, labels = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax2.legend(lines + lines2, labels + labels2, loc='upper left')

    plt.tight_layout()
    file_path = OUTPUT_DIR / 'granular_spend_vs_mentions.png'
    plt.savefig(file_path, dpi=300)
    plt.close()
    logger.info(f"Generated Plot 6 (Volume Granular Layout): {file_path}")

def plot_newspaper_heatmaps(df_nlp):
    """Generates two static heatmaps intersecting Newspapers against Ministries for Sentiment and Volume."""
    if df_nlp.empty: return
    
    # Clean the newspaper domain names conceptually (e.g. 'diepresse.com' -> 'Die Presse')
    domain_map = {
        'derstandard.at': 'Der Standard',
        'krone.at': 'Krone',
        'diepresse.com': 'Die Presse',
        'heute.at': 'Heute',
        'kleinezeitung.at': 'Kleine Zeitung'
    }
    
    df_copy = df_nlp.copy()
    df_copy['outlet_pretty'] = df_copy['outlet'].map(domain_map).fillna(df_copy['outlet'])
    
    # Calculate Heatmap Aggregations
    pivot_sentiment = df_copy.pivot_table(index='outlet_pretty', columns='entity_mentioned', values='sentiment_score', aggfunc='mean')
    pivot_mentions = df_copy.pivot_table(index='outlet_pretty', columns='entity_mentioned', values='sentiment_score', aggfunc='count')
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 6))
    
    sns.heatmap(pivot_sentiment, annot=True, cmap="coolwarm", center=0, ax=ax1, fmt=".2f", linewidths=.5)
    ax1.set_title("Average Sentiment Bias (Newspaper vs Ministry)", fontsize=14, pad=10)
    ax1.set_ylabel("Newspaper")
    ax1.set_xlabel("Ministry")
    
    sns.heatmap(pivot_mentions, annot=True, cmap="YlGnBu", ax=ax2, fmt="g", linewidths=.5)
    ax2.set_title("Absolute Mentions Volume (Newspaper vs Ministry)", fontsize=14, pad=10)
    ax2.set_ylabel("")
    ax2.set_xlabel("Ministry")
    
    plt.tight_layout()
    file_path = OUTPUT_DIR / 'newspaper_cross_heatmaps.png'
    plt.savefig(file_path, dpi=300)
    plt.close()
    logger.info(f"Generated Plot 7 (Newspaper Heatmaps): {file_path}")


def plot_newspaper_ministry_facetgrid(df_fin, df_nlp):
    """Generates a comprehensive 5x5 sub-plot grid mapping Newspaper vs Ministry sentiment, overlaid structurally with respective monthly Marketing Spend."""
    if df_nlp.empty or df_fin.empty: return
    
    # 1. PROCESS FINANCIAL SPEND
    monthly_spend_records = []
    for _, row in df_fin.iterrows():
        org = row['organization']
        amount = row['amount']
        try:
            start_dt = pd.to_datetime(row['start_date'], format="%d.%m.%Y")
            end_dt = pd.to_datetime(row['end_date'], format="%d.%m.%Y")
            start_period = start_dt.to_period('M')
            end_period = end_dt.to_period('M')
            months_active = (end_period - start_period).n + 1
            if months_active <= 0: months_active = 1
            monthly_amount = amount / months_active
            for m in pd.period_range(start_period, end_period, freq='M'):
                monthly_spend_records.append({'organization': org, 'year_month': m, 'spend': monthly_amount})
        except:
            monthly_spend_records.append({'organization': org, 'year_month': pd.Period('2024-01', freq='M'), 'spend': amount})

    spend_df = pd.DataFrame(monthly_spend_records)
    spend_df = spend_df.groupby(['organization', 'year_month'])['spend'].sum().reset_index()
    spend_df['spend_millions'] = spend_df['spend'] / 1000000.0
    spend_df.rename(columns={'organization': 'entity_mentioned'}, inplace=True)
    
    # 2. PROCESS SENTIMENT MAP
    domain_map = {
        'derstandard.at': 'Der Standard',
        'krone.at': 'Krone',
        'diepresse.com': 'Die Presse',
        'heute.at': 'Heute',
        'kleinezeitung.at': 'Kleine Zeitung'
    }
    df_copy = df_nlp.copy()
    df_copy['outlet_pretty'] = df_copy['outlet'].map(domain_map).fillna(df_copy['outlet'])
    heatmap_timeline = df_copy.groupby(['outlet_pretty', 'entity_mentioned', 'year_month'])['sentiment_score'].mean().reset_index()
    
    # 3. BUILD 3D CARTESIAN GRID
    import itertools
    outlets = df_copy['outlet_pretty'].dropna().unique()
    ministries = [m for m in df_fin['organization'].unique() if m != '']
    all_months = pd.period_range(pd.Period("2024-01", freq='M'), pd.Period("2026-01", freq='M'), freq='M')
    
    grid_rows = list(itertools.product(outlets, ministries, all_months))
    grid_df = pd.DataFrame(grid_rows, columns=['outlet_pretty', 'entity_mentioned', 'year_month'])
    
    grid_df = pd.merge(grid_df, spend_df, on=['entity_mentioned', 'year_month'], how='left').fillna({'spend_millions': 0.0})
    grid_df = pd.merge(grid_df, heatmap_timeline, on=['outlet_pretty', 'entity_mentioned', 'year_month'], how='left')
    grid_df['month_str'] = grid_df['year_month'].astype(str)
    
    # 4. PLOT GRID DYNAMICALLY
    sns.set_theme(style="white")
    g = sns.FacetGrid(grid_df, row="entity_mentioned", col="outlet_pretty", margin_titles=True, height=2.8, aspect=1.4, sharey="row")
    
    def dual_axis_plot(data, **kwargs):
        ax = plt.gca()
        if data.empty: return
        
        # Ensure month strings order
        data = data.sort_values(by='month_str')
        
        # Primary axis = Spend amount (blue columns)
        sns.barplot(data=data, x='month_str', y='spend_millions', color='lightblue', alpha=0.6, ax=ax)
        ax.set_ylabel("")
        
        # Secondary axis = Sentiment score
        ax2 = ax.twinx()
        sns.lineplot(data=data, x='month_str', y='sentiment_score', color='purple', marker='o', ax=ax2, linewidth=2)
        ax2.set_ylim(-1.0, 1.0)
        ax2.axhline(0, color='red', linestyle='--', alpha=0.5)
        ax2.set_ylabel("")
        
        # Sparsify Ticks to prevent overlaps
        for ind, label in enumerate(ax.get_xticklabels()):
            if ind % 4 == 0: label.set_visible(True)
            else: label.set_visible(False)
        ax.tick_params(axis='x', rotation=45)
        
        # Hide internal secondary Y limits
        if not ax.get_subplotspec().is_last_col():
            ax2.set_yticks([])
        else:
            ax2.set_ylabel("Sentiment")
    
    g.map_dataframe(dual_axis_plot)
    g.set_axis_labels("", "")
    g.set_titles(row_template="{row_name}", col_template="{col_name}")
    g.fig.suptitle("Temporal 5x5 Grid: Campaign Budget vs Media Sentiment per Newspaper per Ministry", fontsize=18, y=1.02)
    
    plt.tight_layout()
    file_path = OUTPUT_DIR / 'newspaper_5x5_facetgrid.png'
    g.savefig(file_path, dpi=300)
    plt.close()
    
    sns.set_theme(style="whitegrid")
    logger.info(f"Generated Plot 8 (3D 5x5 Overlay FacetGrid): {file_path}")

def main():
    logger.info("Initializing Data Analysis reporting node...")
    df_fin, df_nlp = fetch_data()
    
    if df_nlp.empty:
        logger.warning("NLP Database is null. Metrics aborted.")
        return
        
    logger.info(f"Loaded {len(df_fin)} total financial ledgers and {len(df_nlp)} valid strict sentiment hits.")
    
    plot_spending(df_fin)
    plot_share_of_voice(df_nlp)
    plot_sentiment_over_time(df_nlp)
    plot_spend_vs_sentiment(df_fin, df_nlp)
    plot_granular_spend_vs_sentiment(df_fin, df_nlp)
    plot_granular_spend_vs_mentions(df_fin, df_nlp)
    plot_newspaper_heatmaps(df_nlp)
    plot_newspaper_ministry_facetgrid(df_fin, df_nlp)
    
    logger.info("Analysis visual pipeline completed successfully.")

if __name__ == "__main__":
    main()
