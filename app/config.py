import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str


settings = Settings(database_url=os.getenv("DATABASE_URL", "sqlite:///./data/bbs.db"))
