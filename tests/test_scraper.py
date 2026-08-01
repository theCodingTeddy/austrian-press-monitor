import sys
from pathlib import Path
import pytest
import datetime

# Add the scripts directory to sys.path so we can import scraper
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from scripts.old_scripts.scraper import is_valid_context

def test_is_valid_context_bmi_valid():
    text = "Der Innenminister Karner hat heute die Polizei besucht."
    assert is_valid_context(text, "BMI") == True
    
def test_is_valid_context_bmi_invalid_health():
    text = "Ein hoher Body Mass Index (BMI) und Übergewicht sind schlecht für die Gesundheit."
    assert is_valid_context(text, "BMI") == False

def test_is_valid_context_bmi_invalid_generic():
    text = "Das ist ein komplett irrelevanter Text ohne Kontextwörter."
    assert is_valid_context(text, "BMI") == False
    
def test_is_valid_context_bka_valid():
    text = "Der Bundeskanzler gab heute eine Pressekonferenz."
    assert is_valid_context(text, "BKA") == True
    
def test_is_valid_context_bka_invalid():
    text = "Das BKA (Bundeskriminalamt in Deutschland) ermittelt."
    # Since none of the context words (bundeskanzler, regierung, kanzler, minister) are there, should be false
    assert is_valid_context(text, "BKA") == False
    
def test_is_valid_context_generic_term():
    # If the term is just "Arbeitsminister", any text that doesn't trigger the explicit BMI/BKA exclusions should be valid
    # Our function currently returns True for any keyword not in ["bmi", "bka"] if text is present
    text = "Der Arbeitsminister plant neue Reformen."
    assert is_valid_context(text, "Arbeitsminister") == True
