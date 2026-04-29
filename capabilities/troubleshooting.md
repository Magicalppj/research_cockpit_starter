# Troubleshooting

Use this capability when validation, startup, packaging, or dependencies fail.

## Dependency Failure

If a command reports missing modules, install from the plugin root:

```powershell
python -m pip install -r requirements.txt
```

Or run with a Python that already has the dependencies:

```powershell
$env:RESEARCH_COCKPIT_PYTHON="C:\path\to\python.exe"
```

## Validation

Run:

```powershell
python scripts\validate_cockpit.py --root research_cockpit --json
```

Common causes:

- `parent` points to a missing node.
- A parent's `children` list does not include the child node.
- A node status is not valid for its type.
- Decision checklist fields are missing or reference invalid option IDs.

## Release Checks

From the plugin repo root:

```powershell
python -m unittest discover -s tests
python scripts\skill_smoke_test.py --root examples\demo_research_cockpit --json
python dev\scripts\run_skill_release_check.py --json --skip-mutating
git diff --check
```

If a mutating script is being tested, use a copied data root or `.test_tmp/`; do not mutate a user's real `research_cockpit/` during verification.
