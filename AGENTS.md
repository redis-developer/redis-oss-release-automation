# AGENTS.md

Guidance for coding agents working in this repository.

## Before finishing any change

Run what CI runs (`.github/workflows/validation.yml`), all three, on the whole
repository. Do this for every change, including one that only touches tests.

```
uv run --all-extras black --check --diff .
uv run --all-extras mypy .
uv run --all-extras pytest src/tests packages/redis-version/tests
```

- `mypy .` type checks the tests as well as the sources. Checking only the
  files you edited under `src/redis_release` misses errors in `src/tests`.
- A passing test is not enough. `pytest` does not report type errors, so a
  test can pass and still fail CI.
- Fields such as `Package.publish` are `Optional`. Narrow them with an
  `assert ... is not None` before use instead of silencing the checker.

## Adding a new package

A package is one entry under `packages:` in `config.yaml` plus the code that
decides how its release tree behaves. Treat every new package as a first-class
citizen.

### Principles

- **Own state.** Give the package its own `PackageType` value and, when it
  carries any package-specific field or ephemeral flag, its own `*Meta` and
  `*MetaEphemeral` classes deriving from `PackageMeta` and
  `PackageMetaEphemeral`. Its `serialization_hint` is its own name. State,
  logs and the status display must name the package, never another one.
- **Own behaviours and factory.** Put package-specific behaviours in
  `bht/behaviours_<package>.py` and the factory in
  `bht/tree_factory_<package>.py`, deriving from `GenericPackageFactory`.
- **Do not extend unrelated code.** Never add a flag, parameter or branch to
  another package's behaviours, factory or meta to serve the new package. A
  change to snap must not be able to break Arch Linux, and the reverse.
- **Inherit only for a shared model.** Derive from another package's class
  only when both hold together: the reuse is substantial, and the two packages
  share the same underlying model in a fundamental way, so a future change to
  one is expected to apply to the other. Similar-looking code is not enough.
  When in doubt, duplicate.
- **Extract shared code as its own change.** If several packages really do
  share a model, move it into the generic layer (`behaviours.py`,
  `tree_factory_generic.py`) under a package-neutral name, in a separate
  change that migrates the existing packages and keeps their tests passing
  unmodified.
- **Order the tree instead of re-deriving.** When one behaviour needs a value
  another one computes, run the producer earlier in the sequence and read the
  value from state.

### Touchpoints

1. `config.yaml`: the package entry.
2. `models.py`: `PackageType`.
3. `bht/state.py`: meta and ephemeral classes, both `meta` unions, the
   `Package` docstring, `_create_package_meta_from_config`.
4. `bht/behaviours_<package>.py`: behaviours.
5. `bht/composites.py`: guarded wrappers for the package's behaviours, if any.
6. `bht/tree_factory_<package>.py` and the registry in `bht/tree_factory.py`.
7. `state_display.py`: the display model for the package type.
8. `src/tests/test_<package>.py`: drive the real tree from
   `get_factory(PackageType.<PACKAGE>)` against the real `config.yaml` with a
   mocked GitHub client. Assert on what is dispatched and what is skipped, not
   on internal flags.

### Checking the result

Print the tree for the package and read it as a whole. Every node name should
make sense for this package.

```
GITHUB_TOKEN=$(gh auth token) uv run redis-release release-print <tag> --only-packages <package>
```

Then run the checks in "Before finishing any change".
