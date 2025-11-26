import os

# Render provides a 'PORT' environment variable.
# We default to 8080 if it's not set (e.g., for local testing).
port = os.environ.get('PORT', '8080')

# Bind to '0.0.0.0' to allow connections from outside the container
# and use the PORT provided by Render.
bind = f"0.0.0.0:{port}"

# You can adjust the number of workers based on your app's needs
workers = int(os.environ.get('GUNICORN_WORKERS', '3'))
