import logging
import torch
import spacy
from transformers import pipeline
import sqlite3
from pathlib import Path
import re

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "media_analysis.db"

MINISTRIES = {
    "BMI": ["Bundesministerium für Inneres", "Innenministerium", "BMI"],
    "BMEIA": ["BMEIA", "Außenministerium", "Bundesministerium für europäische und internationale Angelegenheiten"],
    "BMLV": ["BMLV", "Verteidigungsministerium", "Bundesministerium für Landesverteidigung"],
    "BKA": ["Bundeskanzleramt", "BKA"],
    "BMLUK": ["BMLUK", "Umweltministerium", "Klimaministerium", "BMK", "Bundesministerium für Klimaschutz"]
}

class NLPEngine:
    """Engine for processing news articles to extract critical entities and assign context-aware sentiment."""
    
    def __init__(self):
        # Configure hardware device for Mac Silicon (MPS constraints)
        if torch.backends.mps.is_available():
            self.device = torch.device("mps")
            logger.info("Apple Silicon MPS hardware detected. Running ML models on MPS.")
        else:
            self.device = torch.device("cpu")
            logger.info("MPS not available. Falling back to CPU execution.")

        logger.info("Initializing NLP Models... This may take a moment.")

        # Load SpaCy German model for Named Entity Recognition (NER)
        try:
            self.nlp = spacy.load("de_core_news_sm")
            logger.info("Successfully loaded SpaCy NER model 'de_core_news_sm'")
        except OSError:
            logger.error("SpaCy model 'de_core_news_sm' not found. Please run: python -m spacy download de_core_news_sm")
            raise

        # Load Hugging Face German sentiment model
        model_name = "oliverguhr/german-sentiment-bert"
        try:
            # We configure the pipeline to utilize our defined hardware device
            self.sentiment_analyzer = pipeline("sentiment-analysis", model=model_name, device=self.device)
            logger.info(f"Successfully loaded Transformers sentiment model '{model_name}'")
        except Exception as e:
            logger.error(f"Failed to load Hugging Face sentiment model: {e}")
            raise

    def get_context_window(self, doc, match_start_char: int, match_end_char: int, window_size: int = 15) -> str:
        """
        Extracts a specific window of tokens (words) around a character-level regex match.
        This prevents Sentiment Dilution by isolating the context directly regarding the entity.
        """
        # Find which token the match starts in
        start_token_idx = 0
        end_token_idx = len(doc) - 1
        
        for token in doc:
            if token.idx >= match_start_char:
                start_token_idx = token.i
                break
                
        for token in doc:
            if token.idx + len(token) >= match_end_char:
                end_token_idx = token.i
                break
                
        left_bound = max(0, start_token_idx - window_size)
        right_bound = min(len(doc), end_token_idx + window_size + 1)
        
        return doc[left_bound:right_bound].text

    def process_article(self, text: str) -> list[dict]:
        """
        Processes full_text to explicitly detect our targeted Ministries using regex,
        isolates a contextual window around them, and assigns sentiment scores.
        """
        doc = self.nlp(text)
        results = []
        
        # We loop over the synonym dictionary to guarantee 100% recall for targeted organizations
        for org_key, synonyms in MINISTRIES.items():
            for synonym in synonyms:
                # Find all exact phrase mentions of this synonym inside the full text
                # We use word boundaries \b to prevent matching partial words
                pattern = r'\b' + re.escape(synonym) + r'\b'
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    # Extract roughly +/- 15 words around the mention
                    isolated_context = self.get_context_window(doc, match.start(), match.end(), window_size=15)
                    
                    try:
                        sentiment_result = self.sentiment_analyzer(isolated_context)[0]
                        
                        # Score mapping
                        str_label = sentiment_result['label']
                        if str_label == 'positive':
                            score = 1.0
                        elif str_label == 'negative':
                            score = -1.0
                        else:
                            score = 0.0
                            
                        results.append({
                            "entity_mentioned": org_key,
                            "sentiment_score": score,
                            "confidence": sentiment_result['score'],
                            "context_clip": isolated_context
                        })
                    except Exception as e:
                        logger.error(f"Error computing sentiment on context chunk: {e}")

        return results

def run_nlp_pipeline():
    """Batches unprocessed SQLite articles through the NLPEngine and updates the DB."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Fetch articles that do not have any entries in analysis_results yet
        cursor.execute('''
            SELECT a.id, a.full_text 
            FROM news_articles a
            LEFT JOIN analysis_results r ON a.id = r.article_id
            WHERE r.id IS NULL
        ''')
        articles_to_process = cursor.fetchall()
    except Exception as e:
        logger.error(f"Database error during read: {e}")
        return
        
    if not articles_to_process:
        logger.info("No unanalyzed articles found. Pipeline complete.")
        if 'conn' in locals():
            conn.close()
        return
        
    logger.info(f"Found {len(articles_to_process)} unanalyzed articles. Booting Engine...")
    try:
        engine = NLPEngine()
    except Exception as e:
        logger.error(f"Failed to boot NLPEngine: {e}")
        conn.close()
        return
        
    processed_count = 0
    mentions_found = 0
    
    for row in articles_to_process:
        article_id = row['id']
        text = row['full_text']
        
        # 2. Extract sentiments on specific isolated Ministry references
        results = engine.process_article(text)
        
        # 3. Store extracted records securely into analysis_results
        try:
            if not results:
                # Insert a dummy record mapping 0.0 to 'NONE' if no ministries were found at all
                # This explicitly marks the article as "analyzed" so it isn't repeatedly fed through the model on subsequent runs!
                cursor.execute('''
                    INSERT INTO analysis_results (article_id, entity_mentioned, sentiment_score, confidence)
                    VALUES (?, ?, ?, ?)
                ''', (article_id, 'NONE', 0.0, 1.0))
            else:
                for r in results:
                    cursor.execute('''
                        INSERT INTO analysis_results (article_id, entity_mentioned, sentiment_score, confidence)
                        VALUES (?, ?, ?, ?)
                    ''', (article_id, r['entity_mentioned'], r['sentiment_score'], r['confidence']))
                    mentions_found += 1
                    
            conn.commit()
            processed_count += 1
        except Exception as e:
            logger.error(f"Failed to insert NLP results for article {article_id}: {e}")
            conn.rollback()
            
    conn.close()
    logger.info(f"NLP Pipeline complete. Processed {processed_count} articles, discovering {mentions_found} explicit targeted sentiments.")

if __name__ == '__main__':
    run_nlp_pipeline()
