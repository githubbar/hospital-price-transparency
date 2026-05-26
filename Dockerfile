# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Set work directory
WORKDIR /app

# Install dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . /app/

# Collect static files
# Set a dummy SECRET_KEY for building purposes if not present
RUN SECRET_KEY=build_secret_key python manage.py collectstatic --noinput

# Run DB migrations and create cache tables at build time (so startup is instantaneous)
RUN SECRET_KEY=build_secret_key python manage.py migrate --run-syncdb
RUN SECRET_KEY=build_secret_key python manage.py createcachetable

# Build the SQLite database and search index at build time (bypassed - pre-built database is copied directly)
# RUN python load_to_sqlite.py --clean --cached-file data/shoppable_cache.json.gz

# Copy and enable the startup entrypoint
COPY startup.sh /app/startup.sh
RUN chmod +x /app/startup.sh

CMD ["/app/startup.sh"]
