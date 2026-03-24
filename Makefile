.PHONY: help containers rebuild clean down logs ingest ingest-dry ingest-since test

help:
	@echo "Usage: make [target]"
	@echo "Targets:"
	@echo "  containers   - Build and start the Podman containers"
	@echo "  rebuild      - Force rebuild all images and restart containers (even if they exist)"
	@echo "  clean        - Remove all Podman containers and images"
	@echo "  down         - Stop the Podman containers"
	@echo "  logs         - Write the live container logs into a file named 'logs.txt'"
	@echo "  test         - Run the test suite (no containers required)"
	@echo "  ingest       - Ingest package metadata into ChromaDB (first-time setup only)"
	@echo "  ingest-dry   - Preview what ingest would index without writing to ChromaDB"
	@echo "  ingest-since - Re-index packages updated since a date: make ingest-since SINCE=2024-01-01"

containers:
	@echo "Building and starting Podman containers..."
	podman-compose up -d
	@echo "Containers are up and running."

rebuild:
	@echo "Rebuilding and restarting all containers..."
	podman-compose up -d --build
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

test:
	pytest tests/ -v
