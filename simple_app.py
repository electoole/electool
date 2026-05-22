#!/usr/bin/env python3
"""Electoral Intelligence Flask app for the Embakasi Ward MVP."""
from __future__ import annotations

import os
import secrets

from flask import Flask, jsonify, make_response, render_template, request

from app_cache import response_cache
from database import DB
from ai_assistant import ai_status, answer_question
from electoral_intelligence.backend.admin_panel import admin_bp

try:
    from flask_compress import Compress
except ImportError:  # pragma: no cover - dependency is installed in production
    Compress = None


app = Flask(
    __name__,
    template_folder="electoral_intelligence/frontend/templates",
    static_folder="electoral_intelligence/frontend/static",
)
app.secret_key = os.getenv("SECRET_KEY") or os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024,
    SEND_FILE_MAX_AGE_DEFAULT=int(os.getenv("STATIC_CACHE_SECONDS", "3600")),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"},
    COMPRESS_MIMETYPES=[
        "text/html",
        "text/css",
        "text/xml",
        "application/json",
        "application/javascript",
        "text/javascript",
        "image/svg+xml",
    ],
    COMPRESS_LEVEL=int(os.getenv("COMPRESS_LEVEL", "6")),
    COMPRESS_MIN_SIZE=int(os.getenv("COMPRESS_MIN_SIZE", "500")),
)
if Compress:
    Compress(app)
app.register_blueprint(admin_bp)

def ensure_database() -> None:
    DB.ensure()


def cacheable_request() -> bool:
    if request.method != "GET":
        return False
    if request.path.startswith("/admin") or request.path.startswith("/static"):
        return False
    if request.path in {"/api/assistant", "/api/system/database", "/api/system/ai"}:
        return False
    return request.path == "/" or request.path.startswith("/api/")


def cache_ttl_for_path(path: str) -> int:
    if path == "/":
        return 30
    if path.startswith("/api/polling-stations") or path.startswith("/api/battlegrounds"):
        return 90
    if path.startswith("/api/mca/") or path.startswith("/api/mobilization"):
        return 60
    return 45


def cache_key() -> str:
    return f"{request.method}:{request.full_path}"


@app.before_request
def serve_cached_response():
    if not cacheable_request():
        return None
    entry = response_cache.get(cache_key())
    if not entry:
        return None
    response = make_response(entry.body, entry.status_code)
    for header, value in entry.headers.items():
        response.headers[header] = value
    response.headers["X-Cache"] = "HIT"
    return response


@app.after_request
def finalize_response(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    if cacheable_request() and response.status_code == 200 and response.direct_passthrough is False:
        response.headers.setdefault("Cache-Control", "private, max-age=0, must-revalidate")
        response.headers["X-Cache"] = response.headers.get("X-Cache", "MISS")
        headers = {
            "Content-Type": response.headers.get("Content-Type", ""),
            "Cache-Control": response.headers.get("Cache-Control", ""),
        }
        response_cache.set(
            cache_key(),
            response.status_code,
            headers,
            response.get_data(),
            ttl=cache_ttl_for_path(request.path),
        )
    elif request.path.startswith("/admin") or request.path.startswith("/api/system") or request.path == "/api/assistant":
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def rows(query: str, args: tuple = ()) -> list[dict]:
    return DB.query_all(query, args)


def one(query: str, args: tuple = ()) -> dict | None:
    data = rows(query, args)
    return data[0] if data else None


ISSUE_RESPONSE_GUIDE = {
    "Water Supply": {
        "hotspots": ["Tassia", "Embakasi Village", "Nyayo Estate edge"],
        "signal": "Residents are complaining about unreliable supply, high water costs, and delayed response to outages.",
        "response": "You should push a ward-level water interruption register, publish follow-up dates, and escalate chronic estates to Nairobi Water with written evidence.",
        "field_action": "Send agents to log outage days, affected plots, vendor prices, and landlord complaints before your next estate meeting.",
        "message": "Your message should be practical: every outage reported by residents must become a tracked case with a follow-up date.",
    },
    "Roads & Drainage": {
        "hotspots": ["Tassia lanes", "Embakasi feeder roads", "Savannah access roads"],
        "signal": "Voters connect poor drainage, potholes, and muddy access roads with daily transport costs and flood disruption.",
        "response": "You should rank blocked drains and impassable access points, then turn them into a public works follow-up list with photos.",
        "field_action": "Collect photos after rainfall, mark exact road sections, and separate county-maintained roads from estate access lanes.",
        "message": "Your message should be that roads and drainage will be handled by named locations, not general promises.",
    },
    "Security": {
        "hotspots": ["Tassia", "Embakasi Village", "Pipeline border points"],
        "signal": "Security concern rises around dark footpaths, robbery spots, and weak coordination with nyumba kumi structures.",
        "response": "You should organize lighting and patrol-demand petitions by hotspot and use resident reports to pressure the county and police command.",
        "field_action": "Map dark sections, robbery times, and repeat spots; ask agents to verify each claim with local shopkeepers and residents.",
        "message": "Your message should focus on lighting, reporting channels, and accountable follow-up with security actors.",
    },
    "Youth Jobs": {
        "hotspots": ["Tassia", "Embakasi", "Savannah"],
        "signal": "Youth conversations are focused on casual work access, training, sports, small grants, and fair opportunity distribution.",
        "response": "You should build a youth opportunity register and use it to announce training, kazi mtaani-style work, and business support fairly.",
        "field_action": "Capture names, skills, age bracket, and preferred work categories through ward agents or Google Forms.",
        "message": "Your message should show a system for opportunity access, not one-off handouts.",
    },
    "Waste Collection": {
        "hotspots": ["Markets", "estate collection points", "high-density plots"],
        "signal": "Garbage complaints point to irregular collection, illegal dumping, smell, blocked drains, and health concerns.",
        "response": "You should identify dumping points, pressure collection coordination, and promote estate-level reporting for missed pickups.",
        "field_action": "Record dumping points with photos, collection frequency, and the responsible operator where residents know it.",
        "message": "Your message should link cleanliness to health, drainage, and dignity in the estate.",
    },
    "Bursaries & School Fees": {
        "hotspots": ["Primary school catchments", "church groups", "low-income plots"],
        "signal": "Parents want transparent bursary access, clear deadlines, and proof that needy students are prioritized.",
        "response": "You should publish a bursary calendar, eligibility checklist, and ward help desk days before applications close.",
        "field_action": "Ask agents to collect school-fee pressure cases and identify orphaned, disabled, or very needy learners for follow-up.",
        "message": "Your message should be transparent: every applicant should know the process, deadline, and required documents.",
    },
    "Health Services": {
        "hotspots": ["Clinic catchments", "maternal health groups", "elderly households"],
        "signal": "Residents mention medicine stock-outs, distance to care, maternal services, and affordability of basic treatment.",
        "response": "You should track medicine complaints and clinic access gaps, then escalate them as documented ward health needs.",
        "field_action": "Collect facility names, medicine gaps, date of visit, and affected patient category without exposing private medical details.",
        "message": "Your message should promise documented escalation and follow-up, not medical claims you cannot directly control.",
    },
    "Business Permits": {
        "hotspots": ["Kiosks", "markets", "boda stages", "small traders"],
        "signal": "Small traders are worried about permit costs, harassment, unclear county fees, and market sanitation.",
        "response": "You should create a trader issue desk and separate legitimate fee concerns from harassment and enforcement complaints.",
        "field_action": "Capture business type, permit issue, officer interaction details, and market location for structured escalation.",
        "message": "Your message should defend fair treatment for small traders while keeping compliance clear.",
    },
}


ISSUE_ALIASES = {
    "water": "Water Supply",
    "water supply": "Water Supply",
    "roads": "Roads & Drainage",
    "roads & drainage": "Roads & Drainage",
    "drainage": "Roads & Drainage",
    "security": "Security",
    "youth": "Youth Jobs",
    "youth jobs": "Youth Jobs",
    "waste": "Waste Collection",
    "waste collection": "Waste Collection",
    "garbage": "Waste Collection",
    "health": "Health Services",
    "health services": "Health Services",
    "bursary": "Bursaries & School Fees",
    "bursaries": "Bursaries & School Fees",
    "school fees": "Bursaries & School Fees",
    "business": "Business Permits",
    "business permits": "Business Permits",
}


@app.route("/")
def dashboard():
    summary = {
        "total_stations": 0,
        "avg_turnout": 0,
        "critical_priority": 0,
        "high_priority": 0,
        "ward_name": "Embakasi",
        "constituency": "Embakasi East",
        "county": "Nairobi County",
        "timestamp": "",
    }
    return render_template("dashboard.html", summary=summary)


@app.route("/api/statistics")
def api_statistics():
    stats = one(
        """
        SELECT
            COUNT(*) AS total_stations,
            SUM(registered_voters_2022) AS total_registered_voters,
            SUM(votes_2022_total) AS total_votes_2022,
            AVG(turnout_rate_2022) AS average_turnout,
            SUM(CASE WHEN competitiveness_2022 IN ('Highly Contested', 'Battleground') THEN 1 ELSE 0 END) AS battleground_stations,
            SUM(CASE WHEN mobilization_tier = 'Critical Priority' THEN 1 ELSE 0 END) AS critical_stations,
            SUM(untapped_voters) AS total_untapped_voters
        FROM features
        """
    ) or {}
    comp = rows("SELECT competitiveness_2022 AS label, COUNT(*) AS count FROM features GROUP BY competitiveness_2022")
    tiers = rows("SELECT mobilization_tier AS label, COUNT(*) AS count FROM features GROUP BY mobilization_tier")
    return jsonify(
        {
            "success": True,
            "data": {
                "total_stations": stats.get("total_stations", 0),
                "total_registered_voters": stats.get("total_registered_voters", 0),
                "total_votes_2022": stats.get("total_votes_2022", 0),
                "average_turnout_pct": round((stats.get("average_turnout") or 0) * 100, 1),
                "battleground_stations": stats.get("battleground_stations", 0),
                "critical_stations": stats.get("critical_stations", 0),
                "total_untapped_voters": stats.get("total_untapped_voters", 0),
                "stations_by_competitiveness": {r["label"]: r["count"] for r in comp},
                "stations_by_mobilization": {r["label"]: r["count"] for r in tiers},
            },
        }
    )


@app.route("/api/polling-stations")
def api_polling_stations():
    data = rows("SELECT * FROM features ORDER BY mobilization_score DESC")
    return jsonify({"success": True, "data": data, "count": len(data)})


@app.route("/api/polling-stations/<station_id>")
def api_station_detail(station_id: str):
    station = one("SELECT * FROM features WHERE polling_station_id = ?", (station_id,))
    if not station:
        return jsonify({"success": False, "error": "Station not found"}), 404
    station["vote_breakdown"] = rows(
        """
        SELECT candidate_name, party, votes, year, source_type, is_placeholder
        FROM polling_results
        WHERE polling_station_id = ?
        ORDER BY votes DESC
        """,
        (station_id,),
    )
    return jsonify({"success": True, "data": station})


@app.route("/api/battlegrounds")
def api_battlegrounds():
    data = rows(
        """
        SELECT *
        FROM features
        WHERE competitiveness_2022 IN ('Highly Contested', 'Battleground')
        ORDER BY win_margin_pct_2022 ASC, mobilization_score DESC
        """
    )
    return jsonify({"success": True, "data": data, "count": len(data)})


@app.route("/api/mobilization-plan")
def api_mobilization_plan():
    critical = rows(
        "SELECT * FROM features WHERE mobilization_tier = 'Critical Priority' ORDER BY mobilization_score DESC LIMIT 15"
    )
    high = rows(
        "SELECT * FROM features WHERE mobilization_tier = 'High Priority' ORDER BY mobilization_score DESC LIMIT 15"
    )
    untapped = rows("SELECT * FROM features ORDER BY untapped_voters DESC LIMIT 15")
    return jsonify(
        {
            "success": True,
            "critical_priority": critical,
            "high_priority": high,
            "untapped_voters": untapped,
        }
    )


@app.route("/api/candidate-performance")
def api_candidate_performance():
    data = rows(
        """
        SELECT
            candidate_name,
            CASE WHEN candidate_name = 'Hon. Silverster Ogina' THEN 'NEDP' ELSE party END AS party,
            SUM(votes) AS total_votes,
            COUNT(DISTINCT polling_station_id) AS stations_competed,
            AVG(turnout_rate) AS avg_station_turnout,
            CASE WHEN candidate_name = 'Hon. Silverster Ogina' THEN 'campaign_record' ELSE MAX(source_type) END AS source_type,
            CASE WHEN candidate_name = 'Hon. Silverster Ogina' THEN 0 ELSE MAX(is_placeholder) END AS is_placeholder
        FROM polling_results
        WHERE year = 2022
        GROUP BY candidate_name, party
        ORDER BY total_votes DESC
        """
    )
    return jsonify({"success": True, "data": data})


@app.route("/api/candidates")
def api_candidates():
    data = rows(
        """
        SELECT
            cp.candidate_name,
            CASE WHEN cp.candidate_name = 'Hon. Silverster Ogina' THEN 'NEDP' ELSE cp.party END AS party,
            cp.status,
            CASE
                WHEN cp.candidate_name = 'Hon. Silverster Ogina'
                THEN 'Incoming MCA campaign principal for Embakasi Ward.'
                ELSE cp.profile_summary
            END AS profile_summary,
            CASE WHEN cp.candidate_name = 'Hon. Silverster Ogina' THEN 'campaign_record' ELSE cp.source_type END AS source_type,
            CASE WHEN cp.candidate_name = 'Hon. Silverster Ogina' THEN 0 ELSE cp.is_placeholder END AS is_placeholder,
            COALESCE(SUM(pr.votes), 0) AS total_votes
        FROM candidate_profiles cp
        LEFT JOIN polling_results pr ON cp.candidate_name = pr.candidate_name
        WHERE cp.candidate_name = 'Hon. Silverster Ogina'
        GROUP BY cp.candidate_id, cp.candidate_name, cp.party, cp.status, cp.profile_summary, cp.source_type, cp.is_placeholder
        ORDER BY cp.is_placeholder ASC, total_votes DESC
        """
    )
    total = sum(r["total_votes"] for r in data) or 1
    for row in data:
        row["vote_share_percent"] = round(row["total_votes"] / total * 100, 2)
    return jsonify({"success": True, "data": data, "count": len(data)})


@app.route("/api/candidates/performance-history")
def api_candidates_performance_history():
    historical = rows(
        """
        SELECT
            candidate_name,
            party_abbrev AS party,
            year AS election_year,
            votes,
            votes AS total_votes,
            'Historical winner' AS status,
            source_type,
            0 AS is_placeholder
        FROM historical_winners
        ORDER BY year
        """
    )
    aspirants = rows(
        """
        SELECT candidate_name,
               CASE WHEN candidate_name = 'Hon. Silverster Ogina' THEN 'NEDP' ELSE party END AS party,
               election_year, 0 AS votes, 0 AS total_votes,
               status,
               CASE WHEN candidate_name = 'Hon. Silverster Ogina' THEN 'campaign_record' ELSE source_type END AS source_type,
               CASE WHEN candidate_name = 'Hon. Silverster Ogina' THEN 0 ELSE is_placeholder END AS is_placeholder
        FROM candidate_profiles
        WHERE election_year = 2027
          AND candidate_name = 'Hon. Silverster Ogina'
        ORDER BY candidate_name
        """
    )
    data = historical + aspirants
    by_year: dict[str, list[dict]] = {}
    for record in data:
        by_year.setdefault(str(record["election_year"]), []).append(record)
    return jsonify({"success": True, "data": data, "by_year": by_year, "total_candidates": len(data)})


@app.route("/api/candidates/vote-shift-analysis")
def api_vote_shift_analysis():
    winners = rows("SELECT * FROM historical_winners ORDER BY year")
    data = []
    previous = None
    for winner in winners:
        votes = winner["votes"]
        change = votes - previous["votes"] if previous else 0
        data.append(
            {
                "candidate_name": winner["candidate_name"],
                "party": winner["party_abbrev"],
                "year": winner["year"],
                "winner_votes": votes,
                "votes_2017": previous["votes"] if previous and previous["year"] == 2017 else 0,
                "votes_2022": votes if winner["year"] == 2022 else 0,
                "vote_change": change,
                "change_percent": round(change / previous["votes"] * 100, 1) if previous else 0,
                "trend": "Growing" if change > 0 else "Declining" if change < 0 else "Baseline",
                "source_type": winner["source_type"],
            }
        )
        previous = winner
    return jsonify({"success": True, "data": data, "total_candidates_tracked": len(data)})


@app.route("/api/mobilization")
def api_mobilization():
    data = rows("SELECT * FROM features ORDER BY mobilization_score DESC LIMIT 30")
    return jsonify({"success": True, "data": data})


@app.route("/api/mca/competitive-landscape")
def api_competitive_landscape():
    data = rows(
        """
        SELECT candidate_name, party, SUM(votes) AS total_votes,
               ROUND(100.0 * SUM(votes) / (SELECT SUM(votes) FROM polling_results WHERE year = 2022), 2) AS vote_share_percent,
               COUNT(DISTINCT polling_station_id) AS stations_won_or_competed,
               MAX(source_type) AS source_type,
               MAX(is_placeholder) AS is_placeholder
        FROM polling_results
        WHERE year = 2022
        GROUP BY candidate_name, party
        ORDER BY total_votes DESC
        """
    )
    return jsonify({"success": True, "data": data, "count": len(data)})


@app.route("/api/mca/sentiment-summary")
def api_sentiment_summary():
    data = rows(
        """
        SELECT candidate_name,
               AVG(sentiment_score) AS sentiment_score,
               SUM(total_mentions) AS total_mentions,
               COUNT(*) AS record_count,
               CASE WHEN candidate_name = 'Hon. Silverster Ogina' THEN 'campaign_record' ELSE MAX(source_type) END AS source_type,
               CASE WHEN candidate_name = 'Hon. Silverster Ogina' THEN 0 ELSE MAX(is_placeholder) END AS is_placeholder
        FROM sentiment_data
        WHERE candidate_name = 'Hon. Silverster Ogina'
        GROUP BY candidate_name
        ORDER BY total_mentions DESC
        """
    )
    for row in data:
        source = row.get("source_type")
        records = int(row.get("record_count") or 0)
        total = int(float(row.get("total_mentions") or 0))
        average_per_record = total / records if records else total
        if source in {"demo_placeholder", "campaign_record"} and records >= 10 and average_per_record > 20:
            row["total_mentions"] = max(8, round(total / records))
        row.pop("record_count", None)
    return jsonify({"success": True, "data": data})


@app.route("/api/mca/social-issues")
def api_social_issues():
    issue_rows = rows(
        """
        SELECT
            primary_theme,
            SUM(total_mentions) AS total_mentions,
            AVG(sentiment_score) AS sentiment_score,
            MAX(source_type) AS source_type,
            MAX(is_placeholder) AS is_placeholder
        FROM sentiment_data
        WHERE primary_theme IS NOT NULL AND primary_theme != ''
          AND candidate_name = 'Hon. Silverster Ogina'
        GROUP BY primary_theme
        """
    )
    aggregates: dict[str, dict] = {}
    for item in issue_rows:
        raw_theme = str(item.get("primary_theme") or "").strip()
        canonical = ISSUE_ALIASES.get(raw_theme.lower(), raw_theme)
        if canonical not in ISSUE_RESPONSE_GUIDE:
            continue
        existing = aggregates.setdefault(
            canonical,
            {
                "total_mentions": 0,
                "weighted_sentiment": 0.0,
                "source_type": item.get("source_type") or "demo_placeholder",
                "is_placeholder": item.get("is_placeholder", 1),
            },
        )
        mentions = int(item.get("total_mentions") or 0)
        score = float(item.get("sentiment_score") or 0)
        existing["total_mentions"] += mentions
        existing["weighted_sentiment"] += mentions * score

    simulated_baseline = {
        "Water Supply": (84, -0.44),
        "Roads & Drainage": (78, -0.39),
        "Security": (72, -0.34),
        "Youth Jobs": (66, -0.18),
        "Waste Collection": (57, -0.31),
        "Bursaries & School Fees": (50, -0.12),
        "Health Services": (42, -0.16),
        "Business Permits": (36, -0.08),
    }
    output = []
    for issue, guide in ISSUE_RESPONSE_GUIDE.items():
        aggregate = aggregates.get(issue)
        if aggregate and aggregate["total_mentions"]:
            if aggregate["is_placeholder"]:
                mentions, sentiment = simulated_baseline[issue]
            else:
                mentions = aggregate["total_mentions"]
                sentiment = aggregate["weighted_sentiment"] / mentions
            source_type = aggregate["source_type"]
            is_placeholder = aggregate["is_placeholder"]
        else:
            mentions, sentiment = simulated_baseline[issue]
            source_type = "demo_placeholder"
            is_placeholder = 1
        priority = "Critical" if mentions >= 75 and sentiment <= -0.35 else "High" if mentions >= 60 else "Watch"
        output.append(
            {
                "issue": issue,
                "mentions": mentions,
                "sentiment_score": round(sentiment, 2),
                "priority": priority,
                "hotspots": guide["hotspots"],
                "voter_signal": guide["signal"],
                "recommended_response": guide["response"],
                "field_action": guide["field_action"],
                "message": guide["message"],
                "source_type": source_type,
                "is_placeholder": is_placeholder,
            }
        )
    output.sort(key=lambda row: ({"Critical": 0, "High": 1, "Watch": 2}[row["priority"]], -row["mentions"]))
    return jsonify({"success": True, "data": output})


@app.route("/api/data-provenance")
def api_data_provenance():
    table_counts = {}
    for table in [
        "polling_stations",
        "ward_registration",
        "historical_winners",
        "demographics",
        "candidate_profiles",
        "polling_results",
        "sentiment_data",
        "features",
    ]:
        table_counts[table] = one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
    return jsonify(
        {
            "success": True,
            "tables": table_counts,
            "sources": rows("SELECT * FROM data_sources ORDER BY source_type, source_key"),
        }
    )


@app.route("/api/assistant", methods=["POST"])
def api_assistant():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", "")).strip()
    result = answer_question(question)
    return jsonify({"success": True, **result})


@app.route("/api/system/database")
def api_database_status():
    return jsonify(
        {
            "success": True,
            "mysql_configured": DB.settings.mysql_enabled,
            "mysql_strict": DB.settings.mysql_strict,
            "mysql_error": DB.last_mysql_error,
            "using_mysql": DB.using_mysql,
            "table_prefix": DB.settings.table_prefix,
            "storage": "mysql",
        }
    )


@app.route("/api/system/ai")
def api_ai_status():
    return jsonify({"success": True, **ai_status()})


if __name__ == "__main__":
    ensure_database()
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"Dashboard: http://127.0.0.1:{port}")
    print(f"Admin:     http://127.0.0.1:{port}/admin")
    app.run(debug=False, host=host, port=port, use_reloader=False)
