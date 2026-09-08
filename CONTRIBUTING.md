# Contributing to infra

Thank you for helping improve infra. The project is alpha. Documentation fixes,
reproducible bug reports and focused pull requests are welcome.

By submitting a contribution, you agree that it may be distributed under the
repository's MIT License. This project does not currently require a contributor
license agreement.

## Start with the project boundary

The primary project is an application-neutral, controller-free GitOps compiler. Its
portable API consists of the `Package`, `Stack`, `ClusterProfile`, and
`VersionLock` contracts and deterministic Argo CD output.

The optional Lima/kubeadm management reference is experimental, not the package
API or a production installer. Avoid adding application-specific branches or
external source-repository dependencies to the compiler. Application behavior
belongs in package descriptors, capabilities, values, and immutable locks.

## Proposing a change

- Search existing issues and pull requests before starting work.
- Open an issue before a large feature, contract change, or architectural change
  so the compatibility and ownership implications can be discussed.
- Keep pull requests focused. Separate unrelated refactoring, generated content,
  and behavior changes.
- Do not include credentials, kubeconfigs, private endpoints, customer data, or
  local secret state in an issue, test fixture, commit, or pull request.
- Report security vulnerabilities using `SECURITY.md`, not a public issue.

Small documentation corrections and narrowly scoped bug fixes may be submitted
directly as pull requests.

## Development setup

Use Python 3.10+ and an isolated virtual environment. See the
[compatibility matrix](docs/COMPATIBILITY.md) for verified versions:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export PYTHON=.venv/bin/python
make validate
make render
make render-applications
make test
make fresh-clone-test
```

The default targets exercise the application-neutral compiler core. A successful render
does not prove that Argo CD synchronized it or that a target cluster, network, or
storage system is healthy.

## Contract and package changes

When changing schemas, the compiler, package metadata, or release import:

- preserve deterministic output and fail closed on floating or incomplete
  artifact identities;
- add positive and negative tests for validation behavior;
- update examples and documentation in the same pull request;
- describe compatibility impact for existing `v1alpha1` declarations;
- avoid credentials and environment-specific addresses in package descriptors;
- keep runtime-image digest locks stronger than environment overrides; and
- explain any generated Argo CD permissions or cluster-wide resources.

Breaking contract changes are possible during alpha, but they must be explicit,
reviewable, and accompanied by migration guidance. Do not silently reinterpret an
existing field.

## Pull request checklist

Before requesting review:

- run `make validate`, `make render`, `make test`, and `make fresh-clone-test`;
- inspect generated ApplicationSet and Application output;
- run any focused checks relevant to changed local-reference or example content;
- confirm `git diff --check` succeeds;
- document tests that were not run and any required live-cluster validation;
- add release notes for user-visible or contract behavior changes; and
- keep the branch free of unrelated local or generated files.

Pull requests should state separately what was verified in source tests, CI,
artifact publication, Argo CD reconciliation, and live runtime testing. Evidence
from one gate does not substitute for another.

## Review and conduct

Maintainers may request changes for security, compatibility, portability, or
scope. Approval and merge timing are best effort during alpha. All participation
is governed by `CODE_OF_CONDUCT.md`.
