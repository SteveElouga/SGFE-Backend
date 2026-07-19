# Makefile — cibles utilitaires (backend SGFE)
.PHONY: install-hooks hooks lint format

## Installe les hooks Git (pre-commit, commit-msg, pre-push)
install-hooks:
	./scripts/install-hooks.sh

## Exécute tous les hooks pre-commit sur l'ensemble du dépôt
hooks:
	pre-commit run --all-files

## Lint sans correction (ruff)
lint:
	ruff check .

## Formatage (ruff)
format:
	ruff format .
