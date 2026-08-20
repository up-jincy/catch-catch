SHELL := /bin/bash

PYTHON_VERSION := 3.12
SEED := 20260819
DATABASE_PATH := data/generated/customer_signal.duckdb
BACKEND_HOST := 127.0.0.1
BACKEND_PORT := 8000
FRONTEND_HOST := 127.0.0.1
FRONTEND_PORT := 3000
API_BASE_URL := http://$(BACKEND_HOST):$(BACKEND_PORT)
ARTIFACT_DIRECTORY := data/run-artifacts

.DEFAULT_GOAL := help
.PHONY: help setup seed dev dev-fixture dev-gemini serve-backend-fixture serve-frontend test e2e e2e-generic e2e-legacy

help:
	@echo "make setup        의존성과 Playwright Chromium 설치"
	@echo "make seed         seed=$(SEED) 합성 DuckDB 생성"
	@echo "make dev          auto 모드로 Backend와 Frontend 실행"
	@echo "make dev-fixture  결정론적 fixture 모드로 실행"
	@echo "make dev-gemini   Gemini 전용 모드로 실행"
	@echo "make test         Backend/Frontend 전체 자동 검증"
	@echo "make e2e          fixture 기반 실제 브라우저 E2E"
	@echo "make e2e-generic  범용 분석 Desktop/Mobile E2E"
	@echo "make e2e-legacy   기존 Journey 회귀 E2E"

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
	@set -Eeuo pipefail; \
		repository_dir="$$(git rev-parse --show-toplevel)"; \
		env_file="$${ENV_FILE:-}"; \
		if [[ -n "$$env_file" ]]; then \
			[[ "$$env_file" = /* ]] || env_file="$$repository_dir/$$env_file"; \
			if [[ ! -f "$$env_file" ]]; then \
				echo "ENV_FILE이 존재하지 않습니다: $$env_file" >&2; \
				exit 2; \
			fi; \
		elif [[ -f "$$repository_dir/.env" ]]; then \
			env_file="$$repository_dir/.env"; \
		else \
			common_dir="$$(git -C "$$repository_dir" rev-parse --git-common-dir)"; \
			[[ "$$common_dir" = /* ]] || common_dir="$$repository_dir/$$common_dir"; \
			main_checkout="$$(cd "$$(dirname "$$common_dir")" && pwd -P)"; \
			[[ ! -f "$$main_checkout/.env" ]] || env_file="$$main_checkout/.env"; \
		fi; \
		env_args=(); \
		if [[ -n "$$env_file" ]]; then \
			env_args=(--env-file "$$env_file"); \
			printf 'Starting fixture Uvicorn: uvicorn ... --env-file %s --host %s --port %s\n' \
				"$$env_file" "$(BACKEND_HOST)" "$(BACKEND_PORT)"; \
		else \
			printf 'Starting fixture Uvicorn without an env file on %s:%s\n' \
				"$(BACKEND_HOST)" "$(BACKEND_PORT)"; \
		fi; \
		AGENT_MODE=fixture DATABASE_PATH="$(DATABASE_PATH)" \
			ARTIFACT_DIRECTORY="$(ARTIFACT_DIRECTORY)" \
			API_HOST="$(BACKEND_HOST)" API_PORT="$(BACKEND_PORT)" \
			FRONTEND_ORIGIN="http://$(FRONTEND_HOST):$(FRONTEND_PORT)" \
			uv run --project backend uvicorn customer_signal.api:create_app --factory \
			"$${env_args[@]}" --host "$(BACKEND_HOST)" --port "$(BACKEND_PORT)"

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
	npm --prefix frontend run e2e

e2e-generic:
	npm --prefix frontend run e2e -- generic-analysis.spec.ts

e2e-legacy:
	npm --prefix frontend run e2e -- working-demo.spec.ts
