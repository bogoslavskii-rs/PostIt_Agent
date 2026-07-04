FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY data/.gitkeep ./data/.gitkeep

RUN python -m pip install --upgrade pip && \
    pip install -e .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import json, urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health'))['status'])"

CMD ["uvicorn", "postit_agent.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]

