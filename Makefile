PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PYTEST := .venv/bin/pytest
UVICORN := .venv/bin/uvicorn

.PHONY: install dev test smoke docker-build docker-up docker-up-ollama docker-down

install:
	python3 -m venv .venv
	$(PIP) install -e .[dev]

dev:
	$(UVICORN) postit_agent.main:app --reload --app-dir src --host 0.0.0.0 --port 8000

test:
	$(PYTEST)

smoke:
	$(PYTHON) -c "from postit_agent.main import app; print(app.title)"

docker-build:
	docker compose build

docker-up:
	docker compose up --build

docker-up-ollama:
	docker compose --profile ollama up --build

docker-down:
	docker compose down

