.PHONY: check check-links

check: ## frontmatter + markdown-a11y + internal links (offline, fast)
	@python3 scripts/check_pages.py

check-links: ## everything in `check`, plus external link liveness (network)
	@python3 scripts/check_pages.py --external
