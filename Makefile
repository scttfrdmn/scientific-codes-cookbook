.PHONY: check check-links catalog bootstrap stage spec run ls print-bucket

# Your cookbook bucket — holds staged inputs and run outputs, in YOUR account.
# Override with `make run COOKBOOK_BUCKET=my-bucket` if you want a different name.
AWS_ACCOUNT     := $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
AWS_REGION      := $(shell aws configure get region 2>/dev/null)
COOKBOOK_BUCKET ?= cookbook-$(AWS_ACCOUNT)-$(AWS_REGION)

check: ## frontmatter + markdown-a11y + internal links + spec portability + catalog freshness
	@python3 scripts/check_pages.py
	@python3 scripts/gen_catalog.py --check

catalog: ## regenerate catalog/recipes.md from the recipes' frontmatter + dependencies
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

spec: ## resolve a recipe's spec(s) for spawn and print the path(s): spawn task run --spec "$(make -s spec RECIPE=bwa-samtools)"
	@test -n "$(RECIPE)" || { echo "usage: make spec RECIPE=<name>" >&2; exit 1; }
	@test -n "$(AWS_ACCOUNT)" || { echo "AWS not configured — run 'make bootstrap' first" >&2; exit 1; }
	@ls recipes/$(RECIPE)/*.task.json >/dev/null 2>&1 || { echo "recipe '$(RECIPE)' has no TaskSpec — see its page for how it runs" >&2; exit 1; }
	@nonce="$$(date +%Y%m%d%H%M%S)"; \
	for s in recipes/$(RECIPE)/*.task.json; do \
	  out="$${TMPDIR:-/tmp}/$$(basename $$s .task.json).$$nonce.json"; \
	  sed -e 's|$${COOKBOOK_BUCKET}|$(COOKBOOK_BUCKET)|g' \
	      -e 's|\("task_id": "[^"]*\)"|\1-'"$$nonce"'"|' "$$s" > "$$out"; \
	  echo "$$out"; \
	done

run: ## run a recipe against your bucket: make run RECIPE=r
	@test -n "$(RECIPE)" || { echo "usage: make run RECIPE=<name>"; exit 1; }
	@test -n "$(AWS_ACCOUNT)" || { echo "AWS not configured — run 'make bootstrap' first"; exit 1; }
	@nonce="$$(date +%Y%m%d%H%M%S)"; \
	for spec in recipes/$(RECIPE)/*.task.json; do \
	  out="$$(mktemp -t cookbook.XXXXXX)"; \
	  sed -e 's|$${COOKBOOK_BUCKET}|$(COOKBOOK_BUCKET)|g' \
	      -e 's|\("task_id": "[^"]*\)"|\1-'"$$nonce"'"|' "$$spec" > "$$out"; \
	  echo "== $$spec  →  s3://$(COOKBOOK_BUCKET) (run $$nonce) =="; \
	  spawn task run --spec "$$out" --wait; \
	done

ls: ## list a recipe's outputs in your bucket: make ls RECIPE=r
	@test -n "$(RECIPE)" || { echo "usage: make ls RECIPE=<name>"; exit 1; }
	aws s3 ls s3://$(COOKBOOK_BUCKET)/runs/$(RECIPE)/ --recursive
