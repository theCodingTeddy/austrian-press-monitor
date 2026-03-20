import logging
import re
from pathlib import Path
import pdfplumber

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class KampagnenberichtParser:
    """Parser designed to extract date ranges and costs from Kampagnenberichte PDFs (§ 2 Abs. 1b MedKF-TG)."""
    
    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
    
    def extract_data(self) -> dict:
        """
        Extracts the campaign's date range and total cost from the PDF.
        Returns a dictionary with parsed results.
        """
        logger.info(f"Processing PDF: {self.pdf_path}")
        if not self.pdf_path.exists():
            logger.error(f"File not found: {self.pdf_path}")
            return {"error": "File not found"}
        
        extracted_text = ""
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        extracted_text += text + "\n"
        except Exception as e:
            logger.error(f"Error reading PDF: {e}")
            return {"error": str(e)}
            
        # These regex patterns represent templates and may vary strongly depending
        # on the precise layout and phrasing of the Ministry PDFs.
        
        # 1. Dates Extraction (Look for typical formats like DD.MM.YYYY)
        date_pattern = re.compile(r'(\d{1,2}\.\d{1,2}\.\d{4})')
        dates_found = date_pattern.findall(extracted_text)
        
        start_date = dates_found[0] if dates_found else None
        end_date = dates_found[-1] if len(dates_found) > 1 else start_date
        
        # 2. Cost Extraction (Look for typical currency patterns e.g. 1.234,56 €)
        cost_pattern = re.compile(r'(\d{1,3}(?:\.\d{3})*,\d{2})\s*(?:€|EUR)|(?:€|EUR)\s*(\d{1,3}(?:\.\d{3})*,\d{2})')
        costs_found = cost_pattern.findall(extracted_text)
        
        total_cost = None
        if costs_found:
            # Flatten tuples from regex, filter out empty matches
            matches = [m for t in costs_found for m in t if m]
            if matches:
                # Naive templated approach: taking the first matched cost.
                # A more robust script might look for keywords like "Gesamtkosten:" prior to the match.
                total_cost = matches[0]
                
        result = {
            "start_date": start_date,
            "end_date": end_date,
            "total_cost": total_cost,
            "excerpt": extracted_text[:200].replace("\n", " ") + "..." # snippet for preview
        }
        
        logger.info(f"Extraction result: {result}")
        return result

if __name__ == '__main__':
    # Usage Example -> Can be expanded to iterate over a directory
    data_dir = Path(__file__).resolve().parent.parent / 'data'
    sample_pdf = data_dir / 'sample_bericht.pdf'
    
    if sample_pdf.exists():
        parser = KampagnenberichtParser(sample_pdf)
        data = parser.extract_data()
        print(data)
    else:
        logger.info(f"Please place a PDF at {sample_pdf} to test the extractor.")
