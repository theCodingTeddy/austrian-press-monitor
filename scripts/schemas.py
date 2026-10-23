import pandera.pandas as pa
from pandera.typing import Series
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from typing import Optional

# Dates in the synonym lists are formatted as M/D/YY, e.g. 1/7/20
PERIOD_DATE_PATTERN = r"^\d{1,2}/\d{1,2}/\d{2}$"

class BaseMediaSpendingSchema(pa.DataFrameModel):
    """Schema for intermediate dataframes before policy_bucket is added."""
    ministry: Series[str] = pa.Field(nullable=False)
    medium: Series[str] = pa.Field(nullable=False)
    euro: Series[float] = pa.Field(gt=0, nullable=False)
    half_year: Series[str] = pa.Field(alias="half-year", str_matches=r"^\d{4}[12]$", nullable=False)

class FinalMediaSpendingSchema(BaseMediaSpendingSchema):
    """Schema for the final dataframe loaded into the DB."""
    policy_bucket: Series[str] = pa.Field(nullable=False)

class PeriodSchema(pa.DataFrameModel):
    """Schema for dataframes containing a validity period, with a check that the period does not end before it starts."""
    start_of_period: Series[str] = pa.Field(str_matches=PERIOD_DATE_PATTERN, nullable=False)
    end_of_period: Series[str] = pa.Field(str_matches=PERIOD_DATE_PATTERN, nullable=False)

    @pa.dataframe_check
    def period_is_ordered(cls, df: pd.DataFrame) -> Series[bool]:
        start = pd.to_datetime(df['start_of_period'], format='%m/%d/%y', errors='coerce')
        end = pd.to_datetime(df['end_of_period'], format='%m/%d/%y', errors='coerce')
        return start <= end

class MinisterSchema(PeriodSchema):
    """Schema for the ministers dataframe for the news scraper."""
    name: Series[str] = pa.Field(nullable=False)
    party: Series[str] = pa.Field(nullable=False)
    ministry: Series[str] = pa.Field(nullable=False)
    bucket: Series[str] = pa.Field(nullable=False)

class SynonymSchema(PeriodSchema):
    """Schema for the synonyms dataframe for the news scraper."""
    bucket: Series[str] = pa.Field(nullable=False)
    synonym: Series[str] = pa.Field(nullable=False)

class NewsArticle(BaseModel):
    """Model for a scraped news article before it is saved to the DB."""
    model_config = ConfigDict(str_strip_whitespace=True)

    medium: str = Field(min_length=1)
    url: HttpUrl
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
    headline: Optional[str] = None
    # Shorter texts are usually cookie banners or paywall teasers
    full_text: str = Field(min_length=200)
    section: Optional[str] = None

    @field_validator('date', 'headline', 'section', mode='before')
    @classmethod
    def empty_to_none(cls, value):
        return value or None
