.PHONY: setup dev test fga-test fga-model lint reset e2e docs-check docs-links
setup:   ## docker data plane + migrations + provisioning + seed + web deps
	./scripts/setup.sh
dev:     ## run agents, API, worker and web
	./scripts/dev.sh
test:    ## authorization model tests + acceptance suite and evaluation integrity tests (needs docker data plane up)
	$(MAKE) fga-test
	uv run pytest tests -q
fga-test: ## OpenFGA model tests (infra/openfga/model.fga.yaml)
	docker run --rm -v "$(CURDIR)/infra/openfga":/m -w /m openfga/cli:latest model test --tests model.fga.yaml
fga-model: ## recompile infra/openfga/model.fga -> model.json after editing the model
	docker run --rm -v "$(CURDIR)/infra/openfga":/m -w /m openfga/cli:latest model transform --file model.fga > infra/openfga/model.json
lint:
	uv run ruff check apps agents workers packages connectors evals tests && cd apps/web && npm run -s openui:check && npx tsc --noEmit && npx eslint app components lib
reset:   ## reset dev data to the canonical seed
	./scripts/reset-demo.sh
e2e:     ## browser walkthrough against a running stack (needs playwright)
	node scripts/e2e_demo.mjs var/e2e

docs-check: ## validate local links, shared themes and render all Mermaid diagrams
	npm --prefix docs run check
docs-links: ## validate local documentation links and themes without rendering
	npm --prefix docs run check:links
