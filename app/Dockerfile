FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_INIT_ON_START=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home app
USER app

EXPOSE 5000
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/health', timeout=2).status == 200 else 1)"

# --preload: create the DB schema once in the master before forking workers
CMD ["gunicorn", "--preload", "-w", "2", "--threads", "4", "-b", "0.0.0.0:5000", \
     "--timeout", "30", "app:app"]
