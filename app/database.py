from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import URL, create_engine

ROOT = Path(__file__).resolve().parents[1]
config = dotenv_values(ROOT / ".env", interpolate=False)

required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
if any(not config.get(key) for key in required):
    raise RuntimeError("Не заполнены обязательные параметры подключения в .env.")

url = URL.create(
    drivername="postgresql+psycopg",
    username=config["DB_USER"],
    password=config["DB_PASSWORD"],
    host=config["DB_HOST"],
    port=int(config["DB_PORT"]),
    database=config["DB_NAME"],
)

engine = create_engine(
    url,
    pool_pre_ping=True,
    connect_args={
    "connect_timeout": 5,
    "options": "-c timezone=UTC",
    },
    echo=False,
)
