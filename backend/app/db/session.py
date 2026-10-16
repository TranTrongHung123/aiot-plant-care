from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.settings import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_timeout=5,
    connect_args={"connect_timeout": 3, "options": "-c statement_timeout=10000"},
)
Session = sessionmaker(engine, expire_on_commit=False)
