#!/usr/bin/env python3
"""
Canonical data loader for the Embakasi Ward electoral intelligence demo.

The rule for this loader is simple:
- Parse available official/local source files for known real data.
- Generate placeholders only for datasets that are explicitly missing.
- Mark every row with source_type/source_file so the UI can distinguish
  official data from demo placeholders.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "processed" / "electoral.db"
EXTRACTED_DIR = BASE_DIR / "data" / "extracted_real"

IEBC_RESULTS_DIR = BASE_DIR / "IEBCHistoricalResults"
VOTER_DIR = BASE_DIR / "VoterRegistrationData"
DEMO_DIR = BASE_DIR / "DemographicsData"


@dataclass(frozen=True)
class Source:
    file: Path
    source_type: str
    description: str


SOURCES = {
    "winner_2013": Source(
        IEBC_RESULTS_DIR / "2013-winner_data.pdf",
        "official_pdf",
        "IEBC/Kenya Gazette 2013 elected MCA result and candidate table",
    ),
    "winner_2017": Source(
        IEBC_RESULTS_DIR / "2017-winner_data.pdf",
        "official_pdf",
        "IEBC/Kenya Gazette 2017 elected MCA result",
    ),
    "winner_2022": Source(
        IEBC_RESULTS_DIR / "2022-winner_data.pdf",
        "official_pdf",
        "IEBC/Kenya Gazette 2022 elected MCA result",
    ),
    "rov_caw": Source(
        VOTER_DIR / "rov_per_caw.pdf",
        "official_pdf",
        "IEBC registered voters per county assembly ward, 2022",
    ),
    "rov_polling": Source(
        VOTER_DIR / "rov_per_polling_station.pdf",
        "official_pdf",
        "IEBC registered voters per polling station, 2022",
    ),
    "rov_md": Source(
        VOTER_DIR / "poilling Stations.md",
        "third_party_markdown",
        "Kenyayote/IEBC copied polling-station table, retained for cross-check",
    ),
    "knbs_xlsx": Source(
        DEMO_DIR / "2019-Kenya-population-and-Housing-Census-Population-households-density-by-sub-county.xlsx",
        "official_xlsx",
        "KNBS 2019 census population, households, density by sub-county",
    ),
    "knbs_pdf": Source(
        DEMO_DIR / "2019-Kenya-population-and-Housing-Census-Volume-2-Distribution-of-Population-by-Administrative-Units.pdf",
        "official_pdf",
        "KNBS 2019 census Volume II administrative-unit distribution",
    ),
}


def _pdf_text(path: Path, page_indexes: list[int] | None = None) -> str:
    import fitz

    doc = fitz.open(str(path))
    pages = page_indexes if page_indexes is not None else range(doc.page_count)
    return "\n".join(doc[i].get_text("text") for i in pages if 0 <= i < doc.page_count)


def _clean_int(value: str | int | float) -> int:
    return int(str(value).replace(",", "").strip())


def extract_polling_stations() -> pd.DataFrame:
    """Extract all 1423 Embakasi Ward polling-station rows from the IEBC PDF."""
    source = SOURCES["rov_polling"]
    # The Embakasi Ward rows span PDF pages 691-693 (0-based 690-692).
    text = _pdf_text(source.file, [690, 691, 692])
    pattern = re.compile(
        r"047 NAIROBI CITY\s+"
        r"285 EMBAKASI EAST\s+"
        r"1423 EMBAKASI\s+"
        r"(?P<reg_centre_code>\d{3}) (?P<reg_centre_name>[^\n]+)\s+"
        r"(?P<polling_station_id>0472851423\d{5}) (?P<polling_station_name>[^\n]+)\s+"
        r"(?P<registered_voters_2022>[0-9,]+)",
        re.MULTILINE,
    )
    rows = []
    for match in pattern.finditer(text):
        row = match.groupdict()
        row["registered_voters_2022"] = _clean_int(row["registered_voters_2022"])
        row.update(
            {
                "county_code": "047",
                "county": "Nairobi City",
                "constituency_code": "285",
                "constituency": "Embakasi East",
                "ward_code": "1423",
                "ward": "Embakasi",
                "source_type": source.source_type,
                "source_file": str(source.file.relative_to(BASE_DIR)),
                "source_note": source.description,
            }
        )
        rows.append(row)

    df = pd.DataFrame(rows)
    if len(df) != 70:
        raise ValueError(f"Expected 70 Embakasi Ward polling stations from IEBC PDF, got {len(df)}")
    if int(df["registered_voters_2022"].sum()) != 46291:
        raise ValueError("Polling-station voter total does not match IEBC PDF extraction expectation of 46,291")
    return df


def extract_ward_registration() -> pd.DataFrame:
    """Extract the Embakasi Ward registration row from the IEBC CAW PDF."""
    source = SOURCES["rov_caw"]
    text = _pdf_text(source.file, [44])
    pattern = re.compile(
        r"047\s+NAIROBI CITY\s+\|\s+285\s+EMBAKASI EAST\s+\|\s+"
        r"(?P<ward_code>142[1-5])\s+(?P<ward>[A-Z /]+?)\s+\|\s+"
        r"(?P<registered_voters_2022>[0-9,]+)"
    )
    rows = []
    for match in pattern.finditer(text.replace("\n", " | ")):
        row = match.groupdict()
        row["registered_voters_2022"] = _clean_int(row["registered_voters_2022"])
        row.update(
            {
                "county_code": "047",
                "county": "Nairobi City",
                "constituency_code": "285",
                "constituency": "Embakasi East",
                "source_type": source.source_type,
                "source_file": str(source.file.relative_to(BASE_DIR)),
                "source_note": source.description,
            }
        )
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df[df["ward_code"].astype(str) == "1423"].copy()
    if len(df) != 1:
        raise ValueError(f"Expected 1 Embakasi Ward registration row, got {len(df)}")
    return df


def extract_historical_winners() -> pd.DataFrame:
    """Extract real Embakasi Ward MCA winners for 2013, 2017, and 2022."""
    rows = [
        {
            "year": 2013,
            "county_code": "047",
            "county": "Nairobi City",
            "constituency_code": "285",
            "constituency": "Embakasi East",
            "ward_code": "1423",
            "ward": "Embakasi",
            "candidate_name": "Michael Ogada Okumu",
            "party": "Orange Democratic Movement",
            "party_abbrev": "ODM",
            "votes": 11625,
            "source_type": SOURCES["winner_2013"].source_type,
            "source_file": str(SOURCES["winner_2013"].file.relative_to(BASE_DIR)),
            "source_page": 755,
            "source_note": "Extracted from 2013 Embakasi Ward candidate table; winner row.",
        },
        {
            "year": 2017,
            "county_code": "047",
            "county": "Nairobi City",
            "constituency_code": "285",
            "constituency": "Embakasi East",
            "ward_code": "1423",
            "ward": "Embakasi",
            "candidate_name": "Michael Ogada Okumu",
            "party": "Orange Democratic Movement",
            "party_abbrev": "ODM",
            "votes": 12359,
            "source_type": SOURCES["winner_2017"].source_type,
            "source_file": str(SOURCES["winner_2017"].file.relative_to(BASE_DIR)),
            "source_page": 43,
            "source_note": "Extracted from 2017 elected MCA winners table.",
        },
        {
            "year": 2022,
            "county_code": "047",
            "county": "Nairobi City",
            "constituency_code": "285",
            "constituency": "Embakasi East",
            "ward_code": "1423",
            "ward": "Embakasi",
            "candidate_name": "Nyantika Ricardo Billy",
            "party": "Orange Democratic Movement",
            "party_abbrev": "ODM",
            "votes": 12877,
            "source_type": SOURCES["winner_2022"].source_type,
            "source_file": str(SOURCES["winner_2022"].file.relative_to(BASE_DIR)),
            "source_page": 39,
            "source_note": "Extracted from 2022 elected MCA winners table.",
        },
    ]
    _validate_winner_rows_against_pdf(rows)
    return pd.DataFrame(rows)


def _validate_winner_rows_against_pdf(rows: list[dict]) -> None:
    """Guard against accidental hand-entry drift by checking exact PDF text windows."""
    checks = {
        2013: (SOURCES["winner_2013"].file, [754], ["MICHAEL OGADA OKUMU", "11,625"]),
        2017: (SOURCES["winner_2017"].file, [42], ["Okumu", "Michael Ogada", "12,359"]),
        2022: (SOURCES["winner_2022"].file, [38], ["Billy", "Nyantika Ricardo", "12,877"]),
    }
    for row in rows:
        path, pages, tokens = checks[row["year"]]
        page_text = _pdf_text(path, pages).upper()
        missing = [token for token in tokens if token.upper() not in page_text]
        if missing:
            raise ValueError(f"Could not validate {row['year']} winner against PDF tokens: {missing}")


def extract_demographics() -> pd.DataFrame:
    """Extract KNBS 2019 census rows relevant to Embakasi administrative units."""
    subcounty_source = SOURCES["knbs_xlsx"]
    xlsx = pd.read_excel(subcounty_source.file, sheet_name="Pop by Sex and County", header=None)
    embakasi_row = xlsx[xlsx[0].astype(str).str.strip().str.upper().eq("EMBAKASI")].iloc[0]
    rows = [
        {
            "unit_name": "EMBAKASI",
            "unit_level": "sub_county",
            "total_population_2019": int(embakasi_row[1]),
            "male_population": int(embakasi_row[2]),
            "female_population": int(embakasi_row[3]),
            "households": int(embakasi_row[4]),
            "conventional_households": int(embakasi_row[5]),
            "group_quarters_population": int(embakasi_row[6]),
            "land_area_sq_km": float(embakasi_row[7]),
            "population_density": float(embakasi_row[8]),
            "mapping_scope": "Embakasi sub-county; broader than Embakasi Ward",
            "source_type": subcounty_source.source_type,
            "source_file": str(subcounty_source.file.relative_to(BASE_DIR)),
            "source_page": "",
            "source_note": subcounty_source.description,
        }
    ]

    pdf_source = SOURCES["knbs_pdf"]
    page_text = _pdf_text(pdf_source.file, [246])
    # These are the administrative-unit rows visible under the Embakasi branch
    # in Volume II. They are retained as census geography; the app does not
    # pretend they are exact polling-station boundaries.
    unit_rows = [
        ("EMBAKASI", "division/location", 539856, 279572, 260241, 206380, 204898, 1482, 73.6, 7333),
        ("EMBAKASI", "location", 178387, 87403, 90972, 62143, 60694, 1449, 66.9, 2665),
        ("EMBAKASI", "sub_location", 69887, 33953, 35926, 23944, 22589, 1355, 60.5, 1155),
        ("TASSIA", "sub_location", 88874, 43859, 45011, 30948, 30948, 0, 2.5, 35196),
        ("UTAWALA", "sub_location", 19626, 9591, 10035, 7251, 7157, 94, 3.9, 5073),
    ]
    for item in unit_rows:
        name, level, total, male, female, households, conventional, group_q, area, density = item
        for token in [name, f"{total:,}", f"{male:,}", f"{female:,}"]:
            if token not in page_text:
                raise ValueError(f"Could not validate KNBS PDF row token {token!r}")
        rows.append(
            {
                "unit_name": name,
                "unit_level": level,
                "total_population_2019": total,
                "male_population": male,
                "female_population": female,
                "households": households,
                "conventional_households": conventional,
                "group_quarters_population": group_q,
                "land_area_sq_km": area,
                "population_density": density,
                "mapping_scope": "KNBS administrative unit near/within Embakasi area; not exact IEBC polling boundary",
                "source_type": pdf_source.source_type,
                "source_file": str(pdf_source.file.relative_to(BASE_DIR)),
                "source_page": "247",
                "source_note": pdf_source.description,
            }
        )
    return pd.DataFrame(rows)


def generate_placeholder_candidate_profiles(winners: pd.DataFrame) -> pd.DataFrame:
    real = []
    for _, row in winners.iterrows():
        real.append(
            {
                "candidate_id": f"REAL_{row['year']}_{re.sub(r'[^A-Z0-9]+', '_', row['candidate_name'].upper()).strip('_')}",
                "candidate_name": row["candidate_name"],
                "party": row["party_abbrev"],
                "election_year": int(row["year"]),
                "status": "Historical winner",
                "incumbent": 1 if int(row["year"]) == 2022 else 0,
                "profile_summary": row["source_note"],
                "stronghold_notes": "Needs real polling-station results to determine strongholds.",
                "weakness_notes": "Needs real polling-station results to determine weaknesses.",
                "source_type": row["source_type"],
                "source_file": row["source_file"],
                "is_placeholder": 0,
            }
        )

    demo_candidates = [
        ("REAL_2027_SILVERSTER_OGINA", "Hon. Silverster Ogina", "NEDP", "Incoming MCA - campaign principal"),
        ("DEMO_2027_001", "Candidate Amina Mwende", "ODM", "Potential challenger"),
        ("DEMO_2027_002", "Candidate James Kariuki", "UDA", "Potential challenger"),
        ("DEMO_2027_003", "Candidate Peter Otieno", "Independent", "Community aspirant"),
        ("DEMO_2027_004", "Candidate Grace Wambui", "Jubilee", "Potential challenger"),
    ]
    for cid, name, party, status in demo_candidates:
        is_principal = name == "Hon. Silverster Ogina"
        real.append(
            {
                "candidate_id": cid,
                "candidate_name": name,
                "party": party,
                "election_year": 2027,
                "status": status,
                "incumbent": 0,
                "profile_summary": "Incoming MCA campaign principal for Embakasi Ward." if is_principal else "Placeholder candidate intelligence for demo until real aspirant data is collected.",
                "stronghold_notes": "Campaign principal; update with verified strongholds through admin panel." if is_principal else "Demo only; replace through admin panel.",
                "weakness_notes": "Update with verified opposition risk notes through admin panel." if is_principal else "Demo only; replace through admin panel.",
                "source_type": "campaign_record" if is_principal else "demo_placeholder",
                "source_file": "",
                "is_placeholder": 0 if is_principal else 1,
            }
        )
    return pd.DataFrame(real).drop_duplicates(subset=["candidate_id"])


def generate_placeholder_polling_results(stations: pd.DataFrame, winners: pd.DataFrame) -> pd.DataFrame:
    """Generate station-level results because real IEBC station/candidate results are missing."""
    rng = np.random.default_rng(20270521)
    candidates = [
        ("Nyantika Ricardo Billy", "ODM", 12877, 0.43),
        ("Candidate James Kariuki", "UDA", 10350, 0.34),
        ("Candidate Amina Mwende", "ODM", 4550, 0.15),
        ("Candidate Peter Otieno", "Independent", 1700, 0.06),
        ("Candidate Grace Wambui", "Jubilee", 650, 0.02),
    ]
    total_real_winner_votes = int(winners[winners["year"] == 2022].iloc[0]["votes"])
    rows = []
    station_weights = stations["registered_voters_2022"] / stations["registered_voters_2022"].sum()
    allocated_winner = np.floor(station_weights * total_real_winner_votes).astype(int).to_numpy()
    allocated_winner[-1] += total_real_winner_votes - int(allocated_winner.sum())

    for idx, (_, station) in enumerate(stations.iterrows()):
        registered = int(station["registered_voters_2022"])
        turnout_rate = float(rng.uniform(0.58, 0.72))
        projected_votes = max(int(registered * turnout_rate), allocated_winner[idx] + 20)
        remaining = max(projected_votes - allocated_winner[idx], 0)
        competitor_weights = rng.dirichlet([7.5, 3.0, 1.1, 0.5])
        competitor_votes = np.floor(competitor_weights * remaining).astype(int)
        competitor_votes[-1] += remaining - int(competitor_votes.sum())

        station_votes = [allocated_winner[idx], *competitor_votes.tolist()]
        for (candidate, party, _, _), votes in zip(candidates, station_votes):
            rows.append(
                {
                    "polling_station_id": station["polling_station_id"],
                    "polling_station_name": station["polling_station_name"],
                    "year": 2022,
                    "candidate_name": candidate,
                    "party": party,
                    "votes": int(max(votes, 0)),
                    "registered_voters": registered,
                    "turnout_rate": projected_votes / registered if registered else 0,
                    "source_type": "demo_placeholder",
                    "source_file": "",
                    "source_note": "Placeholder station-level candidate results. Replace with certified IEBC station results when obtained.",
                    "is_placeholder": 1,
                }
            )
    return pd.DataFrame(rows)


def generate_placeholder_sentiment(candidate_profiles: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(20270521)
    candidates = candidate_profiles[candidate_profiles["election_year"].isin([2022, 2027])]["candidate_name"].unique()
    themes = ["Water", "Roads", "Security", "Youth Jobs", "Waste Collection", "Health Services"]
    start = date.today() - timedelta(days=29)
    rows = []
    for day in range(30):
        current = start + timedelta(days=day)
        for candidate in candidates:
            mentions = int(rng.integers(20, 220))
            score = float(rng.uniform(-0.25, 0.72))
            positive = int(max(0, mentions * (0.35 + max(score, 0) * 0.25)))
            negative = int(max(0, mentions * (0.18 + max(-score, 0) * 0.25)))
            neutral = max(mentions - positive - negative, 0)
            rows.append(
                {
                    "date": current.isoformat(),
                    "candidate_name": candidate,
                    "total_mentions": mentions,
                    "positive_mentions": positive,
                    "neutral_mentions": neutral,
                    "negative_mentions": negative,
                    "primary_theme": str(rng.choice(themes)),
                    "sentiment_score": round(score, 3),
                    "source_type": "demo_placeholder",
                    "source_file": "",
                    "source_note": "Placeholder resident sentiment. Replace with coded field notes, public discussion, and resident interaction data.",
                    "is_placeholder": 1,
                }
            )
    return pd.DataFrame(rows)


def compute_features(stations: pd.DataFrame, polling_results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for station_id, group in polling_results[polling_results["year"] == 2022].groupby("polling_station_id"):
        station = stations[stations["polling_station_id"] == station_id].iloc[0]
        sorted_group = group.sort_values("votes", ascending=False)
        first = sorted_group.iloc[0]
        second = sorted_group.iloc[1] if len(sorted_group) > 1 else sorted_group.iloc[0]
        total_votes = int(sorted_group["votes"].sum())
        registered = int(station["registered_voters_2022"])
        margin = int(first["votes"] - second["votes"])
        margin_pct = margin / total_votes if total_votes else 0
        turnout_rate = total_votes / registered if registered else 0
        untapped = max(registered - total_votes, 0)

        if margin_pct <= 0.05:
            competitiveness = "Highly Contested"
        elif margin_pct <= 0.10:
            competitiveness = "Battleground"
        elif margin_pct <= 0.20:
            competitiveness = "Leaning"
        else:
            competitiveness = "Safe"

        voter_size = registered / stations["registered_voters_2022"].max()
        turnout_gap = 1 - min(turnout_rate, 1)
        margin_gap = 1 - min(margin_pct, 1)
        mobilization_score = int(round((0.38 * voter_size + 0.37 * turnout_gap + 0.25 * margin_gap) * 100))
        if mobilization_score >= 75:
            tier = "Critical Priority"
        elif mobilization_score >= 62:
            tier = "High Priority"
        elif mobilization_score >= 40:
            tier = "Medium Priority"
        elif mobilization_score >= 20:
            tier = "Low Priority"
        else:
            tier = "Maintain Support"

        rows.append(
            {
                "polling_station_id": station_id,
                "polling_station_name": station["polling_station_name"],
                "sub_location": station["reg_centre_name"],
                "votes_2022_total": total_votes,
                "registered_voters_2022": registered,
                "turnout_rate_2022": turnout_rate,
                "winner_2022": first["candidate_name"],
                "winner_party_2022": first["party"],
                "votes_1st_2022": int(first["votes"]),
                "votes_2nd_2022": int(second["votes"]),
                "win_margin_2022": margin,
                "win_margin_pct_2022": margin_pct,
                "competitiveness_2022": competitiveness,
                "mobilization_tier": tier,
                "mobilization_score": mobilization_score,
                "untapped_voters": untapped,
                "result_source_type": str(first.get("source_type", "unknown")),
            }
        )
    return pd.DataFrame(rows).sort_values("mobilization_score", ascending=False)


def initialize_all() -> None:
    """Build the canonical SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    stations = extract_polling_stations()
    ward_registration = extract_ward_registration()
    winners = extract_historical_winners()
    demographics = extract_demographics()
    candidate_profiles = generate_placeholder_candidate_profiles(winners)
    polling_results = generate_placeholder_polling_results(stations, winners)
    sentiment = generate_placeholder_sentiment(candidate_profiles)
    features = compute_features(stations, polling_results)

    sources = pd.DataFrame(
        [
            {
                "source_key": key,
                "source_file": str(src.file.relative_to(BASE_DIR)),
                "source_type": src.source_type,
                "description": src.description,
            }
            for key, src in SOURCES.items()
        ]
        + [
            {
                "source_key": "placeholder_polling_results",
                "source_file": "",
                "source_type": "demo_placeholder",
                "description": "Generated because certified IEBC polling station + candidate results are missing.",
            },
            {
                "source_key": "placeholder_candidate_intelligence",
                "source_file": "",
                "source_type": "demo_placeholder",
                "description": "Generated because 2027 aspirant/candidate intelligence is not yet collected.",
            },
            {
                "source_key": "placeholder_social_sentiment",
                "source_file": "",
                "source_type": "demo_placeholder",
                "description": "Generated because real resident sentiment and issue-signal data is not yet collected.",
            },
        ]
    )

    extracted_tables = {
        "polling_stations.csv": stations,
        "ward_registration.csv": ward_registration,
        "historical_winners.csv": winners,
        "demographics.csv": demographics,
        "candidate_profiles.csv": candidate_profiles,
        "polling_results_placeholder.csv": polling_results,
        "sentiment_data_placeholder.csv": sentiment,
        "features.csv": features,
        "data_sources.csv": sources,
    }
    for filename, frame in extracted_tables.items():
        frame.to_csv(EXTRACTED_DIR / filename, index=False)

    with sqlite3.connect(DB_PATH) as conn:
        stations.to_sql("polling_stations", conn, if_exists="replace", index=False)
        ward_registration.to_sql("ward_registration", conn, if_exists="replace", index=False)
        winners.to_sql("historical_winners", conn, if_exists="replace", index=False)
        demographics.to_sql("demographics", conn, if_exists="replace", index=False)
        candidate_profiles.to_sql("candidate_profiles", conn, if_exists="replace", index=False)
        polling_results.to_sql("polling_results", conn, if_exists="replace", index=False)
        # Keep a compatibility alias for the older package app.
        polling_results.rename(columns={"party": "candidate_party"}).to_sql("results", conn, if_exists="replace", index=False)
        sentiment.to_sql("sentiment_data", conn, if_exists="replace", index=False)
        features.to_sql("features", conn, if_exists="replace", index=False)
        sources.to_sql("data_sources", conn, if_exists="replace", index=False)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_polling_results_station ON polling_results(polling_station_id);
            CREATE INDEX IF NOT EXISTS idx_polling_results_year ON polling_results(year);
            CREATE INDEX IF NOT EXISTS idx_features_tier ON features(mobilization_tier);
            CREATE INDEX IF NOT EXISTS idx_features_comp ON features(competitiveness_2022);
            """
        )

    print(f"Canonical database built: {DB_PATH}")
    print(f"Real polling stations: {len(stations)}; registered voters: {stations['registered_voters_2022'].sum():,}")
    print(f"Historical winners: {len(winners)}; demographics rows: {len(demographics)}")
    print("Placeholder tables: polling_results, candidate_profiles for 2027, sentiment_data")


def initialize_from_extracted_csv() -> bool:
    """Rebuild SQLite from stored one-time extraction CSVs without reading PDFs."""
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
    if not all(path.exists() for path in required.values()):
        return False

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tables = {name: pd.read_csv(path) for name, path in required.items()}
    features = compute_features(tables["polling_stations"], tables["polling_results"])
    tables["features"] = features

    with sqlite3.connect(DB_PATH) as conn:
        for table_name, frame in tables.items():
            frame.to_sql(table_name, conn, if_exists="replace", index=False)
        tables["polling_results"].rename(columns={"party": "candidate_party"}).to_sql(
            "results", conn, if_exists="replace", index=False
        )
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_polling_results_station ON polling_results(polling_station_id);
            CREATE INDEX IF NOT EXISTS idx_polling_results_year ON polling_results(year);
            CREATE INDEX IF NOT EXISTS idx_features_tier ON features(mobilization_tier);
            CREATE INDEX IF NOT EXISTS idx_features_comp ON features(competitiveness_2022);
            """
        )
    return True


if __name__ == "__main__":
    initialize_all()
