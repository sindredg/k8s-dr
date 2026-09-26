# Short names for the operator commands in docs/runbooks/.
# Run `make -n <target>` to print a command without running it.
# Real identifiers are read from Terraform and gcloud at run time and are
# never stored here.

TF_DIR ?= infra/primary
SSH_KEY ?= $$HOME/.ssh/google_compute_engine
INVENTORY := ansible/inventory/generated/hosts.json
VENV := .venv
IAP := python3 scripts/run_with_iap.py --inventory $(INVENTORY) --
PLAYBOOK := $(VENV)/bin/ansible-playbook -i $(INVENTORY)
PLAYBOOKS := bootstrap deploy_test_app cleanup_test_app validate validate_cluster validate_test_app

.DEFAULT_GOAL := help
.PHONY: help venv inventory bootstrap validate-cluster validate deploy-test-app cleanup-test-app check pins

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

venv: ## Install the pinned controller toolchain
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r ansible/requirements.txt

inventory: ## Generate the ignored inventory from TF_DIR outputs
	python3 scripts/prepare_ansible_inventory.py --terraform-dir $(TF_DIR) \
		--ssh-user "$$(gcloud compute os-login describe-profile --format='value(posixAccounts[0].username)')" \
		--ssh-key "$(SSH_KEY)" --output $(INVENTORY)

bootstrap: ## Run bootstrap.yml through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/bootstrap.yml

validate-cluster: ## Run validate_cluster.yml through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/validate_cluster.yml -v

validate: ## Run validate.yml (cluster and test app) through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/validate.yml -v

deploy-test-app: ## Deploy the disposable application through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/deploy_test_app.yml

cleanup-test-app: ## Remove the disposable application through IAP
	$(IAP) $(PLAYBOOK) ansible/playbooks/cleanup_test_app.yml

check: ## Run the local unit tests, linters, and syntax checks
	$(VENV)/bin/python -m unittest discover -s tests
	$(VENV)/bin/yamllint -c ansible/.yamllint.yml ansible .github/workflows
	$(VENV)/bin/ansible-lint ansible
	for playbook in $(PLAYBOOKS); do \
		$(VENV)/bin/ansible-playbook -i 'localhost,' --syntax-check ansible/playbooks/$$playbook.yml || exit 1; \
	done

pins: ## Check that every pinned artifact still resolves upstream
	$(VENV)/bin/python scripts/check_pins.py
