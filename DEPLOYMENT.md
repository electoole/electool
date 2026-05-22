# Render Deployment

This repository is prepared for a Git-backed Render web service.

## Required Render Environment Variables

Set these in the Render dashboard. Do not commit real values.

- `SECRET_KEY`
- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `DB_TABLE_PREFIX`
- `MYSQL_STRICT=true`
- `MYSQL_SSL_DISABLED=false`
- `MYSQL_SSL_CA` or `MYSQL_SSL_CA_CONTENT` if your MySQL provider requires a CA
- `GROQ_API_KEY`
- `GEMINI_API_KEY`

## Start Command

Render uses:

```bash
gunicorn simple_app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

## Notes

- The admin panel is at `/admin`.
- Admin upload/manual-entry endpoints require login and CSRF protection.
- `.env`, certificates, SQLite databases, raw PDFs, old extraction scripts, and local generated files are ignored by Git.
- `data/extracted_real/*.csv` is intentionally included so the app can seed data without reparsing PDFs.
