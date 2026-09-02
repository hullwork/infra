PYTHON ?= scripts/infra-python.sh

.PHONY: help validate render render-applications bootstrap bootstrap-workload publish-rendered preflight validate-nodepool plan-nodepool test fresh-clone-test destroy

CATALOG ?= catalog/packages
STACK ?= examples/stacks/demo.yaml
PROFILE ?= examples/profiles/local.yaml
LOCK ?= versions.lock.yaml
NODEPOOL ?= examples/node-pools/local-workload.yaml
RENDERED_NAME ?= demo
RENDERED_FILE ?= /tmp/infra-applications.yaml


# There was no way to ask this repository what it can do except by reading this
# file, which is where a newcomer was trying not to start. Generated from the
# targets rather than kept as a second list: a hand-written listing and the
# targets it describes are two sources of truth, and they drift.
help: ## — list every target with its one-line description
	@grep -E '^[a-z][a-z0-9-]*:.*## — ' Makefile | sed 's/:.*## — / — /'

validate: ## — check contracts, the capability graph, and the version lock
	@$(PYTHON) scripts/infra.py validate \
		--catalog $(CATALOG) \
		--stack $(STACK) \
		--profile $(PROFILE) \
		--lock $(LOCK)

render: ## — render the ApplicationSet stream on stdout
	@$(PYTHON) scripts/infra.py render \
		--catalog $(CATALOG) \
		--stack $(STACK) \
		--profile $(PROFILE) \
		--lock $(LOCK) \
		--format applicationset

render-applications: ## — render plain Applications, easier to review
	@$(PYTHON) scripts/infra.py render \
		--catalog $(CATALOG) \
		--stack $(STACK) \
		--profile $(PROFILE) \
		--lock $(LOCK) \
		--format applications

bootstrap: ## — bring up the local GitOps management plane (no app package)
	@scripts/bootstrap.sh

bootstrap-workload: ## — bring up the local workload reference cluster
	@scripts/bootstrap-workload.sh

publish-rendered: ## — publish compiler output into the local GitOps repository
	@scripts/publish-rendered.sh "$(RENDERED_NAME)" "$(RENDERED_FILE)"

preflight: ## — check this host against the bootstrap requirements
	@scripts/preflight.sh

validate-nodepool: ## — check a node-pool declaration against its schema
	@$(PYTHON) scripts/nodepool.py validate --pool $(NODEPOOL)

plan-nodepool: ## — show what reconciling that node pool would change
	@$(PYTHON) scripts/nodepool.py plan --pool $(NODEPOOL)

test: ## — run the unit suite; no network, no cluster
	@$(PYTHON) -m unittest discover -s tests -v

fresh-clone-test: ## — clone, install, validate and render in a clean tree
	@scripts/test-fresh-clone.sh

destroy: ## — stop the local reference clusters, keeping their disks
	@scripts/destroy.sh
