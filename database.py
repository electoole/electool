"""MySQL database layer with prefixed table support."""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - app still works without dotenv installed
    def load_dotenv(*args, **kwargs):
        return False

from canonical_data_loader import EXTRACTED_DIR, compute_features


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

LOGICAL_TABLES = [
    "polling_stations",
    "ward_registration",
    "historical_winners",
    "demographics",
    "candidate_profiles",
    "polling_results",
    "results",
    "sentiment_data",
    "features",
    "data_sources",
    "admin_audit_log",
    "data_versions",
]

MYSQL_INTERNAL_ID = "record_id"

OPERATIONAL_TABLES = {
    "admin_audit_log": [
        "created_at",
        "admin_username",
        "action",
        "table_name",
        "row_count",
        "status",
        "details",
    ],
    "data_versions": [
        "created_at",
        "admin_username",
        "table_name",
        "action",
        "row_count",
        "snapshot_json",
    ],
}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def sanitize_prefix(prefix: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "", prefix or "eii_").lower()
    if not cleaned:
        cleaned = "eii_"
    if not cleaned.endswith("_"):
        cleaned += "_"
    return cleaned


@dataclass(frozen=True)
class DbSettings:
    mysql_host: str = _env("MYSQL_HOST", _env("DB_HOST"))
    mysql_port: int = _env_int("MYSQL_PORT", 3306)
    mysql_database: str = _env("MYSQL_DATABASE", _env("DB_NAME"))
    mysql_user: str = _env("MYSQL_USER", _env("DB_USER"))
    mysql_password: str = _env("MYSQL_PASSWORD", _env("DB_PASSWORD"))
    mysql_ssl_ca: str = _env("MYSQL_SSL_CA")
    mysql_ssl_ca_content: str = os.getenv("MYSQL_SSL_CA_CONTENT", "").strip()
    mysql_ssl_disabled: bool = _env("MYSQL_SSL_DISABLED", "false").lower() in {"1", "true", "yes"}
    mysql_strict: bool = True
    table_prefix: str = sanitize_prefix(_env("DB_TABLE_PREFIX", "eii_"))

    @property
    def mysql_enabled(self) -> bool:
        return bool(self.mysql_host and self.mysql_database and self.mysql_user)


SETTINGS = DbSettings()


class Database:
    def __init__(self, settings: DbSettings = SETTINGS):
        self.settings = settings
        self._engine = None
        self._mysql_available: bool | None = None
        self.last_mysql_error: str = ""
        self._ssl_ca_path: Path | None = None
        self._ensured = False

    @property
    def using_mysql(self) -> bool:
        return self.settings.mysql_enabled

    def require_mysql(self) -> None:
        if not self.settings.mysql_enabled:
            raise RuntimeError(
                "MySQL is required. Set MYSQL_HOST, MYSQL_DATABASE, MYSQL_USER, and MYSQL_PASSWORD."
            )

    def table(self, logical_name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_]", "", logical_name)
        return f"{self.settings.table_prefix}{safe}"

    def physical_sql(self, sql: str) -> str:
        converted = sql
        for table in sorted(LOGICAL_TABLES, key=len, reverse=True):
            converted = re.sub(rf"\b{table}\b", self.table(table), converted)
        return converted.replace("?", "%s")

    def mysql_engine(self):
        if self._engine is None:
            from sqlalchemy import create_engine
            from urllib.parse import quote_plus

            password = quote_plus(self.settings.mysql_password)
            url = (
                f"mysql+pymysql://{self.settings.mysql_user}:{password}"
                f"@{self.settings.mysql_host}:{self.settings.mysql_port}/{self.settings.mysql_database}"
                "?charset=utf8mb4"
            )
            connect_args: dict[str, Any] = {}
            connect_args["connect_timeout"] = 5
            ssl_options = self.mysql_ssl_options()
            if ssl_options:
                connect_args["ssl"] = ssl_options
            self._engine = create_engine(url, pool_pre_ping=True, pool_recycle=1800, connect_args=connect_args)
        return self._engine

    def resolve_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        return path if path.is_absolute() else ROOT / path

    def mysql_ssl_options(self) -> dict[str, str] | None:
        if self.settings.mysql_ssl_disabled:
            return None
        if self.settings.mysql_ssl_ca_content:
            if self._ssl_ca_path is None:
                ca_file = Path(tempfile.gettempdir()) / "eii_mysql_ca.pem"
                ca_file.write_text(self.settings.mysql_ssl_ca_content, encoding="utf-8")
                self._ssl_ca_path = ca_file
            return {"ca": str(self._ssl_ca_path)}
        if self.settings.mysql_ssl_ca:
            return {"ca": str(self.resolve_path(self.settings.mysql_ssl_ca))}
        return None

    def mysql_conn(self):
        import pymysql

        kwargs: dict[str, Any] = {
            "host": self.settings.mysql_host,
            "port": self.settings.mysql_port,
            "database": self.settings.mysql_database,
            "user": self.settings.mysql_user,
            "password": self.settings.mysql_password,
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor,
            "autocommit": True,
            "connect_timeout": 5,
        }
        ssl_options = self.mysql_ssl_options()
        if ssl_options:
            kwargs["ssl"] = ssl_options
        return pymysql.connect(**kwargs)

    def mysql_identifier(self, name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_]", "", str(name))
        if not safe:
            raise ValueError(f"Unsafe MySQL identifier: {name!r}")
        return f"`{safe}`"

    def mysql_column_type(self, series: pd.Series) -> str:
        if pd.api.types.is_integer_dtype(series):
            return "BIGINT"
        if pd.api.types.is_float_dtype(series):
            return "DOUBLE"
        if pd.api.types.is_bool_dtype(series):
            return "TINYINT(1)"
        return "TEXT"

    def create_mysql_table_for_frame(self, table_name: str, frame: pd.DataFrame) -> None:
        table_sql = self.mysql_identifier(self.table(table_name))
        columns: list[str] = []
        if MYSQL_INTERNAL_ID not in frame.columns:
            columns.append(f"{self.mysql_identifier(MYSQL_INTERNAL_ID)} BIGINT NOT NULL AUTO_INCREMENT")
            primary_key = MYSQL_INTERNAL_ID
        else:
            columns.append(f"{self.mysql_identifier(MYSQL_INTERNAL_ID)} BIGINT NOT NULL AUTO_INCREMENT")
            primary_key = MYSQL_INTERNAL_ID

        for column in frame.columns:
            if column == MYSQL_INTERNAL_ID:
                continue
            columns.append(f"{self.mysql_identifier(column)} {self.mysql_column_type(frame[column])} NULL")
        columns.append(f"PRIMARY KEY ({self.mysql_identifier(primary_key)})")

        conn = self.mysql_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE IF NOT EXISTS {table_sql} ({', '.join(columns)}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        finally:
            conn.close()

    def drop_mysql_table(self, table_name: str) -> None:
        conn = self.mysql_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.mysql_identifier(self.table(table_name))}")
        finally:
            conn.close()

    def existing_columns(self, table_name: str) -> set[str]:
        self.require_mysql()
        if not self.table_exists(table_name):
            return set()
        conn = self.mysql_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s",
                    (self.settings.mysql_database, self.table(table_name)),
                )
                return {row["column_name"] for row in cur.fetchall()}
        finally:
            conn.close()

    def ensure_table_columns(self, table_name: str, frame: pd.DataFrame) -> None:
        self.require_mysql()
        if not self.table_exists(table_name):
            return
        existing = self.existing_columns(table_name)
        missing = [column for column in frame.columns if column not in existing and column != MYSQL_INTERNAL_ID]
        if not missing:
            return
        conn = self.mysql_conn()
        try:
            with conn.cursor() as cur:
                for column in missing:
                    cur.execute(
                        f"ALTER TABLE {self.mysql_identifier(self.table(table_name))} "
                        f"ADD COLUMN {self.mysql_identifier(column)} {self.mysql_column_type(frame[column])} NULL"
                    )
        finally:
            conn.close()

    def query_all(self, sql: str, params: tuple = ()) -> list[dict]:
        self.ensure()
        conn = self.mysql_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(self.physical_sql(sql), params)
                return list(cur.fetchall())
        finally:
            conn.close()

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query_all(sql, params)
        return rows[0] if rows else None

    def read_table(self, table_name: str, limit: int | None = None, ensure_db: bool = True) -> pd.DataFrame:
        if ensure_db:
            self.ensure()
        self.require_mysql()
        sql = f"SELECT * FROM {table_name}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return pd.read_sql_query(self.physical_sql(sql), self.mysql_engine())

    def write_table(self, table_name: str, frame: pd.DataFrame, if_exists: str = "replace") -> None:
        self.require_mysql()
        if if_exists == "replace":
            self.drop_mysql_table(table_name)
            self.create_mysql_table_for_frame(table_name, frame)
            frame.to_sql(self.table(table_name), self.mysql_engine(), if_exists="append", index=False)
        elif if_exists == "append":
            if not self.table_exists(table_name):
                self.create_mysql_table_for_frame(table_name, frame)
            else:
                self.ensure_table_columns(table_name, frame)
            frame.to_sql(self.table(table_name), self.mysql_engine(), if_exists="append", index=False)
        else:
            frame.to_sql(self.table(table_name), self.mysql_engine(), if_exists=if_exists, index=False)

    def table_exists(self, table_name: str) -> bool:
        self.require_mysql()
        from sqlalchemy import text

        with self.mysql_engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT COUNT(*) AS count FROM information_schema.tables "
                    "WHERE table_schema=:schema AND table_name=:table"
                ),
                {"schema": self.settings.mysql_database, "table": self.table(table_name)},
            ).fetchone()
            return bool(row and row[0])

    def ensure_operational_tables(self) -> None:
        for table_name, columns in OPERATIONAL_TABLES.items():
            if not self.table_exists(table_name):
                self.write_table(table_name, pd.DataFrame(columns=columns), if_exists="replace")

    def ensure(self) -> None:
        if self._ensured:
            return
        self.require_mysql()
        try:
            if not self.table_exists("features"):
                self.initialize_from_extracted_csv()
            self.ensure_operational_tables()
            self.apply_app_migrations()
            self._mysql_available = True
            self._ensured = True
        except Exception as exc:
            self.last_mysql_error = str(exc)
            self._mysql_available = False
            raise

    def initialize_from_extracted_csv(self) -> None:
        required = {
            "polling_stations": EXTRACTED_DIR / "polling_stations.csv",
            "ward_registration": EXTRACTED_DIR / "ward_registration.csv",
            "historical_winners": EXTRACTED_DIR / "historical_winners.csv",
            "demographics": EXTRACTED_DIR / "demographics.csv",
            "candidate_profiles": EXTRACTED_DIR / "candidate_profiles.csv",
            "polling_results": EXTRACTED_DIR / "polling_results_placeholder.csv",
            "sentiment_data": EXTRACTED_DIR / "sentiment_data_placeholder.csv",
            "data_sources": EXTRACTED_DIR / "data_sources.csv",
        }
        missing = [str(path) for path in required.values() if not path.exists()]
        if missing:
            raise RuntimeError(f"Missing extracted CSV snapshots: {missing}")

        tables = {name: pd.read_csv(path) for name, path in required.items()}
        tables["features"] = compute_features(tables["polling_stations"], tables["polling_results"])
        tables["results"] = tables["polling_results"].rename(columns={"party": "candidate_party"})
        for table_name, frame in tables.items():
            self.write_table(table_name, frame, if_exists="replace")

    def recompute_features(self) -> None:
        stations = self.read_table("polling_stations", ensure_db=False)
        results = self.read_table("polling_results", ensure_db=False)
        features = compute_features(stations, results)
        self.write_table("features", features, if_exists="replace")

    def apply_app_migrations(self) -> None:
        self.migrate_candidate_profiles()
        self.migrate_ward_registration()
        self.migrate_sentiment_data()
        if self.table_exists("polling_stations") and self.table_exists("polling_results"):
            self.recompute_features()

    def migrate_ward_registration(self) -> None:
        snapshot = EXTRACTED_DIR / "ward_registration.csv"
        if snapshot.exists():
            frame = pd.read_csv(snapshot)
            self.write_table("ward_registration", frame, if_exists="replace")

    def migrate_candidate_profiles(self) -> None:
        if not self.table_exists("candidate_profiles"):
            return
        frame = self.read_table("candidate_profiles", ensure_db=False)
        columns = {
            "candidate_id": "",
            "candidate_name": "",
            "party": "",
            "election_year": 2027,
            "status": "",
            "profile_summary": "",
            "known_strongholds": "",
            "known_weaknesses": "",
            "source_type": "",
            "is_placeholder": 0,
        }
        for column, default in columns.items():
            if column not in frame.columns:
                frame[column] = default

        name = "Hon. Silverster Ogina"
        mask = frame["candidate_name"].astype(str).str.strip().eq(name)
        silverster_record = {
            "candidate_id": "REAL_2027_SILVERSTER_OGINA",
            "candidate_name": name,
            "party": "NEDP",
            "election_year": 2027,
            "status": "Incoming MCA - campaign principal",
            "profile_summary": "Incoming MCA campaign principal for Embakasi Ward.",
            "known_strongholds": "Embakasi Ward campaign network, resident engagement, field intelligence.",
            "known_weaknesses": "Requires continued station-level mobilization and fresh resident feedback.",
            "source_type": "campaign_record",
            "is_placeholder": 0,
        }
        if mask.any():
            for column, value in silverster_record.items():
                frame.loc[mask, column] = value
        else:
            frame = pd.concat([frame, pd.DataFrame([silverster_record])], ignore_index=True)
        election_year = pd.to_numeric(frame["election_year"], errors="coerce").fillna(0).astype(int)
        frame = frame[(election_year != 2027) | frame["candidate_name"].astype(str).str.strip().eq(name)]
        self.write_table("candidate_profiles", frame, if_exists="replace")

    def migrate_sentiment_data(self) -> None:
        if not self.table_exists("sentiment_data"):
            return
        frame = self.read_table("sentiment_data", ensure_db=False)
        if "candidate_name" not in frame.columns:
            return
        for column, default in {"source_type": "campaign_record", "is_placeholder": 0}.items():
            if column not in frame.columns:
                frame[column] = default
        mask = frame["candidate_name"].astype(str).str.strip().eq("Hon. Silverster Ogina")
        frame = frame[mask].copy()
        if not frame.empty:
            frame.loc[:, "source_type"] = "campaign_record"
            frame.loc[:, "is_placeholder"] = 0
        self.write_table("sentiment_data", frame, if_exists="replace")


DB = Database()
