import pandera.pandas as pa
from pandera.typing import Series
import pandas as pd

class BaseMediaSpendingSchema(pa.DataFrameModel):
    """Schema for intermediate dataframes before policy_bucket is added."""
    ministry: Series[str] = pa.Field(nullable=False)
    medium: Series[str] = pa.Field(nullable=False)
    euro: Series[float] = pa.Field(gt=0, nullable=False)
    half_year: Series[str] = pa.Field(alias="half-year", str_matches=r"^\d{4}[12]$", nullable=False)

class FinalMediaSpendingSchema(BaseMediaSpendingSchema):
    """Schema for the final dataframe loaded into the DB."""
    policy_bucket: Series[str] = pa.Field(nullable=False)
