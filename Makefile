# Short names for the operator commands in docs/runbooks/.
# Run `make -n <target>` to print a command without running it.
# Real identifiers are read from Terraform and gcloud at run time and are
# never stored here.

TF_DIR ?= infra/primary
SSH_KEY ?= $$HOME/.ssh/google_compute_engine
# Age private key that decrypts the SOPS files. Flux receives it at bootstrap;
# the fixture targets use it locally. Keep it outside the repository and in the
# recovery credential store.
FLUX_AGE_KEY_FILE ?= $(HOME)/.config/k8s-dr/age.agekey
# Backup age private key for restores. Keep it outside the repository and in
# the recovery credential store. The cluster holds it only during a restore.
BACKUP_AGE_KEY_FILE ?= $(HOME)/.config/k8s-dr/backup.agekey
# Restore target: gitea on a recovery cluster, gitea-restore for a test
# restore. It has no default, so a restore cannot replace the live service by
# mistake. RESTORE_SET is a set name such as 20260929T140701Z; empty selects
# the newest complete set.
RESTORE_NAMESPACE ?=
RESTORE_SET ?=
# Branch Flux reconciles. Override it to test a pushed branch before merging.
FLUX_GIT_BRANCH ?= main
# Endpoint for the fixture targets. Empty means git_host from the primary
# cluster settings; a drill sets the recovery host.
GIT_HOST ?=
INVENTORY := ansible/inventory/generated/hosts.json
VENV := .venv
# CI installs tools into the system Python and runs `make check BIN=`.
BIN ?= $(VENV)/bin/
IAP := python3 scripts/run_with_iap.py --inventory $(INVENTORY) --
PLAYBOOK := $(BIN)ansible-playbook -i $(INVENTORY)
# The fixture playbooks run on the operator machine against the public endpoint.
FIXTURE_PLAYBOOK := SOPS_AGE_KEY_FILE="$(FLUX_AGE_KEY_FILE)" $(BIN)ansible-playbook -i localhost, \
	$(if $(GIT_HOST),-e git_host=$(GIT_HOST))
PLAYBOOKS := bootstrap validate_cluster validate_services create_fixtures check_fixtures write_check restore

.DEFAULT_GOAL := help
.PHONY: help venv inventory age-key bootstrap validate-cluster validate-services \
	create-fixtures check-fixtures write-check restore check pins

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

venv: ## Install the pinned controller toolchain
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r ansible/requirements.txt

inventory: ## Generate the ignored inventory from TF_DIR outputs
	$(BIN)python scripts/prepare_ansible_inventory.py --terraform-dir $(TF_DIR) \
		--ssh-user "$$(gcloud compute os-login describe-profile --format='value(posixAccounts[0].username)')" \
		--ssh-key "$(SSH_KEY)" --output $(INVENTORY)

age-key:
	@test -r "$(FLUX_AGE_KEY_FILE)" || { echo "FLUX_AGE_KEY_FILE not readable: $(FLUX_AGE_KEY_FILE)" >&2; exit 1; }

bootstrap: age-key ## Run bootstrap.yml through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/bootstrap.yml \
		-e sops_age_key_file="$(FLUX_AGE_KEY_FILE)" -e flux_git_branch="$(FLUX_GIT_BRANCH)"

validate-cluster: ## Run validate_cluster.yml through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/validate_cluster.yml -v

validate-services: ## Run validate_services.yml (Flux-owned services) through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/validate_services.yml -v

create-fixtures: age-key ## Create the recovery fixtures, then check them
	$(FIXTURE_PLAYBOOK) ansible/playbooks/create_fixtures.yml

check-fixtures: age-key ## Check the recovery fixtures (read-only)
	$(FIXTURE_PLAYBOOK) ansible/playbooks/check_fixtures.yml

write-check: age-key ## Push a commit as the fixture user; print its UTC time
	$(FIXTURE_PLAYBOOK) ansible/playbooks/write_check.yml

restore: ## Restore a backup set into RESTORE_NAMESPACE through IAP
	@test -n "$(RESTORE_NAMESPACE)" || { echo "Set RESTORE_NAMESPACE to gitea or gitea-restore" >&2; exit 1; }
	@test -r "$(BACKUP_AGE_KEY_FILE)" || { echo "BACKUP_AGE_KEY_FILE not readable: $(BACKUP_AGE_KEY_FILE)" >&2; exit 1; }
	$(IAP) $(PLAYBOOK) ansible/playbooks/restore.yml -e restore_namespace="$(RESTORE_NAMESPACE)" \
		-e restore_set="$(RESTORE_SET)" -e backup_age_key_file="$(BACKUP_AGE_KEY_FILE)"

check: ## Run the local unit tests, linters, and syntax checks
	$(BIN)python -m unittest discover -s tests
	$(BIN)yamllint -c ansible/.yamllint.yml ansible deploy recovery .sops.yaml .github/workflows
	$(BIN)ansible-lint ansible
	for playbook in $(PLAYBOOKS); do \
		$(BIN)ansible-playbook -i 'localhost,' --syntax-check ansible/playbooks/$$playbook.yml || exit 1; \
	done

pins: ## Check that every pinned artifact still resolves upstream
	$(BIN)python scripts/check_pins.py
