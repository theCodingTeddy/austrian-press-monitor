import logging
import torch
import spacy
from transformers import pipeline

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

    def process_article(self, text: str) -> list[dict]:
        """
        Processes full_text to detect political entities and attach sentiment scores.
        Given that long context causes sentiment dilution, we parse sentence by sentence.
        
        Returns a list of dictionaries with matching sentiment constraints.
        """
        logger.info("Processing article through NLP Engine.")
        doc = self.nlp(text)
        results = []
        
        # We loop at sentence granularity
        for sent in doc.sents:
            # Look for entities grouped as Organizations or Persons
            # E.g., 'Bundeskanzleramt', 'Klimaministerium' -> ORG
            entities_in_sent = [ent.text for ent in sent.ents if ent.label_ in ['ORG', 'PER']]
            
            if not entities_in_sent:
                continue
                
            # Truncating sentences to typical BERT token limit just to be safe (512 tokens).
            # Usually single sentences don't exceed this.
            truncated_sent = sent.text[:1500] 
            
            try:
                sentiment_result = self.sentiment_analyzer(truncated_sent)[0]
                
                # Map outcome back to entities in this context block
                for entity in entities_in_sent:
                    results.append({
                        "entity_mentioned": entity,
                        "sentiment_label": sentiment_result['label'],
                        # In the future, we may map labels (positive, negative, neutral) to a numeric -1.0 to 1.0 scope
                        "sentiment_score_str": sentiment_result['label'],
                        "confidence": sentiment_result['score'],
                        "context_clip": sent.text
                    })
            except Exception as e:
                logger.error(f"Error computing sentiment on sentence chunk: {e}")

        logger.info(f"Extraction complete. Found {len(results)} entity mentions with sentiments.")
        return results

if __name__ == '__main__':
    # Test execution for dry run
    try:
        engine = NLPEngine()
        sample_text = (
             "Das Klimaministerium hat ein neues Umweltgesetz vorgestellt. "
             "Der Bundeskanzler zeigte sich über diesen progressiven Schritt sehr erfreut, "
             "während die Opposition das neue Maßnahmenpaket als katastrophal und teuer bezeichnete."
        )
        
        results = engine.process_article(sample_text)
        print("\n--- Dry Run Results ---")
        for r in results:
            print(f"- Entity: {r['entity_mentioned']} | Sentiment: {r['sentiment_label']} (Score: {r['confidence']:.2f})")
    except Exception as e:
        logger.error(f"Dry run failed: {e}")
