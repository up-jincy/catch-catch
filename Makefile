SHELL := /bin/bash

PYTHON_VERSION := 3.12
SEED := 20260819
DATABASE_PATH := data/generated/customer_signal.duckdb
BACKEND_HOST := 127.0.0.1
BACKEND_PORT := 8000
FRONTEND_HOST := 127.0.0.1
FRONTEND_PORT := 3000
API_BASE_URL := http://$(BACKEND_HOST):$(BACKEND_PORT)

.DEFAULT_GOAL := help
.PHONY: help setup seed dev dev-fixture dev-gemini serve-backend-fixture serve-frontend test e2e

help:
	@echo "make setup        의존성과 Playwright Chromium 설치"
	@echo "make seed         seed=$(SEED) 합성 DuckDB 생성"
	@echo "make dev          auto 모드로 Backend와 Frontend 실행"
	@echo "make dev-fixture  결정론적 fixture 모드로 실행"
	@echo "make dev-gemini   Gemini 전용 모드로 실행"
	@echo "make test         Backend/Frontend 전체 자동 검증"
	@echo "make e2e          fixture 기반 실제 브라우저 E2E"

setup:
	uv sync --project backend --python $(PYTHON_VERSION)
	npm --prefix frontend ci
	npm --prefix frontend run e2e:install

seed:
	uv run --project backend python -m customer_signal.data.cli \
		--database "$(DATABASE_PATH)" --seed "$(SEED)"

dev:
	bash scripts/dev.sh auto

dev-fixture:
	bash scripts/dev.sh fixture

dev-gemini:
	bash scripts/dev.sh gemini

serve-backend-fixture: seed
	AGENT_MODE=fixture DATABASE_PATH="$(DATABASE_PATH)" \
		API_HOST="$(BACKEND_HOST)" API_PORT="$(BACKEND_PORT)" \
		FRONTEND_ORIGIN="http://$(FRONTEND_HOST):$(FRONTEND_PORT)" \
		uv run --project backend uvicorn customer_signal.api:create_app --factory \
		--host "$(BACKEND_HOST)" --port "$(BACKEND_PORT)"

serve-frontend:
	NEXT_PUBLIC_API_BASE_URL="$(API_BASE_URL)" \
		npm --prefix frontend run dev -- --port "$(FRONTEND_PORT)"

test:
	uv run --project backend pytest backend/tests -q
	uv run --project backend ruff check backend
	npm --prefix frontend test -- --run
	npm --prefix frontend run typecheck
	NEXT_PUBLIC_API_BASE_URL="$(API_BASE_URL)" npm --prefix frontend run build

e2e:
	AGENT_MODE=fixture npm --prefix frontend run e2e
