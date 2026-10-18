FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY . .

# Static files build time par jama hote hain (secret key sirf is step ke liye dummy hai)
RUN DJANGO_SECRET_KEY=build-only-not-used DJANGO_DEBUG=False python manage.py collectstatic --noinput

RUN useradd --create-home appuser && mkdir -p /app/media && chown -R appuser /app/media
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn taskmanagement.wsgi:application --bind 0.0.0.0:8000 --workers 3"]
