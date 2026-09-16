# Niyān Server

## Runtime configuration

The server reads runtime configuration from environment variables. For local development, `django-environ` loads the gitignored `.env` file in this directory; operating-system environment variables take precedence. Start from `.env.example` and provide real values for the deployment.

`django-environ` provides typed environment parsing, including Django database URLs. `psycopg` is the PostgreSQL adapter required by Django for `NIYAN_DATABASE_URL` connections.
