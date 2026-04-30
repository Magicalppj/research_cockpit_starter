# Troubleshooting

Use this capability when validation, startup, packaging, or dependencies fail.

## Dependency Failure

If a command reports missing modules, install the package from the plugin root:

```sh
python -m pip install -e .
```

Or activate a Python environment that already has the package and its dependencies installed.

If `research-cockpit` is not visible on `PATH` but the package imports successfully, run the module entry point with the same interpreter:

```sh
python -m research_cockpit.cli validate --root /absolute/path/to/research_cockpit --json
```

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
research-cockpit bootstrap --root examples/demo_research_cockpit --build --json
research-cockpit validate --root examples/demo_research_cockpit --json
research-cockpit smoke --root examples/demo_research_cockpit --json
research-cockpit commands --json
git diff --check
```

If a mutating script is being tested, use a copied data root or `.test_tmp/`; do not mutate a user's real `research_cockpit/` during verification.

## Terminal Encoding

The Markdown files are UTF-8. If Chinese text appears garbled in a terminal, switch to a UTF-8 capable terminal before judging the file contents. On legacy Windows terminals this may require:

```sh
chcp 65001
```
