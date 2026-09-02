# Third-party package example

This example composes the upstream cert-manager Helm chart with no first-party package
involved. It demonstrates that the compiler contract is product-neutral.

The chart location and version follow the upstream installation documentation:
https://cert-manager.io/docs/installation/helm/

Validate and render it with:

```bash
python scripts/infra.py validate \
  --catalog examples/third-party/catalog \
  --stack examples/third-party/stack.yaml \
  --profile examples/third-party/profile.yaml \
  --lock examples/third-party/versions.lock.yaml

python scripts/infra.py render \
  --catalog examples/third-party/catalog \
  --stack examples/third-party/stack.yaml \
  --profile examples/third-party/profile.yaml \
  --lock examples/third-party/versions.lock.yaml
```

This is a contract and render example, not an instruction to install cert-manager
without reviewing its current upstream security and upgrade guidance.
