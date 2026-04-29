# Troubleshooting

Use this capability when validation, startup, packaging, or dependencies fail.

## Dependency Failure

If a command reports missing modules, install the package from the plugin root:

```sh
python -m pip install -e .
```

Or activate a Python environment that already has the package and its dependencies installed.

## Validation

Run:

```sh
research-cockpit validate --root research_cockpit --json
```

Common causes:

- `parent` points to a missing node.
- A parent's `children` list does not include the child node.
- A node status is not valid for its type.
- Decision checklist fields are missing or reference invalid option IDs.

## Release Checks

From the plugin repo root:

```sh
python -m unittest discover -s tests
research-cockpit smoke --root examples/demo_research_cockpit --json
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

If a mutating script is being tested, use a copied data root or `.test_tmp/`; do not mutate a user's real `research_cockpit/` during verification.
