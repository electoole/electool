# Embakasi Ward Electoral Intelligence

Campaign dashboard for Hon. Silverster Ogina focused on Embakasi Ward, Nairobi County.

## Render Deployment

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn simple_app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

The same commands are already configured in `render.yaml`.

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

The app uses prefixed database tables through `DB_TABLE_PREFIX=eii_`.
