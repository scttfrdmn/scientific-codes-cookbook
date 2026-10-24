.PHONY: check check-links catalog bootstrap stage spec run ls print-bucket

# Your cookbook bucket — holds staged inputs and run outputs, in YOUR account.
# Override with `make run COOKBOOK_BUCKET=my-bucket` if you want a different name.
AWS_ACCOUNT     := $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
AWS_REGION      := $(shell aws configure get region 2>/dev/null)
COOKBOOK_BUCKET ?= cookbook-$(AWS_ACCOUNT)-$(AWS_REGION)

check: ## frontmatter + markdown-a11y + internal links + spec portability + measurement records + catalog freshness
	@python3 scripts/check_pages.py
	@python3 scripts/gen_catalog.py --check

inventory: ## per-recipe matrix: frontmatter, contract, last_verified, measurement linkage
	@python3 scripts/check_pages.py --inventory

catalog: ## regenerate catalog/recipes.md and catalog/record.json from frontmatter, specs and measurements/*/record.toml
	@python3 scripts/gen_catalog.py

check-links: ## everything in `check`, plus external link liveness (network)
	@python3 scripts/check_pages.py --external

print-bucket: ## print the resolved COOKBOOK_BUCKET
	@echo $(COOKBOOK_BUCKET)

bootstrap: ## create your cookbook bucket in your account (once)
	@test -n "$(AWS_ACCOUNT)" || { echo "AWS not configured — run 'aws configure' (aws sts get-caller-identity must succeed)"; exit 1; }
	@test -n "$(AWS_REGION)"  || { echo "no default region — set one with 'aws configure'"; exit 1; }
	@aws s3 mb s3://$(COOKBOOK_BUCKET) 2>/dev/null && echo "created s3://$(COOKBOOK_BUCKET)" || echo "s3://$(COOKBOOK_BUCKET) already exists — fine"
	@echo "COOKBOOK_BUCKET=$(COOKBOOK_BUCKET)"

stage: ## build a recipe's inputs into your bucket from public sources: make stage RECIPE=blast
	@test -n "$(RECIPE)" || { echo "usage: make stage RECIPE=<name>"; exit 1; }
	@if [ -f recipes/$(RECIPE)/stage-inputs.sh ]; then \
	  bash recipes/$(RECIPE)/stage-inputs.sh $(COOKBOOK_BUCKET); \
	else echo "recipe '$(RECIPE)' builds its input in the task — no staging needed"; fi
	@env=$$(sed -n '2,/^---$$/s/^env_x86_64:[[:space:]]*//p' recipes/$(RECIPE)/README.md 2>/dev/null); \
	if [ -n "$$env" ] && { [ "$(ARCH)" = "x86_64" ] || [ "$(ARCH)" = "amd" ]; }; then bash scripts/stage_env_x86.sh "$$env" $(COOKBOOK_BUCKET); fi

spec: ## resolve a recipe's spec(s) for spawn and print the path(s); ARCH=x86_64 (Intel) or ARCH=amd for an x86 run: spawn task run --spec "$(make -s spec RECIPE=bwa-samtools)"
	@test -n "$(RECIPE)" || { echo "usage: make spec RECIPE=<name>" >&2; exit 1; }
	@test -n "$(AWS_ACCOUNT)" || { echo "AWS not configured — run 'make bootstrap' first" >&2; exit 1; }
	@ls recipes/$(RECIPE)/*.task.json >/dev/null 2>&1 || { echo "recipe '$(RECIPE)' has no TaskSpec — see its page for how it runs" >&2; exit 1; }
	@nonce="$$(date +%Y%m%d%H%M%S)"; \
	if [ -n "$(ARCH)" ] && [ "$(ARCH)" != "arm64" ]; then \
	  python3 scripts/spec_arch.py "$(RECIPE)" "$(ARCH)" "$(COOKBOOK_BUCKET)" "$$nonce"; exit $$?; \
	fi; \
	dir="$$(mktemp -d "$${TMPDIR:-/tmp}/cookbook-spec.XXXXXX")"; \
	for s in recipes/$(RECIPE)/*.task.json; do \
	  out="$$dir/$$(basename $$s .task.json).$$nonce.json"; \
	  sed -e 's|$${COOKBOOK_BUCKET}|$(COOKBOOK_BUCKET)|g' \
	      -e 's|\("task_id": "[^"]*\)"|\1-'"$$nonce"'"|' "$$s" > "$$out"; \
	  echo "$$out"; \
	done

run: ## run a recipe against your bucket (ARCH=x86_64 for the x86 run): make run RECIPE=r
	@test -n "$(RECIPE)" || { echo "usage: make run RECIPE=<name>"; exit 1; }
	@test -n "$(AWS_ACCOUNT)" || { echo "AWS not configured — run 'make bootstrap' first"; exit 1; }
	@nonce="$$(date +%Y%m%d%H%M%S)"; \
	if [ -n "$(ARCH)" ] && [ "$(ARCH)" != "arm64" ]; then \
	  specs="$$(python3 scripts/spec_arch.py "$(RECIPE)" "$(ARCH)" "$(COOKBOOK_BUCKET)" "$$nonce")" || exit 1; \
	  for out in $$specs; do \
	    echo "== $$out ($(ARCH))  →  s3://$(COOKBOOK_BUCKET)/runs-$(ARCH) (run $$nonce) =="; \
	    spawn task run --spec "$$out" --wait || exit 1; \
	  done; exit 0; \
	fi; \
	for spec in recipes/$(RECIPE)/*.task.json; do \
	  out="$$(mktemp -t cookbook.XXXXXX)"; \
	  sed -e 's|$${COOKBOOK_BUCKET}|$(COOKBOOK_BUCKET)|g' \
	      -e 's|\("task_id": "[^"]*\)"|\1-'"$$nonce"'"|' "$$spec" > "$$out"; \
	  echo "== $$spec  →  s3://$(COOKBOOK_BUCKET) (run $$nonce) =="; \
	  spawn task run --spec "$$out" --wait; \
	done

ls: ## list a recipe's outputs in your bucket: make ls RECIPE=r
	@test -n "$(RECIPE)" || { echo "usage: make ls RECIPE=<name>"; exit 1; }
	aws s3 ls s3://$(COOKBOOK_BUCKET)/runs$(if $(filter-out arm64,$(ARCH)),-$(ARCH))/$(RECIPE)/ --recursive
