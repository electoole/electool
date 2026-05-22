# Embakasi Ward Electoral Intelligence

Campaign dashboard for Hon. Silverster Ogina focused on Embakasi Ward, Nairobi County.

## Render Deployment

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn simple_app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
```

The same commands are already configured in `render.yaml`.

MySQL is required in every environment. The app does not use SQLite fallback storage.

## Required Render Environment Variables

Set these secrets in Render:

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_SSL_CA_CONTENT` or `MYSQL_SSL_CA`
- `GROQ_API_KEY`
- `GEMINI_API_KEY`

The app uses prefixed database tables through `DB_TABLE_PREFIX=eii_`. On Render, prefer `MYSQL_SSL_CA_CONTENT` for hosted MySQL CA certificates because local certificate files are not committed.

## Sentiment Uploads

The admin panel accepts either pre-scored sentiment rows or raw resident feedback.

For English `raw_text`, the app scores sentiment locally with VADER. For Swahili/non-English `raw_text`, it tries the configured Gemini/Groq model, then falls back to a local keyword scorer if AI is unavailable.

## Performance

The app enables response compression and short-lived server-side caching for public dashboard reads. Admin uploads and manual inserts clear the public cache immediately.
