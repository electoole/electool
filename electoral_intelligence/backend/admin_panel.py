#!/usr/bin/env python3
"""
ADMIN PANEL BACKEND
Handles data management:
1. CSV file uploads (validation + insertion)
2. Manual data entry (forms)
3. Data quality checks
4. Database updates
5. Data export
"""
import hmac
import io
import os
import secrets
import time
from functools import wraps

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for, Response
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash
import pandas as pd
from pathlib import Path
import json
from datetime import datetime

from database import DB
from sentiment_engine import classify_text, polarity_counts

# Create admin blueprint
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

UPLOAD_FOLDER = Path('electoral_intelligence/data/uploads')
ALLOWED_EXTENSIONS = {'csv', 'xlsx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Create upload folder
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
AUTO_REPLACE_TABLES = {'polling_stations', 'demographics', 'historical_winners', 'ward_registration'}


def admin_password_valid(password: str) -> bool:
    if ADMIN_PASSWORD_HASH:
        return check_password_hash(ADMIN_PASSWORD_HASH, password)
    if ADMIN_PASSWORD:
        return hmac.compare_digest(password, ADMIN_PASSWORD)
    return False


def admin_configured() -> bool:
    return bool(ADMIN_PASSWORD or ADMIN_PASSWORD_HASH)


def login_rate_limited(remote_addr: str) -> bool:
    now = time.time()
    attempts = [ts for ts in LOGIN_ATTEMPTS.get(remote_addr, []) if now - ts < LOGIN_WINDOW_SECONDS]
    LOGIN_ATTEMPTS[remote_addr] = attempts
    return len(attempts) >= MAX_LOGIN_ATTEMPTS


def record_failed_login(remote_addr: str) -> None:
    attempts = LOGIN_ATTEMPTS.setdefault(remote_addr, [])
    attempts.append(time.time())


def automatic_upload_mode(table_name: str) -> str:
    """Use a safe default so uploaders do not choose destructive behavior manually."""
    return "replace" if table_name in AUTO_REPLACE_TABLES else "append"


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authenticated"):
            if request.path.startswith("/admin/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)
    return wrapped


def require_csrf() -> tuple[bool, tuple | None]:
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not token or not hmac.compare_digest(token, session.get("csrf_token", "")):
        return False, (jsonify({"error": "Invalid CSRF token"}), 403)
    return True, None


@admin_bp.before_request
def protect_admin_routes():
    if request.endpoint in {"admin.login"}:
        return None
    if not session.get("admin_authenticated"):
        if request.path.startswith("/admin/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect(url_for("admin.login"))
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        ok, response = require_csrf()
        if not ok:
            return response
    return None

# ============================================================================
# ALLOWED DATA TABLES & SCHEMAS
# ============================================================================

ALLOWED_TABLES = {
    'polling_stations': {
        'required_columns': ['polling_station_id', 'polling_station_name', 'registered_voters_2022'],
        'description': 'Polling station locations and voter registration data'
    },
    'candidate_profiles': {
        'required_columns': ['candidate_name', 'party'],
        'description': 'Candidate background and intelligence data'
    },
    'polling_results': {
        'required_columns': ['polling_station_id', 'candidate_name', 'year', 'votes'],
        'description': 'Polling station results (all candidates per station)'
    },
    'sentiment_data': {
        'required_columns': ['date', 'candidate_name'],
        'description': 'Resident sentiment and issue-signal data'
    },
    'demographics': {
        'required_columns': ['unit_name', 'total_population_2019', 'households', 'male_population', 'female_population'],
        'description': 'Demographics and census data'
    },
    'historical_winners': {
        'required_columns': ['year', 'candidate_name', 'party', 'votes'],
        'description': 'Official ward winner records by election year'
    },
    'ward_registration': {
        'required_columns': ['ward_code', 'ward', 'registered_voters_2022'],
        'description': 'IEBC ward-level registered voter totals'
    }
}

UPLOAD_TEMPLATES = {
    'polling_stations': {
        'columns': ['polling_station_id', 'polling_station_name', 'sub_location', 'registered_voters_2022'],
        'example': {
            'polling_station_id': 'TASSIA_PS_01',
            'polling_station_name': 'TASSIA CATHOLIC PRIMARY SCHOOL',
            'sub_location': 'Tassia',
            'registered_voters_2022': 699,
        },
        'notes': 'polling_station_id must be unique and must match polling_results.polling_station_id.'
    },
    'candidate_profiles': {
        'columns': ['candidate_id', 'candidate_name', 'party', 'election_year', 'status', 'incumbent', 'profile_summary', 'stronghold_notes', 'weakness_notes', 'source_type', 'is_placeholder'],
        'example': {
            'candidate_id': 'REAL_2027_SILVERSTER_OGINA',
            'candidate_name': 'Hon. Silverster Ogina',
            'party': 'NEDP',
            'election_year': 2027,
            'status': 'Incoming MCA - campaign principal',
            'incumbent': 0,
            'profile_summary': 'Incoming MCA campaign principal for Embakasi Ward.',
            'stronghold_notes': 'Add verified stronghold notes.',
            'weakness_notes': 'Add verified risk notes.',
            'source_type': 'admin_uploaded',
            'is_placeholder': 0,
        },
        'notes': 'candidate_name should match names used in polling_results and sentiment_data.'
    },
    'polling_results': {
        'columns': ['polling_station_id', 'candidate_name', 'party', 'year', 'votes', 'registered_voters', 'turnout_rate', 'source_type', 'is_placeholder'],
        'example': {
            'polling_station_id': 'TASSIA_PS_01',
            'candidate_name': 'Hon. Silverster Ogina',
            'party': 'NEDP',
            'year': 2027,
            'votes': 180,
            'registered_voters': 699,
            'turnout_rate': 0.62,
            'source_type': 'admin_uploaded',
            'is_placeholder': 0,
        },
        'notes': 'polling_station_id must already exist in polling_stations. votes must be zero or higher.'
    },
    'sentiment_data': {
        'columns': ['date', 'candidate_name', 'raw_text', 'location', 'source_channel', 'total_mentions', 'positive_mentions', 'neutral_mentions', 'negative_mentions', 'primary_theme', 'sentiment_score', 'source_type', 'is_placeholder'],
        'example': {
            'date': '2026-05-22',
            'candidate_name': 'Hon. Silverster Ogina',
            'raw_text': 'Residents in Tassia say water supply has been unreliable this week.',
            'location': 'Tassia',
            'source_channel': 'field_note',
            'total_mentions': 1,
            'positive_mentions': '',
            'neutral_mentions': '',
            'negative_mentions': '',
            'primary_theme': '',
            'sentiment_score': '',
            'source_type': 'admin_uploaded',
            'is_placeholder': 0,
        },
        'notes': 'Upload either pre-scored rows with sentiment_score, or raw_text rows. English raw_text is scored with VADER; Swahili/non-English raw_text uses the configured AI model, then a local fallback. sentiment_score must be between -1 and 1.'
    },
    'demographics': {
        'columns': ['unit_name', 'total_population_2019', 'households', 'male_population', 'female_population'],
        'example': {
            'unit_name': 'Embakasi',
            'total_population_2019': 88874,
            'households': 25000,
            'male_population': 44000,
            'female_population': 44874,
        },
        'notes': 'Use KNBS area names consistently so dashboard grouping remains stable.'
    },
    'historical_winners': {
        'columns': ['year', 'candidate_name', 'party', 'party_abbrev', 'votes', 'source_type', 'is_placeholder'],
        'example': {
            'year': 2022,
            'candidate_name': 'Nyantika Ricardo Billy',
            'party': 'Orange Democratic Movement',
            'party_abbrev': 'ODM',
            'votes': 12877,
            'source_type': 'official_pdf',
            'is_placeholder': 0,
        },
        'notes': 'Use winner-level official records only in this table.'
    },
    'ward_registration': {
        'columns': ['ward_code', 'ward', 'constituency', 'county', 'registered_voters_2022'],
        'example': {
            'ward_code': '1423',
            'ward': 'Embakasi',
            'constituency': 'Embakasi East',
            'county': 'Nairobi',
            'registered_voters_2022': 46291,
        },
        'notes': 'registered_voters_2022 should be the official IEBC ward total.'
    },
}

# ============================================================================
# DATA VALIDATION & CLEANING
# ============================================================================

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def validate_csv_structure(df, table_name):
    """Validate CSV has required columns"""
    required_cols = ALLOWED_TABLES[table_name]['required_columns']
    missing_cols = [col for col in required_cols if col not in df.columns]
    
    if missing_cols:
        return False, f"Missing required columns: {', '.join(missing_cols)}"
    if table_name == 'sentiment_data':
        has_raw_text = 'raw_text' in df.columns and df['raw_text'].fillna('').astype(str).str.strip().ne('').any()
        has_scored = {'total_mentions', 'sentiment_score'}.issubset(df.columns)
        if not has_raw_text and not has_scored:
            return False, "Sentiment uploads need either raw_text or both total_mentions and sentiment_score columns"
    
    return True, "Structure valid"


def prepare_sentiment_data(df):
    """Score raw resident text rows and fill derived sentiment columns."""
    df = df.copy()
    for column, default in {
        'total_mentions': 1,
        'positive_mentions': pd.NA,
        'neutral_mentions': pd.NA,
        'negative_mentions': pd.NA,
        'primary_theme': '',
        'sentiment_score': pd.NA,
        'language': '',
        'sentiment_method': '',
    }.items():
        if column not in df.columns:
            df[column] = default

    if 'raw_text' not in df.columns:
        return df

    for idx, row in df.iterrows():
        raw_text = str(row.get('raw_text') or '').strip()
        if not raw_text:
            continue
        score_missing = pd.isna(row.get('sentiment_score')) or str(row.get('sentiment_score')).strip() == ''
        theme_missing = pd.isna(row.get('primary_theme')) or str(row.get('primary_theme')).strip() == ''
        method_missing = pd.isna(row.get('sentiment_method')) or str(row.get('sentiment_method')).strip() == ''
        language_missing = pd.isna(row.get('language')) or str(row.get('language')).strip() == ''
        if score_missing or theme_missing or method_missing or language_missing:
            result = classify_text(raw_text)
            if score_missing:
                df.at[idx, 'sentiment_score'] = result.sentiment_score
            if theme_missing:
                df.at[idx, 'primary_theme'] = result.primary_theme
            if method_missing:
                df.at[idx, 'sentiment_method'] = result.sentiment_method
            if language_missing:
                df.at[idx, 'language'] = result.language
        mentions = int(pd.to_numeric(pd.Series([df.at[idx, 'total_mentions']]), errors='coerce').fillna(1).iloc[0] or 1)
        missing_counts = any(
            pd.isna(df.at[idx, col]) or str(df.at[idx, col]).strip() == ''
            for col in ['positive_mentions', 'neutral_mentions', 'negative_mentions']
        )
        if missing_counts:
            positive, neutral, negative = polarity_counts(float(df.at[idx, 'sentiment_score'] or 0), mentions)
            df.at[idx, 'positive_mentions'] = positive
            df.at[idx, 'neutral_mentions'] = neutral
            df.at[idx, 'negative_mentions'] = negative
    return df


def validate_data_relationships(df, table_name):
    """Validate values that would otherwise break dashboard joins or scoring."""
    errors = []
    if table_name == 'polling_results':
        if 'polling_station_id' in df.columns:
            station_ids = set(str(row['polling_station_id']) for row in DB.query_all("SELECT polling_station_id FROM polling_stations"))
            unknown = sorted(set(df['polling_station_id'].dropna().astype(str)) - station_ids)
            if unknown:
                errors.append(f"Unknown polling_station_id values: {', '.join(unknown[:10])}")
        if 'votes' in df.columns and (pd.to_numeric(df['votes'], errors='coerce') < 0).any():
            errors.append("votes must be zero or higher")
        if 'year' in df.columns:
            years = pd.to_numeric(df['year'], errors='coerce')
            if years.isna().any() or (years < 2010).any() or (years > 2030).any():
                errors.append("year must be a valid election year between 2010 and 2030")
    if table_name == 'sentiment_data':
        if 'sentiment_score' in df.columns:
            scores = pd.to_numeric(df['sentiment_score'], errors='coerce')
            if scores.isna().any() or (scores < -1).any() or (scores > 1).any():
                errors.append("sentiment_score must be between -1 and 1")
        mention_cols = {'positive_mentions', 'neutral_mentions', 'negative_mentions', 'total_mentions'}
        if mention_cols.issubset(df.columns):
            parts = (
                pd.to_numeric(df['positive_mentions'], errors='coerce').fillna(0)
                + pd.to_numeric(df['neutral_mentions'], errors='coerce').fillna(0)
                + pd.to_numeric(df['negative_mentions'], errors='coerce').fillna(0)
            )
            totals = pd.to_numeric(df['total_mentions'], errors='coerce').fillna(-1)
            if (parts != totals).any():
                errors.append("total_mentions must equal positive_mentions + neutral_mentions + negative_mentions")
    if table_name in {'polling_stations', 'ward_registration', 'demographics'}:
        for col in ALLOWED_TABLES[table_name]['required_columns']:
            if col in df.columns and df[col].isna().any():
                errors.append(f"{col} cannot be empty")
    if errors:
        return False, "; ".join(errors)
    return True, "Values valid"

def clean_data(df, table_name):
    """Clean and normalize data"""
    df = df.fillna(pd.NA)
    
    # Remove duplicate rows
    initial_rows = len(df)
    df = df.drop_duplicates()
    duplicates = initial_rows - len(df)
    
    # Type conversions
    numeric_cols = ['votes', 'registered_voters', 'registered_voters_2022', 'age', 'experience_years',
                    'total_mentions', 'positive_mentions', 'neutral_mentions', 'negative_mentions',
                    'total_population_2019', 'households', 'male_population', 'female_population',
                    'is_placeholder']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

    float_cols = ['sentiment_score', 'turnout_rate']
    for col in float_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
    
    # Date conversions
    date_cols = ['date', 'collection_date', 'last_updated']
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')

    if table_name in {'polling_results', 'candidate_profiles', 'sentiment_data'}:
        if 'source_type' not in df.columns:
            df['source_type'] = 'admin_uploaded'
        if 'is_placeholder' not in df.columns:
            df['is_placeholder'] = 0

    if table_name == 'candidate_profiles':
        silverster = df['candidate_name'].astype(str).str.strip().eq('Hon. Silverster Ogina') if 'candidate_name' in df.columns else pd.Series(False)
        if silverster.any():
            df.loc[silverster, 'party'] = 'NEDP'
            df.loc[silverster, 'source_type'] = 'campaign_record'
            df.loc[silverster, 'is_placeholder'] = 0
    if table_name == 'sentiment_data' and 'candidate_name' in df.columns:
        silverster = df['candidate_name'].astype(str).str.strip().eq('Hon. Silverster Ogina')
        if silverster.any():
            df.loc[silverster, 'source_type'] = 'campaign_record'
            df.loc[silverster, 'is_placeholder'] = 0
    
    return df, duplicates


def recompute_features_if_needed(table_name):
    """Recompute derived station features after source-table changes."""
    if table_name not in {'polling_results', 'polling_stations'}:
        return
    try:
        DB.recompute_features()
    except Exception as exc:
        print(f"Feature recompute failed: {exc}")


def current_admin() -> str:
    return session.get("admin_username") or ADMIN_USERNAME


def audit_admin_action(action, table_name="", row_count=0, status="success", details=None):
    """Append an admin audit event without blocking the main workflow."""
    try:
        payload = {
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "admin_username": current_admin(),
            "action": action,
            "table_name": table_name,
            "row_count": int(row_count or 0),
            "status": status,
            "details": json.dumps(details or {}, default=str),
        }
        DB.write_table("admin_audit_log", pd.DataFrame([payload]), if_exists="append")
    except Exception as exc:
        print(f"Admin audit failed: {exc}")


def save_table_version(table_name, action):
    """Store a DB-backed snapshot before destructive table replacement."""
    try:
        if not DB.table_exists(table_name):
            return
        frame = DB.read_table(table_name)
        snapshot = {
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "admin_username": current_admin(),
            "table_name": table_name,
            "action": action,
            "row_count": len(frame),
            "snapshot_json": frame.to_json(orient="records", date_format="iso"),
        }
        DB.write_table("data_versions", pd.DataFrame([snapshot]), if_exists="append")
    except Exception as exc:
        print(f"Data version snapshot failed: {exc}")

# ============================================================================
# ADMIN API ENDPOINTS
# ============================================================================

@admin_bp.route('/')
def admin_dashboard():
    """Admin dashboard home"""
    return render_template('admin_dashboard.html', tables=ALLOWED_TABLES, csrf_token=session.get("csrf_token", ""))


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = ""
    if request.method == "POST":
        remote_addr = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
        if login_rate_limited(remote_addr):
            return render_template("admin_login.html", error="Too many failed attempts. Try again later."), 429
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if not admin_configured():
            error = "Admin password is not configured. Set ADMIN_PASSWORD or ADMIN_PASSWORD_HASH."
        elif hmac.compare_digest(username, ADMIN_USERNAME) and admin_password_valid(password):
            session.clear()
            session["admin_authenticated"] = True
            session["admin_username"] = username
            session["csrf_token"] = secrets.token_urlsafe(32)
            LOGIN_ATTEMPTS.pop(remote_addr, None)
            audit_admin_action("login", status="success", details={"remote_addr": remote_addr})
            return redirect(url_for("admin.admin_dashboard"))
        else:
            record_failed_login(remote_addr)
            error = "Invalid admin credentials."
    return render_template("admin_login.html", error=error)


@admin_bp.route('/logout', methods=['POST'])
def logout():
    audit_admin_action("logout", status="success")
    session.clear()
    return redirect(url_for("admin.login"))

@admin_bp.route('/api/tables')
def get_tables():
    """Get list of available tables for data management"""
    tables = {
        name: {
            **config,
            "template_columns": UPLOAD_TEMPLATES[name]["columns"],
            "template_notes": UPLOAD_TEMPLATES[name]["notes"],
        }
        for name, config in ALLOWED_TABLES.items()
    }
    return jsonify(tables)


@admin_bp.route('/api/template/<table_name>')
def download_template(table_name):
    """Download an example CSV that matches a managed table."""
    if table_name not in ALLOWED_TABLES:
        return jsonify({'error': f'Invalid table: {table_name}'}), 400
    template = UPLOAD_TEMPLATES[table_name]
    frame = pd.DataFrame([template['example']], columns=template['columns'])
    csv_buffer = io.StringIO()
    frame.to_csv(csv_buffer, index=False)
    audit_admin_action("download_template", table_name=table_name, row_count=1, status="success")
    return Response(
        csv_buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table_name}_template.csv"'},
    )

@admin_bp.route('/api/upload', methods=['POST'])
def upload_data():
    """
    Handle CSV file upload and database insertion
    Parameters:
        - table_name: target table name
        - file: CSV or XLSX file to upload
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        if 'table_name' not in request.form:
            return jsonify({'error': 'No table_name specified'}), 400
        
        file = request.files['file']
        table_name = request.form.get('table_name').lower()
        merge_mode = request.form.get('merge_mode', 'auto')
        if merge_mode == 'auto':
            merge_mode = automatic_upload_mode(table_name)
        if merge_mode not in {'replace', 'append'}:
            return jsonify({'error': 'Invalid merge mode'}), 400
        
        # Validate table name
        if table_name not in ALLOWED_TABLES:
            return jsonify({'error': f'Invalid table: {table_name}'}), 400
        
        # Validate file
        if not allowed_file(file.filename):
            return jsonify({'error': 'Only CSV and XLSX files allowed'}), 400
        
        if request.content_length and request.content_length > MAX_FILE_SIZE:
            return jsonify({'error': 'File too large (max 10MB)'}), 400
        
        # Save uploaded file
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filepath = UPLOAD_FOLDER / f"{table_name}_{timestamp}_{filename}"
        file.save(filepath)
        
        # Read file
        if filename.endswith('.csv'):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath)
        
        # Validate structure
        is_valid, validation_msg = validate_csv_structure(df, table_name)
        if not is_valid:
            return jsonify({'error': validation_msg}), 400

        if table_name == 'sentiment_data':
            df = prepare_sentiment_data(df)
        
        # Clean data
        df_clean, duplicates_removed = clean_data(df, table_name)
        values_valid, values_msg = validate_data_relationships(df_clean, table_name)
        if not values_valid:
            return jsonify({'error': values_msg}), 400
        
        if merge_mode == 'replace':
            save_table_version(table_name, "replace_upload")
            DB.write_table(table_name, df_clean, if_exists='replace')
            msg = f"Updated {table_name.replace('_', ' ')} with {len(df_clean)} records"
        else:  # append
            DB.write_table(table_name, df_clean, if_exists='append')
            msg = f"Added {len(df_clean)} records to {table_name.replace('_', ' ')}"
        recompute_features_if_needed(table_name)
        audit_admin_action(
            "upload",
            table_name=table_name,
            row_count=len(df_clean),
            status="success",
            details={
                "upload_mode": merge_mode,
                "filename": filename,
                "duplicates_removed": duplicates_removed,
            },
        )
        
        return jsonify({
            'success': True,
            'message': msg,
            'details': {
                'rows_imported': len(df_clean),
                'duplicates_removed': duplicates_removed,
                'table': table_name,
                'upload_mode': merge_mode,
                'file_saved': str(filepath)
            }
        }), 200
    
    except Exception as e:
        audit_admin_action(
            "upload",
            table_name=request.form.get("table_name", ""),
            status="failed",
            details={"error": str(e)},
        )
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/api/data/<table_name>', methods=['GET'])
def get_table_data(table_name):
    """Get current data from a table"""
    try:
        if table_name not in ALLOWED_TABLES:
            return jsonify({'error': f'Invalid table: {table_name}'}), 400
        
        df = DB.read_table(table_name, limit=100)
        
        return jsonify({
            'table': table_name,
            'row_count': len(df),
            'data': df.to_dict('records')
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/api/data/<table_name>', methods=['POST'])
def insert_data_manual(table_name):
    """
    Manually insert a single record
    Parameters: JSON record matching table schema
    """
    try:
        if table_name not in ALLOWED_TABLES:
            return jsonify({'error': f'Invalid table: {table_name}'}), 400
        
        data = request.get_json(silent=True) or {}
        
        # Validate required fields
        required_cols = ALLOWED_TABLES[table_name]['required_columns']
        missing_fields = [col for col in required_cols if col not in data]
        
        if missing_fields:
            return jsonify({'error': f'Missing fields: {missing_fields}'}), 400
        
        # Create DataFrame and clean
        df = pd.DataFrame([data])
        if table_name == 'sentiment_data':
            df = prepare_sentiment_data(df)
        df_clean, _ = clean_data(df, table_name)
        values_valid, values_msg = validate_data_relationships(df_clean, table_name)
        if not values_valid:
            return jsonify({'error': values_msg}), 400
        
        DB.write_table(table_name, df_clean, if_exists='append')
        recompute_features_if_needed(table_name)
        audit_admin_action("manual_insert", table_name=table_name, row_count=1, status="success")
        
        return jsonify({
            'success': True,
            'message': f'Record inserted into {table_name}',
            'record': df_clean.to_dict('records')[0]
        }), 200
    
    except Exception as e:
        audit_admin_action(
            "manual_insert",
            table_name=table_name,
            status="failed",
            details={"error": str(e)},
        )
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/api/export/<table_name>')
def export_table(table_name):
    """Export table as CSV"""
    try:
        if table_name not in ALLOWED_TABLES:
            return jsonify({'error': f'Invalid table: {table_name}'}), 400
        
        df = DB.read_table(table_name)
        audit_admin_action("export", table_name=table_name, row_count=len(df), status="success")
        
        # Return CSV
        csv_data = df.to_csv(index=False)
        return csv_data, 200, {
            'Content-Disposition': f'attachment; filename="{table_name}.csv"',
            'Content-Type': 'text/csv; charset=utf-8',
        }
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_bp.route('/api/stats')
def get_stats():
    """Get database statistics"""
    try:
        stats = {}
        for table_name in ALLOWED_TABLES.keys():
            try:
                row = DB.query_one(f"SELECT COUNT(*) AS count FROM {table_name}")
                count = row["count"] if row else 0
                stats[table_name] = count
            except:
                stats[table_name] = 0
        return jsonify(stats), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/audit-log')
def get_audit_log():
    """Return recent admin actions for accountability."""
    try:
        df = DB.read_table("admin_audit_log")
        if "created_at" in df.columns:
            df = df.sort_values("created_at", ascending=False)
        return jsonify({
            "success": True,
            "data": df.head(100).to_dict("records"),
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/api/versions')
def get_data_versions():
    """Return available pre-replace data snapshots."""
    try:
        df = DB.read_table("data_versions")
        if "snapshot_json" in df.columns:
            df = df.drop(columns=["snapshot_json"])
        if "created_at" in df.columns:
            df = df.sort_values("created_at", ascending=False)
        return jsonify({
            "success": True,
            "data": df.head(50).to_dict("records"),
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# HELPER FUNCTION FOR FLASK APP INTEGRATION
# ============================================================================

def register_admin_panel(app):
    """Register admin panel blueprint with Flask app"""
    app.register_blueprint(admin_bp)
    print("✅ Admin panel registered at /admin")
