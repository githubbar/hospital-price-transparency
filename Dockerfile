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

# Pre-compute the cache doc count so check_and_reload.py doesn't stream the
# entire file on every container restart just to get a count.
# NDJSON format: count non-empty lines — much faster than ijson streaming.
RUN python -c "import gzip, os; cache_file = 'data/shoppable_cache.json.gz'; count = sum(1 for line in gzip.open(cache_file, 'rt', encoding='utf-8') if line.strip()) if os.path.exists(cache_file) else 0; open(cache_file + '.count', 'w').write(str(count)); print('Pre-computed cache doc count: ' + str(count))"

# Copy and enable the startup entrypoint
COPY startup.sh /app/startup.sh
RUN chmod +x /app/startup.sh

# startup.sh launches check_and_reload.py in the background (auto-reloads ES data
# when the index is empty, e.g. after a Spot VM preemption) then starts gunicorn.
CMD ["/app/startup.sh"]
