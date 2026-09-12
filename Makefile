.DEFAULT_GOAL := run
.PHONY: run help containers rebuild clean down logs ingest ingest-dry ingest-since tests tui tui-build tui-tests

BACKEND_HEALTH_TIMEOUT ?= 180

# Recursive makes keep startup ordered, including with make -j.
run:
	$(MAKE) tests
	$(MAKE) containers
	@echo "Waiting up to $(BACKEND_HEALTH_TIMEOUT)s for http://localhost:8000/health..."
	@deadline=$$(( $$(date +%s) + $(BACKEND_HEALTH_TIMEOUT) )); \
	while :; do \
		remaining=$$((deadline - $$(date +%s))); \
		if [ "$$remaining" -le 0 ]; then \
			echo "Error: backend at http://localhost:8000/health did not become healthy within $(BACKEND_HEALTH_TIMEOUT)s. Check podman-compose logs backend ramalama." >&2; \
			exit 1; \
		fi; \
		request_timeout=5; \
		if [ "$$remaining" -lt "$$request_timeout" ]; then request_timeout=$$remaining; fi; \
		if curl --fail --silent --output /dev/null --max-time "$$request_timeout" http://localhost:8000/health; then \
			break; \
		fi; \
		sleep 2; \
	done
	$(MAKE) tui

help:
	@echo "Usage: make [target]"
	@echo "Targets:"
	@echo "  run          - Run tests, start containers, wait for backend health, and launch the TUI (default)"
	@echo "  containers   - Build (using cache) and start the Podman containers, always reflecting current source"
	@echo "  rebuild      - Force a from-scratch rebuild of all images (bypasses build cache) and restart containers"
	@echo "  clean        - Remove all Podman containers and images"
	@echo "  down         - Stop the Podman containers"
	@echo "  logs         - Write the live container logs into a file named 'logs.txt'"
	@echo "  tests         - Run the test suite (no containers required)"
	@echo "  ingest       - Ingest package metadata into ChromaDB (first-time setup only)"
	@echo "  ingest-dry   - Preview what ingest would index without writing to ChromaDB"
	@echo "  ingest-since - Re-index packages updated since a date: make ingest-since SINCE=2024-01-01"
	@echo "  tui          - Build and run the Go TUI (start the stack first)"
	@echo "  tui-build    - Build build/drillbit-tui"
	@echo "  tui-tests    - Run the Go TUI tests"

containers:
	@echo "Building (using cache) and starting Podman containers..."
	@old_backend=$$(podman images -q localhost/drillbit_backend:latest 2>/dev/null); \
	old_mcp=$$(podman images -q localhost/drillbit_mcp-server:latest 2>/dev/null); \
	old_ramalama=$$(podman images -q localhost/drillbit_ramalama:latest 2>/dev/null); \
	podman-compose up -d --build --force-recreate && \
	scripts/prune-stale-images.sh "$$old_backend" "$$old_mcp" "$$old_ramalama"
	@echo "Containers are up and running."

rebuild:
	@echo "Rebuilding (from scratch, bypassing cache) and restarting all containers..."
	@old_backend=$$(podman images -q localhost/drillbit_backend:latest 2>/dev/null); \
	old_mcp=$$(podman images -q localhost/drillbit_mcp-server:latest 2>/dev/null); \
	old_ramalama=$$(podman images -q localhost/drillbit_ramalama:latest 2>/dev/null); \
	podman-compose build --no-cache && \
	podman-compose up -d --force-recreate && \
	scripts/prune-stale-images.sh "$$old_backend" "$$old_mcp" "$$old_ramalama"
	@echo "Containers have been rebuilt and restarted."

down:
	@echo "Stopping Podman containers..."
	podman-compose down
	@echo "Containers have been stopped."

ingest:
	@echo ""
	@echo "WARNING: This indexes all COPR package metadata into ChromaDB."
	@echo "Only run this if the index has never been built."
	@echo "To preview without writing:        make ingest-dry"
	@echo "To refresh entries since a date:   make ingest-since SINCE=2024-01-01"
	@echo ""
	@read -p "Continue? [y/N] " ans && [ "$$ans" = "y" ] || exit 1
	podman exec -it drillbit-test_backend_1 python ingest.py

ingest-dry:
	podman exec -it drillbit-test_backend_1 python ingest.py --dry-run

ingest-since:
	@[ "$(SINCE)" ] || (echo "Usage: make ingest-since SINCE=2024-01-01" && exit 1)
	podman exec -it drillbit-test_backend_1 python ingest.py --since $(SINCE)

tests:
	uv run pytest tests/ -v

tui-build:
	cd tui && go build -o ../build/drillbit-tui .

tui: tui-build
	./build/drillbit-tui

tui-tests:
	cd tui && go test ./...
