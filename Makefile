# Short names for the operator commands in docs/runbooks/.
# Run `make -n <target>` to print a command without running it.
# Real identifiers are read from Terraform and gcloud at run time and are
# never stored here.

# Selects the Terraform root, the generated inventory, the Flux cluster
# directory, and the default fixture host. Recovery commands set
# CLUSTER=recovery. See decision 0008.
CLUSTER ?= primary
TF_DIR ?= infra/$(CLUSTER)
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
# Endpoint for the fixture targets. Empty means git_host from the settings
# of CLUSTER; set git-dr.sindrg.com to reach a recovery cluster before the
# DNS cutover.
GIT_HOST ?=
# Full endpoint URL for the fixture targets, such as http://localhost:3000
# for a port-forward. It overrides GIT_HOST.
GIT_URL ?=
# One inventory per cluster, so a stale file cannot send a recovery command
# to the primary.
INVENTORY := ansible/inventory/generated/$(CLUSTER)/hosts.json
VENV := .venv
# CI installs tools into the system Python and runs `make check BIN=`.
BIN ?= $(VENV)/bin/
# gcloud compute ssh arguments for the control plane, read when a target uses them.
CONTROL_PLANE = $(shell python3 -c 'import json, sys; \
	h = json.load(open(sys.argv[1]))["all"]["children"]["kube_control_plane"]["hosts"]["control-plane"]; \
	print(h["gcp_instance_name"], "--zone", h["gcp_zone"], "--project", h["gcp_project"])' $(INVENTORY))
IAP := python3 scripts/run_with_iap.py --inventory $(INVENTORY) --
PLAYBOOK := $(BIN)ansible-playbook -i $(INVENTORY)
# The fixture playbooks run on the operator machine against the public endpoint.
FIXTURE_PLAYBOOK := SOPS_AGE_KEY_FILE="$(FLUX_AGE_KEY_FILE)" $(BIN)ansible-playbook -i localhost, \
	-e fixture_cluster=$(CLUSTER) $(if $(GIT_HOST),-e git_host=$(GIT_HOST)) $(if $(GIT_URL),-e git_url=$(GIT_URL))
PLAYBOOKS := bootstrap validate_cluster validate_services create_fixtures check_fixtures write_check restore restore_test_env

.DEFAULT_GOAL := help
.PHONY: help venv inventory age-key bootstrap validate-cluster validate-services \
	create-fixtures check-fixtures write-check restore restore-test-env \
	restore-test-env-delete restore-test-forward check pins surface

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

venv: ## Install the pinned controller toolchain
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r ansible/requirements.txt

inventory: ## Generate the ignored inventory of CLUSTER from TF_DIR outputs
	$(BIN)python scripts/prepare_ansible_inventory.py --terraform-dir $(TF_DIR) \
		--ssh-user "$$(gcloud compute os-login describe-profile --format='value(posixAccounts[0].username)')" \
		--ssh-key "$(SSH_KEY)" --output $(INVENTORY)

age-key:
	@test -r "$(FLUX_AGE_KEY_FILE)" || { echo "FLUX_AGE_KEY_FILE not readable: $(FLUX_AGE_KEY_FILE)" >&2; exit 1; }

bootstrap: age-key ## Run bootstrap.yml through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/bootstrap.yml \
		-e sops_age_key_file="$(FLUX_AGE_KEY_FILE)" -e flux_git_branch="$(FLUX_GIT_BRANCH)" \
		-e flux_cluster=$(CLUSTER)

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

restore-test-env: ## Create the test restore namespaces through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/restore_test_env.yml

restore-test-env-delete: ## Delete the test restore namespaces and their data through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/restore_test_env.yml -e restore_test_env_state=absent

# Holds the terminal. Run the fixture targets with GIT_URL=http://localhost:3000
# from a second terminal, then stop this with Ctrl+C.
restore-test-forward: ## Forward localhost:3000 to the test restore Gitea through IAP
	gcloud compute ssh $(CONTROL_PLANE) --tunnel-through-iap -- -L 3000:127.0.0.1:3000 \
		sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl --namespace gitea-restore \
		port-forward service/gitea-http 3000:3000

check: ## Run the local unit tests, linters, and syntax checks
	$(BIN)python -m unittest discover -s tests
	$(BIN)yamllint -c ansible/.yamllint.yml ansible deploy recovery .sops.yaml .github/workflows
	$(BIN)ansible-lint ansible
	for playbook in $(PLAYBOOKS); do \
		$(BIN)ansible-playbook -i 'localhost,' --syntax-check ansible/playbooks/$$playbook.yml || exit 1; \
	done

pins: ## Check that every pinned artifact still resolves upstream
	$(BIN)python scripts/check_pins.py

surface: ## Probe the public endpoint from outside (read-only)
	scripts/check-public-surface.sh $(GIT_HOST)
