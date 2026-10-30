# packages/agentctl

The PoC-9 agent factory: `agentctl new <name> --kind ... --framework ... --connector ...` renders the Copier template in `src/agentctl/template/` into a new folder, then runs `git init -b main` and one commit. Usage is in `README.md`.

- agentctl never imports `chassis` (import-linter). Its only dependency is `copier`, pinned exactly. Tests may import the chassis to check the rendered config loads.
- The trust rule lives in `rules.py` and mirrors `ChassisConfig._lane_fits_profile`; `test_agentctl_config.py` checks the two agree. ADR-006 names the untrusted engines.
- The template variables are fixed, other PoC-9 parts rely on them: `agent_name`, `package_name`, `kind`, `framework`, `connector`, `trust`, `platform_path`.
- `template/_frameworks/<framework>/` is the example business logic: byte copies of `packages/workloads/echo-<framework>/src` with the module name replaced by `{{ package_name }}`. `test_agentctl_render.py` fails when they drift; copy the echo file again.
- Every Python file in the template ends in `.jinja`, so the monorepo's ruff, mypy, and pytest never read it. `test_agentctl_rendered_checks.py` runs ruff and the rendered unit tests on each framework instead.
- `template/project/` also holds `deploy/compose/`, `.github/`, `.gitlab-ci.yml`, `.vscode/`, and `docs/`. Their tests are `pocs/poc-09-agent-mvp-template/tests/test_poc09_template_ops.py` and `test_poc09_docs.py`. Every `make` target the template CI calls must exist in `Makefile.jinja` (`test_every_target_the_ci_calls_is_in_the_makefile`).
- `agentctl new` pins the agent to the platform: it exports the platform's `uv.lock` as `[tool.uv] constraint-dependencies` and runs `uv lock` before the first commit (`scaffold.pin_to_platform`). Unit tests pass `--no-lock`; the PoC-9 scaffold test locks with `UV_OFFLINE=1`.
- Tests start with the package name. They render into `tmp_path` and run git with no user config.
