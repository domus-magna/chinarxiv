PY?=$(shell command -v python3.11 2>/dev/null || command -v python3 2>/dev/null || echo python)
PORT?=8001
VENV=.venv
VPY=$(VENV)/bin/python
VPIP=$(VENV)/bin/pip
DEV_LIMIT?=5
MODEL?=

.PHONY: setup test lint fmt smoke build serve health clean samples check-keys fix-keys ensure-env self-review gate-fixtures dev-clean

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

venv:
	$(PY) -m venv $(VENV)
	$(VPY) -m pip install --upgrade pip
	$(VPIP) install -r requirements.txt

test:
	# Suppress noisy third-party SWIG DeprecationWarnings emitted by PyMuPDF/fitz
	# (including a shutdown-time warning that bypasses pytest's normal filtering).
	PYTHONWARNINGS="ignore:.*SwigPyPacked has no __module__ attribute.*:DeprecationWarning,ignore:.*SwigPyObject has no __module__ attribute.*:DeprecationWarning,ignore:.*swigvarlink has no __module__ attribute.*:DeprecationWarning" \
		$(PY) -m pytest -q

lint:
	ruff check src tests

fmt:
	ruff format src tests

health:
	$(PY) -m src.health --skip-openrouter || true

check-keys:
	$(PY) -m src.tools.env_diagnose --check

fix-keys:
	$(PY) -m src.tools.env_diagnose --fix

ensure-env:
	@if ! $(PY) -m src.tools.env_diagnose --fix --validate; then \
		echo "❌ Environment check failed - run 'make fix-keys' first"; \
		exit 1; \
	fi

self-review:
	bash scripts/self_review.sh

self-review-skip:
	@echo "Marking self-review as completed (manual override)"
	date +%s > .self_review_log

self-review-status:
	@if [ -f .self_review_log ]; then \
		echo "Last self-review: $$(date -r .self_review_log '+%Y-%m-%d %H:%M')"; \
		AGE=$$(( $$(date +%s) - $$(cat .self_review_log) )); \
		if [ $$AGE -lt 3600 ]; then \
			echo "✅ Valid (within 1 hour)"; \
		else \
			echo "❌ Expired (older than 1 hour)"; \
		fi \
	else \
		echo "❌ No self-review found"; \
	fi

gate-fixtures:
	$(PY) scripts/prepare_gate_fixtures.py

smoke:
	# Attempt ChinaXiv harvest via BrightData (optimized) for current month if credentials exist
	@if [ -n "$$BRIGHTDATA_API_KEY" ] && [ -n "$$BRIGHTDATA_ZONE" ]; then \
		$(PY) -m src.harvest_chinaxiv_optimized --month $$($(PY) -c 'import datetime;print(datetime.datetime.utcnow().strftime("%Y%m"))') || true; \
	else \
		echo 'Skipping harvest: set BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE to enable'; \
	fi
	@latest=$$(ls -1t data/records/*.json 2>/dev/null | head -n1 || echo ''); \
	if [ -n "$$latest" ]; then \
		$(PY) -m src.select_and_fetch --records "$$latest" --limit 2 --output data/selected.json || true; \
	else \
		echo '[]' > data/selected.json; \
	fi
	$(PY) -m src.translate --selected data/selected.json --dry-run
	$(PY) -m src.render
	$(PY) -m src.search_index
	$(PY) -m src.make_pdf || true

build: smoke

serve:
	$(PY) -m http.server -d site $(PORT)

site-from-b2:
	# Load .env so BACKBLAZE_* vars are present
	set -a; [ -f .env ] && . .env; set +a; \
	if [ -z "$$BACKBLAZE_KEY_ID" ] || [ -z "$$BACKBLAZE_APPLICATION_KEY" ] || [ -z "$$BACKBLAZE_S3_ENDPOINT" ] || [ -z "$$BACKBLAZE_BUCKET" ]; then \
		echo "Set BACKBLAZE_* env vars in .env (KEY_ID, APPLICATION_KEY, S3_ENDPOINT, BUCKET)"; exit 1; \
	fi; \
	python -m pip install --upgrade pip >/dev/null 2>&1 || true; \
	pip install awscli >/dev/null 2>&1 || true; \
	export AWS_ACCESS_KEY_ID="$$BACKBLAZE_KEY_ID"; \
	export AWS_SECRET_ACCESS_KEY="$$BACKBLAZE_APPLICATION_KEY"; \
	export AWS_DEFAULT_REGION=us-west-004; \
	DEST="s3://$$BACKBLAZE_BUCKET/$$BACKBLAZE_PREFIX"; \
	rm -rf data/translated && mkdir -p data/translated; \
	echo "⬇️  Syncing $${DEST}validated/translations → data/translated ..."; \
	aws s3 sync "$${DEST}validated/translations" data/translated --exclude "*" --include "*.json" --endpoint-url "$$BACKBLAZE_S3_ENDPOINT" --only-show-errors || true; \
	COUNT=$$(ls -1 data/translated/*.json 2>/dev/null | wc -l | tr -d ' '); \
	echo "Found $$COUNT validated translation JSON files"; \
	if [ "$$COUNT" = "0" ]; then \
		echo "No validated translations found; aborting build"; \
		exit 1; \
	fi; \
	$(PY) -m src.render; \
	$(PY) -m src.search_index; \
	-$(PY) -m src.make_pdf; \
	@echo "Starting server at http://localhost:$(PORT) (Ctrl+C to stop)"; \
	$(PY) -m http.server -d site $(PORT)

samples:
	@echo "Generating before/after samples into site/samples/ ..."
	$(PY) -m src.tools.formatting_compare --count 3 || true
	@echo "Open http://localhost:$(PORT)/samples/ after running 'make serve'"

clean:
	rm -rf site data

dev: venv ensure-env
	@if [ -d site ] || [ -d data ]; then \
		if [ "$$DEV_ALLOW_CLEAN" != "1" ]; then \
			echo "Refusing to delete existing site/ or data/. Set DEV_ALLOW_CLEAN=1 make dev to proceed."; \
			exit 1; \
		fi; \
	fi
	$(MAKE) clean
	@if [ -z "$$OPENROUTER_API_KEY" ] && [ ! -f .env ]; then echo "Set OPENROUTER_API_KEY or create .env"; exit 1; fi
	$(VPY) -m pytest -q
	# Try harvest via BrightData if configured
	@if [ -n "$$BRIGHTDATA_API_KEY" ] && [ -n "$$BRIGHTDATA_ZONE" ]; then \
		$(VPY) -m src.harvest_chinaxiv_optimized --month $$($(VPY) -c 'import datetime;print(datetime.datetime.utcnow().strftime("%Y%m"))') || true; \
	else \
		echo 'Skipping harvest: set BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE to enable'; \
	fi
	@latest=$$(ls -1t data/records/*.json 2>/dev/null | head -n1 || echo ''); \
	if [ -n "$$latest" ]; then \
		$(VPY) -m src.select_and_fetch --records "$$latest" --limit $(DEV_LIMIT) --output data/selected.json || true; \
	else \
		echo '[]' > data/selected.json; \
	fi
	@if [ ! -s data/selected.json ] || [ "$$($(VPY) -c 'import json,sys;print(json.load(open("data/selected.json"))==[])' )" = "True" ]; then \
		echo 'Seeding sample record for dev...'; \
		mkdir -p data; \
		echo '[{"id":"dev-1","oai_identifier":"oai:chinaxiv.org:dev-1","title":"Test title","abstract":"This is a test abstract with formula $E=mc^2$.","creators":["Li, Hua"],"subjects":["cs.AI"],"date":"2025-10-03","source_url":"https://example.org/","license":{"raw":"CC BY"}}]' > data/selected.json; \
	fi
	# Translate (allow model override; fallback to dry-run on failure)
		$(VPY) -m src.translate --selected data/selected.json $(if $(MODEL),--model $(MODEL),) \
	|| (echo 'Translation failed; falling back to --dry-run' && $(VPY) -m src.translate --selected data/selected.json --dry-run)
	# Build and serve the site locally
	$(VPY) -m src.render
	$(VPY) -m src.search_index
	-$(VPY) -m src.make_pdf
	@echo "Starting server at http://localhost:$(PORT) (Ctrl+C to stop)"
	$(VPY) -m http.server -d site $(PORT)

dev-clean:
	@DEV_ALLOW_CLEAN=1 $(MAKE) dev

admin:
	# Load .env first so checks see values
	set -a; [ -f .env ] && . .env; set +a; \
	if [ -z "$$ADMIN_PASSWORD" ]; then echo "Set ADMIN_PASSWORD in .env to protect the admin UI"; exit 1; fi; \
	if [ -z "$$GH_TOKEN" ]; then echo "Set GH_TOKEN (repo+workflow scope) in .env"; exit 1; fi; \
	if [ -z "$$GH_REPO" ]; then echo "Set GH_REPO (e.g., owner/repo) in .env"; exit 1; fi; \
	$(PY) -m flask --app src.admin_ci:make_app run --host 127.0.0.1 --port 8081
