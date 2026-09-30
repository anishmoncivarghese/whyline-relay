- [x] CRS-1: `planner.validate`, `planner.approve`, `PlanExists`

  ## Global Constraints

  - Python `>=3.11`; use `tomllib` for reading TOML. Add no new dependencies to either package.
  - whyline must require `whyline-relay>=0.2.26,<0.3` (both places it appears in `pyproject.toml`).
  - Every commit the relay or console makes on the user's behalf is scoped to its own files (`gitcheck.commit_paths`), never `commit_all` / `git add -A`.
  - Relay role dropdowns list `sorted(whyline_relay.adapters.BUILTIN)` — today `["claude", "codex"]`. Do not hard-code Antigravity or Grok.
  - Console code imports `whyline_relay` lazily inside functions (the existing pattern in `src/whyline/console/adapters.py`), so the console still imports without it.
  - Console widget lookups on the main screen go through `WhylineConsoleApp._main(selector, type)`, never `App.query_one` (a dialog may be on top).
  - Relay output goes to `.whyline/relay/logs/console-run.log` (already git-ignored by the relay under `.whyline/relay/logs/`), never a pipe.
  - Plan and Set up refuse in the home-directory repo with the existing `_HOME_REFUSAL` text from `src/whyline/console/repl.py`.
  - After each task, record the decision per `AGENTS.md`:
    `whyline note "<decision>" --because "<why>" --file <path> --actor <your agent name> --role implementer --task CRS-<task number>`.
  - Commit messages end with a blank line and your own `Co-Authored-By:` line if your harness adds one. Do not push or tag except in Tasks 4 and 13.

  **Files:**
  - Modify: `src/whyline_relay/planner.py` (add near the top-level helpers; rebuild `review_gate`'s approve branch)
  - Test: `tests/test_planner_api.py` (create)

  **Interfaces:**
  - Consumes: `plan.parse(text) -> list[plan.Task]` (raises `plan.PlanError`), `gitcheck.commit_paths(root, paths, message) -> bool`, `state.clear_plan(root)`.
  - Produces:
    - `class PlanExists(RuntimeError)`
    - `validate(text: str) -> list[str]` — empty list when valid.
    - `approve(root: Path, settings: config.Config, draft_path: Path, *, drafted_by: str, replace: bool = False, clear_checkpoint: bool = False) -> Path` — returns `root / settings.plan`. Raises `plan.PlanError` (invalid text) or `PlanExists`.

  Step 1: Write the failing tests

  Create `tests/test_planner_api.py`:

  ```python
  import subprocess
  from pathlib import Path

  import pytest

  from whyline_relay import config, plan, planner, state


  def _git(root: Path, *args: str) -> str:
      return subprocess.run(
          ["git", *args], cwd=root, check=True, capture_output=True, text=True
      ).stdout


  @pytest.fixture
  def repo(tmp_path: Path) -> Path:
      _git(tmp_path, "init", "-q", "-b", "main")
      _git(tmp_path, "config", "user.email", "t@example.com")
      _git(tmp_path, "config", "user.name", "T")
      (tmp_path / "README.md").write_text("x\n")
      _git(tmp_path, "add", "-A")
      _git(tmp_path, "commit", "-qm", "initial")
      return tmp_path


  def test_validate_accepts_a_real_plan():
      assert planner.validate("- [ ] T-1: build it\n  Do the thing.\n") == []


  def test_validate_accepts_crlf_and_no_trailing_newline():
      assert planner.validate("- [ ] T-1: build it\r\n  Do it.\r\n- [ ] T-2: more") == []


  @pytest.mark.parametrize("text", ["", "   \n", "Just some prose about the plan.\n"])
  def test_validate_rejects_text_with_no_tasks(text):
      problems = planner.validate(text)
      assert problems and "no tasks" in problems[0]


  def test_validate_reports_duplicate_ids():
      problems = planner.validate("- [ ] T-1: a\n- [ ] T-1: b\n")
      assert problems and "duplicate task id" in problems[0]


  def test_approve_writes_and_commits_only_plan_md(repo: Path):
      (repo / "unrelated.txt").write_text("keep me uncommitted\n")
      draft = repo / ".whyline" / "relay" / "d.md"
      draft.parent.mkdir(parents=True)
      draft.write_text("- [ ] T-1: x\n  y.\n")
      target = planner.approve(repo, config.load(repo), draft, drafted_by="codex")
      assert target == repo / "plan.md"
      assert target.read_text() == "- [ ] T-1: x\n  y.\n"
      assert _git(repo, "log", "-1", "--format=%s").strip() == "docs: add plan drafted by codex"
      assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["plan.md"]
      assert "unrelated.txt" in _git(repo, "status", "--porcelain")


  def test_approve_refuses_to_replace_without_permission(repo: Path):
      (repo / "plan.md").write_text("- [ ] OLD-1: old\n")
      draft = repo / "d.md"
      draft.write_text("- [ ] T-1: new\n")
      with pytest.raises(planner.PlanExists):
          planner.approve(repo, config.load(repo), draft, drafted_by="codex")
      assert (repo / "plan.md").read_text() == "- [ ] OLD-1: old\n"
      planner.approve(repo, config.load(repo), draft, drafted_by="codex", replace=True)
      assert (repo / "plan.md").read_text() == "- [ ] T-1: new\n"


  def test_approve_rejects_an_invalid_draft(repo: Path):
      draft = repo / "d.md"
      draft.write_text("no tasks here\n")
      with pytest.raises(plan.PlanError, match="no tasks"):
          planner.approve(repo, config.load(repo), draft, drafted_by="codex")
      assert not (repo / "plan.md").exists()


  def test_approve_can_clear_the_planner_checkpoint(repo: Path):
      state.save_plan(repo, state.PlanState(
          description="d", stage="@complete", round=1, stage_visits={}, agent="",
          feedback="", draft_path=str(planner.draft_path(repo)), paused_reason="",
          log_path="",
      ))
      draft = repo / "d.md"
      draft.write_text("- [ ] T-1: x\n")
      planner.approve(repo, config.load(repo), draft, drafted_by="codex", clear_checkpoint=True)
      assert state.load_plan(repo) is None
  ```

  Step 2: Run the tests to verify they fail

  Run: `uv run pytest tests/test_planner_api.py -q`
  Expected: FAIL with `AttributeError: module 'whyline_relay.planner' has no attribute 'validate'`.

  Step 3: Implement

  In `src/whyline_relay/planner.py`, below `class PlanAlreadyInProgress`, add:

  ```python
  class PlanExists(RuntimeError):
      """plan.md already exists and the caller did not ask to replace it."""


  def validate(text: str) -> list[str]:
      """Problems that stop `text` being used as a plan; empty when it's fine.
      Prose with no checkboxes parses as an empty list, so that is a problem
      too."""
      try:
          tasks = plan.parse(text)
      except plan.PlanError as error:
          return [str(error)]
      if not tasks:
          return ["no tasks found -- write each task as `- [ ] ID: title`"]
      return []


  def approve(
      root: Path,
      settings: config.Config,
      draft_path: Path,
      *,
      drafted_by: str,
      replace: bool = False,
      clear_checkpoint: bool = False,
  ) -> Path:
      """Writes the draft as the plan and commits only that file. Raises
      plan.PlanError for a draft that isn't a usable plan, and PlanExists when
      a plan is already there and `replace` is false."""
      text = draft_path.read_text(encoding="utf-8")
      problems = validate(text)
      if problems:
          raise plan.PlanError("; ".join(problems))
      target = root / settings.plan
      if target.exists() and not replace:
          raise PlanExists(f"{target} already exists")
      target.write_text(text, encoding="utf-8")
      gitcheck.commit_paths(root, [target], f"docs: add plan drafted by {drafted_by}")
      if clear_checkpoint:
          state.clear_plan(root)
      return target
  ```

  Then in `review_gate`, replace everything from `target = root / settings.plan` down to and including `on_settled()` (the approve branch, just before the `start_now` prompt) with:

  ```python
      target = root / settings.plan
      replace = False
      if target.exists():
          try:
              overwrite = confirm(f"{target} already exists. Replace it? [y/N] ").strip().lower()
          except EOFError:
              overwrite = "n"
          if not overwrite.startswith("y"):
              return f"Not approved: {target} already exists and was not replaced."
          replace = True
      try:
          approve(root, settings, draft_path, drafted_by=drafted_by, replace=replace)
      except plan.PlanError as error:
          return f"Not approved: {error}. The draft is still at {draft_path}."
      on_settled()
  ```

  Leave the `start_now` prompt and everything after it unchanged. Change nothing else.

  Step 4: Run the new tests and the existing planner tests

  Run: `uv run pytest tests/test_planner_api.py tests/test_planner.py tests/test_planner_cli.py -q`
  Expected: all PASS.

  Step 5: Commit

  ```bash
  git add src/whyline_relay/planner.py tests/test_planner_api.py
  git commit -m "feat(planner): validate and approve a plan without prompting"
  ```

- [x] CRS-2: `planner.draft`, `planner.revise`, `planner.resume_draft` with progress

  ## Global Constraints

  - Python `>=3.11`; use `tomllib` for reading TOML. Add no new dependencies to either package.
  - whyline must require `whyline-relay>=0.2.26,<0.3` (both places it appears in `pyproject.toml`).
  - Every commit the relay or console makes on the user's behalf is scoped to its own files (`gitcheck.commit_paths`), never `commit_all` / `git add -A`.
  - Relay role dropdowns list `sorted(whyline_relay.adapters.BUILTIN)` — today `["claude", "codex"]`. Do not hard-code Antigravity or Grok.
  - Console code imports `whyline_relay` lazily inside functions (the existing pattern in `src/whyline/console/adapters.py`), so the console still imports without it.
  - Console widget lookups on the main screen go through `WhylineConsoleApp._main(selector, type)`, never `App.query_one` (a dialog may be on top).
  - Relay output goes to `.whyline/relay/logs/console-run.log` (already git-ignored by the relay under `.whyline/relay/logs/`), never a pipe.
  - Plan and Set up refuse in the home-directory repo with the existing `_HOME_REFUSAL` text from `src/whyline/console/repl.py`.
  - After each task, record the decision per `AGENTS.md`:
    `whyline note "<decision>" --because "<why>" --file <path> --actor <your agent name> --role implementer --task CRS-<task number>`.
  - Commit messages end with a blank line and your own `Co-Authored-By:` line if your harness adds one. Do not push or tag except in Tasks 4 and 13.

  **Files:**
  - Modify: `src/whyline_relay/planner.py` (`_run_pipeline` gains `on_stage`; three new functions)
  - Test: `tests/test_planner_api.py` (append)

  **Interfaces:**
  - Consumes: `_run_pipeline`, `state.load_plan`, `draft_path(root)`, `PlanAlreadyInProgress` (Task 1 file).
  - Produces:
    - `class NoPlanInProgress(RuntimeError)`
    - `draft(root, settings, description, *, print_fn=None, runner=subprocess.run) -> Path`
    - `revise(root, settings, feedback, *, print_fn=None, runner=subprocess.run) -> Path`
    - `resume_draft(root, settings, *, print_fn=None, runner=subprocess.run) -> Path`
    - `pending_description(root) -> str | None`
    - progress lines: `"<agent> is drafting the plan"` and `"<agent> is reviewing the draft"`.

  Step 1: Write the failing tests

  Append to `tests/test_planner_api.py`:

  ```python
  import sys

  from whyline_relay import loop

  FAKE = str(Path(__file__).parent / "fake_pipeline_agent.py")


  def _settings(root: Path) -> config.Config:
      base = config.load(root)
      return config.Config(
          plan=base.plan, max_rounds=base.max_rounds,
          timeout_minutes=base.timeout_minutes, branch_prefix=base.branch_prefix,
          agents={"codex": ["codex"], "claude": ["claude"]},
          status_map=base.status_map,
          planner=config.PlannerConfig(draft="codex", review="claude", max_visits=3),
      )


  def _scripted(repo: Path, specs: list[str], prompts: list[str]):
      """Each turn pops one "to_actor:status" spec; the drafting agent (codex)
      also writes the draft file, like a real one would."""
      def run(command, prompt, *, cwd, log_path, timeout_seconds, which=None,
              echo=True, agent_name=None):
          prompts.append(prompt)
          if agent_name == "codex":
              planner.draft_path(repo).write_text("- [ ] T-1: build it\n")
          result = subprocess.run(
              [sys.executable, FAKE, specs.pop(0), str(cwd), prompt],
              cwd=cwd, capture_output=True, text=True,
          )
          log_path.parent.mkdir(parents=True, exist_ok=True)
          log_path.write_text(result.stdout + result.stderr)
          return result.returncode
      return run


  @pytest.fixture
  def quiet_whyline(monkeypatch):
      monkeypatch.setattr(loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")
      monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
      monkeypatch.setattr(planner.whylinecmd, "claim", lambda *a, **k: None)


  def test_draft_returns_the_draft_and_reports_each_stage(repo, monkeypatch, quiet_whyline):
      prompts, lines = [], []
      monkeypatch.setattr(loop.agents, "run",
                          _scripted(repo, ["claude:ready", "claude:approved"], prompts))
      path = planner.draft(repo, _settings(repo), "a health check", print_fn=lines.append)
      assert path == planner.draft_path(repo)
      assert path.read_text() == "- [ ] T-1: build it\n"
      assert lines == ["codex is drafting the plan", "claude is reviewing the draft"]
      assert planner.pending_description(repo) == "a health check"


  def test_draft_refuses_while_another_draft_is_checkpointed(repo, monkeypatch, quiet_whyline):
      monkeypatch.setattr(loop.agents, "run",
                          _scripted(repo, ["claude:ready", "claude:approved"], []))
      planner.draft(repo, _settings(repo), "first")
      with pytest.raises(planner.PlanAlreadyInProgress):
          planner.draft(repo, _settings(repo), "second")


  def test_revise_sends_the_feedback_to_the_drafting_agent(repo, monkeypatch, quiet_whyline):
      prompts = []
      monkeypatch.setattr(loop.agents, "run", _scripted(
          repo, ["claude:ready", "claude:approved", "claude:ready", "claude:approved"], prompts))
      planner.draft(repo, _settings(repo), "a health check")
      planner.revise(repo, _settings(repo), "split T-1 into two tasks")
      assert "split T-1 into two tasks" in prompts[2]


  def test_revise_without_a_draft_says_so(repo):
      with pytest.raises(planner.NoPlanInProgress):
          planner.revise(repo, _settings(repo), "anything")


  def test_resume_draft_at_complete_runs_no_agent(repo, monkeypatch, quiet_whyline):
      monkeypatch.setattr(loop.agents, "run",
                          _scripted(repo, ["claude:ready", "claude:approved"], []))
      planner.draft(repo, _settings(repo), "a health check")
      monkeypatch.setattr(loop.agents, "run", lambda *a, **k: pytest.fail("ran an agent"))
      assert planner.resume_draft(repo, _settings(repo)) == planner.draft_path(repo)


  def test_pending_description_is_none_without_a_draft(repo):
      assert planner.pending_description(repo) is None
  ```

  Step 2: Run the tests to verify they fail

  Run: `uv run pytest tests/test_planner_api.py -q`
  Expected: the six new tests FAIL with `AttributeError: ... has no attribute 'draft'` (Task 1 tests still pass).

  Step 3: Implement

  In `_run_pipeline`'s signature, after `runner: failover.Runner = subprocess.run,` add:

  ```python
      on_stage=None,
  ```

  Inside its `while True:` loop, immediately before `target = loop.run_agent(`, add:

  ```python
          if on_stage is not None:
              on_stage(stage.id, agent)
  ```

  Below `discard`, add:

  ```python
  class NoPlanInProgress(RuntimeError):
      """There is no checkpointed plan draft to revise or resume."""


  _STAGE_WORDS = {"draft": "drafting the plan", "review": "reviewing the draft"}


  def _announcer(print_fn):
      if print_fn is None:
          return None
      return lambda stage_id, agent: print_fn(
          f"{agent} is {_STAGE_WORDS.get(stage_id, stage_id)}"
      )


  def pending_description(root: Path) -> str | None:
      """What the checkpointed draft was asked to build, or None."""
      saved = state.load_plan(root)
      return saved.description if saved is not None else None


  def draft(
      root: Path,
      settings: config.Config,
      description: str,
      *,
      print_fn=None,
      runner: failover.Runner = subprocess.run,
  ) -> Path:
      """Runs the draft<->review pipeline with no terminal gate and returns the
      draft's path. The checkpoint stays until approve(clear_checkpoint=True)
      or discard()."""
      if state.load_plan(root) is not None:
          raise PlanAlreadyInProgress(
              "a plan draft is already in progress; resume it or discard it first"
          )
      _run_pipeline(root, settings, description, echo=False, runner=runner,
                    on_stage=_announcer(print_fn))
      return draft_path(root)


  def revise(
      root: Path,
      settings: config.Config,
      feedback: str,
      *,
      print_fn=None,
      runner: failover.Runner = subprocess.run,
  ) -> Path:
      """Re-drafts with a human's requested change, then re-reviews."""
      saved = state.load_plan(root)
      if saved is None:
          raise NoPlanInProgress("no plan draft is in progress")
      _run_pipeline(
          root, settings, saved.description,
          current_stage_id="draft", round_=1, stage_visits={"draft": 1},
          feedback=feedback, echo=False, runner=runner,
          on_stage=_announcer(print_fn),
      )
      return draft_path(root)


  def resume_draft(
      root: Path,
      settings: config.Config,
      *,
      print_fn=None,
      runner: failover.Runner = subprocess.run,
  ) -> Path:
      """Finishes a checkpointed draft (a crash, or a closed console) without
      the terminal gate."""
      saved = state.load_plan(root)
      if saved is None:
          raise NoPlanInProgress("no plan draft is in progress")
      if saved.stage != "@complete":
          _run_pipeline(
              root, settings, saved.description,
              current_stage_id=saved.stage, round_=saved.round,
              stage_visits=saved.stage_visits, feedback=saved.feedback,
              consult_handoff=True, echo=False, runner=runner,
              on_stage=_announcer(print_fn),
          )
      return draft_path(root)
  ```

  Change nothing else.

  Step 4: Run the tests

  Run: `uv run pytest tests/test_planner_api.py tests/test_planner.py -q`
  Expected: all PASS.

  Step 5: Commit

  ```bash
  git add src/whyline_relay/planner.py tests/test_planner_api.py
  git commit -m "feat(planner): draft, revise and resume a plan without prompting"
  ```

- [ ] CRS-3: `setup.write_roles` with a scoped commit

  ## Global Constraints

  - Python `>=3.11`; use `tomllib` for reading TOML. Add no new dependencies to either package.
  - whyline must require `whyline-relay>=0.2.26,<0.3` (both places it appears in `pyproject.toml`).
  - Every commit the relay or console makes on the user's behalf is scoped to its own files (`gitcheck.commit_paths`), never `commit_all` / `git add -A`.
  - Relay role dropdowns list `sorted(whyline_relay.adapters.BUILTIN)` — today `["claude", "codex"]`. Do not hard-code Antigravity or Grok.
  - Console code imports `whyline_relay` lazily inside functions (the existing pattern in `src/whyline/console/adapters.py`), so the console still imports without it.
  - Console widget lookups on the main screen go through `WhylineConsoleApp._main(selector, type)`, never `App.query_one` (a dialog may be on top).
  - Relay output goes to `.whyline/relay/logs/console-run.log` (already git-ignored by the relay under `.whyline/relay/logs/`), never a pipe.
  - Plan and Set up refuse in the home-directory repo with the existing `_HOME_REFUSAL` text from `src/whyline/console/repl.py`.
  - After each task, record the decision per `AGENTS.md`:
    `whyline note "<decision>" --because "<why>" --file <path> --actor <your agent name> --role implementer --task CRS-<task number>`.
  - Commit messages end with a blank line and your own `Co-Authored-By:` line if your harness adds one. Do not push or tag except in Tasks 4 and 13.

  **Files:**
  - Modify: `src/whyline_relay/setup.py` (`run_role_wizard` delegates; `run` commits only its paths)
  - Test: `tests/test_setup_write_roles.py` (create)

  **Interfaces:**
  - Consumes: `PIPELINE_CONFIG_TEMPLATE`, `TEST_PROMPT_TEMPLATE`, `config.relay_dir`, `gitcheck.commit_paths`.
  - Produces:
    - `role_paths(root: Path) -> list[Path]` — `[config.toml, prompts/test.md]`.
    - `write_roles(root, implementer, tester, reviewer, backup=(), *, commit=True) -> list[Path]`.

  Step 1: Write the failing tests

  Create `tests/test_setup_write_roles.py`:

  ```python
  import subprocess
  from pathlib import Path

  import pytest

  from whyline_relay import config, setup


  def _git(root: Path, *args: str) -> str:
      return subprocess.run(["git", *args], cwd=root, check=True,
                            capture_output=True, text=True).stdout


  @pytest.fixture
  def repo(tmp_path: Path) -> Path:
      _git(tmp_path, "init", "-q", "-b", "main")
      _git(tmp_path, "config", "user.email", "t@example.com")
      _git(tmp_path, "config", "user.name", "T")
      (tmp_path / "README.md").write_text("x\n")
      _git(tmp_path, "add", "-A")
      _git(tmp_path, "commit", "-qm", "initial")
      return tmp_path


  def _config(root: Path) -> Path:
      return root / ".whyline" / "relay" / "config.toml"


  def test_fresh_repo_gets_the_default_pipeline(repo):
      paths = setup.write_roles(repo, "codex", "claude", "claude")
      assert paths == setup.role_paths(repo)
      loaded = config.load(repo)
      assert set(loaded.pipeline.stages) == {"draft", "test", "review"}
      assert loaded.backup_chain == []


  def test_commit_contains_only_the_setup_files(repo):
      (repo / "unrelated.txt").write_text("not mine\n")
      setup.write_roles(repo, "codex", "claude", "claude")
      committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
      assert sorted(committed) == [".whyline/relay/config.toml", ".whyline/relay/prompts/test.md"]
      assert "unrelated.txt" in _git(repo, "status", "--porcelain")


  def test_backup_chain_is_written_and_removed(repo):
      setup.write_roles(repo, "codex", "claude", "claude", backup=["claude"])
      assert config.load(repo).backup_chain == ["claude"]
      setup.write_roles(repo, "codex", "claude", "claude", backup=[])
      assert "[backup]" not in _config(repo).read_text()
      assert config.load(repo).backup_chain == []


  def test_existing_custom_pipeline_keeps_everything_but_the_roles(repo):
      setup.write_roles(repo, "codex", "claude", "claude")
      custom = _config(repo).read_text().replace(
          "[roles]", "# my notes stay\n[roles]\ndesigner = \"claude\""
      ).replace('max_visits = 5', 'max_visits = 9')
      _config(repo).write_text(custom)
      setup.write_roles(repo, "claude", "codex", "codex", backup=["codex"])
      text = _config(repo).read_text()
      assert "# my notes stay" in text
      assert 'designer = "claude"' in text
      assert "max_visits = 9" in text
      assert 'implementer = "claude"' in text
      assert 'tester      = "codex"' in text
      assert 'reviewer    = "codex"' in text
      assert config.load(repo).backup_chain == ["codex"]


  def test_old_two_role_config_is_replaced_by_the_pipeline_template(repo):
      _config(repo).parent.mkdir(parents=True)
      _config(repo).write_text('[roles]\nimplementer = "codex"\nreviewer = "claude"\n')
      setup.write_roles(repo, "codex", "claude", "claude")
      assert config.load(repo).pipeline is not None


  def test_write_roles_can_skip_the_commit(tmp_path):
      # Not a git repo at all: the terminal wizard's own tests run like this.
      setup.write_roles(tmp_path, "codex", "claude", "claude", commit=False)
      assert _config(tmp_path).exists()
  ```

  Step 2: Run the tests to verify they fail

  Run: `uv run pytest tests/test_setup_write_roles.py -q`
  Expected: FAIL with `AttributeError: module 'whyline_relay.setup' has no attribute 'write_roles'`.

  Step 3: Implement

  At the top of `src/whyline_relay/setup.py`, add `import re` and `import tomllib` to the imports, and `gitcheck` is already imported. Below `PIPELINE_CONFIG_TEMPLATE`, add:

  ```python
  def role_paths(root: Path) -> list[Path]:
      relay = config.relay_dir(root)
      return [relay / "config.toml", relay / "prompts" / "test.md"]


  def _has_pipeline(text: str) -> bool:
      try:
          return "pipeline" in tomllib.loads(text)
      except tomllib.TOMLDecodeError:
          return False


  def _set_keys(text: str, table: str, values: dict[str, str]) -> str:
      """Sets `key = "value"` lines inside [table], keeping every other line
      (comments, other keys, alignment) as it was. Missing keys are added at
      the end of the table."""
      lines = text.splitlines()
      start = next((i for i, line in enumerate(lines) if line.strip() == f"[{table}]"), None)
      if start is None:
          return text.rstrip("\n") + f"\n\n[{table}]\n" + "".join(
              f'{key} = "{value}"\n' for key, value in values.items()
          )
      end = start + 1
      while end < len(lines) and not lines[end].lstrip().startswith("["):
          end += 1
      remaining = dict(values)
      for index in range(start + 1, end):
          for key in list(remaining):
              match = re.match(rf"^(\s*{key}\s*=\s*).*$", lines[index])
              if match:
                  lines[index] = f'{match.group(1)}"{remaining.pop(key)}"'
      insert_at = end
      while insert_at > start + 1 and not lines[insert_at - 1].strip():
          insert_at -= 1
      lines[insert_at:insert_at] = [f'{key} = "{value}"' for key, value in remaining.items()]
      return "\n".join(lines) + "\n"


  def _set_backup(text: str, chain: list[str]) -> str:
      """Replaces the [backup] table (or removes it when `chain` is empty)."""
      lines = text.splitlines()
      kept: list[str] = []
      index = 0
      while index < len(lines):
          if lines[index].strip() == "[backup]":
              index += 1
              while index < len(lines) and not lines[index].lstrip().startswith("["):
                  index += 1
              continue
          kept.append(lines[index])
          index += 1
      content = "\n".join(kept).rstrip("\n") + "\n"
      if chain:
          chain_toml = ", ".join(f'"{name}"' for name in chain)
          content += f"\n[backup]\nchain = [{chain_toml}]\n"
      return content


  def write_roles(
      root: Path,
      implementer: str,
      tester: str,
      reviewer: str,
      backup=(),
      *,
      commit: bool = True,
  ) -> list[Path]:
      """Writes the role assignments without asking anything. A config with its
      own [pipeline] keeps everything except the three role keys and the
      backup chain; anything else gets the default pipeline template. Commits
      only these files."""
      config_file, test_prompt = role_paths(root)
      config_file.parent.mkdir(parents=True, exist_ok=True)
      existing = config_file.read_text(encoding="utf-8") if config_file.exists() else ""
      if _has_pipeline(existing):
          content = _set_keys(existing, "roles", {
              "implementer": implementer, "tester": tester, "reviewer": reviewer,
          })
      else:
          content = PIPELINE_CONFIG_TEMPLATE.format(
              implementer=implementer, tester=tester, reviewer=reviewer
          )
      content = _set_backup(content, [name for name in backup if name])
      config_file.write_text(content, encoding="utf-8")
      test_prompt.parent.mkdir(parents=True, exist_ok=True)
      test_prompt.write_text(TEST_PROMPT_TEMPLATE, encoding="utf-8")
      if commit:
          gitcheck.commit_paths(
              root, [config_file, test_prompt],
              "setup: assign implementer/tester/reviewer roles",
          )
      return [config_file, test_prompt]
  ```

  Replace the body of `run_role_wizard` after the four `input_fn` questions (from `relay = config.relay_dir(root)` through the second `print_fn(...)`) with:

  ```python
      config_path, test_prompt_path = write_roles(
          root, implementer, tester, reviewer, backup_chain, commit=False
      )
      print_fn(f"Wrote {config_path.relative_to(root)}.")
      print_fn(f"Wrote {test_prompt_path.relative_to(root)}.")
  ```

  In `run`, replace:

  ```python
      if gitcheck.commit_all(root, "setup: assign implementer/tester/reviewer roles"):
  ```

  with:

  ```python
      if gitcheck.commit_paths(
          root, role_paths(root), "setup: assign implementer/tester/reviewer roles"
      ):
  ```

  Change nothing else.

  Step 4: Run the tests

  Run: `uv run pytest tests/test_setup_write_roles.py tests/test_setup.py tests/test_cli_setup.py -q`
  Expected: all PASS. (`test_run_commits_setup_before_running_doctor` still passes: the only dirty files in its repo are the setup files.)

  Step 5: Run the whole relay suite

  Run: `uv run pytest -q`
  Expected: all PASS.

  Step 6: Commit

  ```bash
  git add src/whyline_relay/setup.py tests/test_setup_write_roles.py
  git commit -m "feat(setup): write roles without prompting and commit only setup files"
  ```

- [ ] CRS-4: Release whyline-relay 0.2.26

  ## Global Constraints

  - Python `>=3.11`; use `tomllib` for reading TOML. Add no new dependencies to either package.
  - whyline must require `whyline-relay>=0.2.26,<0.3` (both places it appears in `pyproject.toml`).
  - Every commit the relay or console makes on the user's behalf is scoped to its own files (`gitcheck.commit_paths`), never `commit_all` / `git add -A`.
  - Relay role dropdowns list `sorted(whyline_relay.adapters.BUILTIN)` — today `["claude", "codex"]`. Do not hard-code Antigravity or Grok.
  - Console code imports `whyline_relay` lazily inside functions (the existing pattern in `src/whyline/console/adapters.py`), so the console still imports without it.
  - Console widget lookups on the main screen go through `WhylineConsoleApp._main(selector, type)`, never `App.query_one` (a dialog may be on top).
  - Relay output goes to `.whyline/relay/logs/console-run.log` (already git-ignored by the relay under `.whyline/relay/logs/`), never a pipe.
  - Plan and Set up refuse in the home-directory repo with the existing `_HOME_REFUSAL` text from `src/whyline/console/repl.py`.
  - After each task, record the decision per `AGENTS.md`:
    `whyline note "<decision>" --because "<why>" --file <path> --actor <your agent name> --role implementer --task CRS-<task number>`.
  - Commit messages end with a blank line and your own `Co-Authored-By:` line if your harness adds one. Do not push or tag except in Tasks 4 and 13.

  **Files:**
  - Modify: `pyproject.toml` (`version = "0.2.26"`), `src/whyline_relay/__init__.py` (`__version__ = "0.2.26"`), `uv.lock`
  - Create: `docs/releases/v0.2.26.md`

  Step 1: Bump the version

  ```bash
  sed -i '' 's/^version = "0.2.25"/version = "0.2.26"/' pyproject.toml
  sed -i '' 's/__version__ = "0.2.25"/__version__ = "0.2.26"/' src/whyline_relay/__init__.py
  uv lock
  ```

  (On Linux use `sed -i` without `''`.)

  Step 2: Write the release notes

  Create `docs/releases/v0.2.26.md`:

  ```markdown
  # whyline-relay 0.2.26

  Planning and setup can now be driven without typed answers, so whyline's
  console can offer them as buttons.

  ## What's changed

  - `planner.draft`, `planner.revise`, `planner.resume_draft`: run the plan
    draft/review pipeline and return the draft, reporting each stage through
    an optional `print_fn`.
  - `planner.validate` and `planner.approve`: check a plan and save it as
    `plan.md`, committing only that file. `approve` refuses to replace an
    existing plan unless asked.
  - `setup.write_roles`: assign implementer, tester, reviewer and the backup
    chain. A config with its own `[pipeline]` keeps everything else.
  - `whyline relay setup` now commits only its own files (config and test
    prompt) instead of everything in the working tree.
  - Approving a draft that isn't a usable plan now says why instead of saving it.

  ## Upgrading

  ```bash
  uv tool upgrade --refresh whyline
  ```
  ```

  Step 3: Test, commit, tag and push

  ```bash
  uv run pytest -q
  git add pyproject.toml src/whyline_relay/__init__.py uv.lock docs/releases/v0.2.26.md .whyline/decisions.md
  git commit -m "chore: release whyline-relay 0.2.26"
  git tag v0.2.26
  git push origin main v0.2.26
  ```

  Step 4: Confirm it published

  Run: `gh run list --limit 3` until the `release` run for `v0.2.26` shows `success`, then:
  `curl -s https://pypi.org/simple/whyline-relay/ | grep -o 'whyline_relay-0.2.26[^"#<]*' | sort -u`
  Expected: a `.whl` and a `.tar.gz` for 0.2.26. If the release run fails, read `gh run view <id> --log-failed`, fix, move the tag (`git tag -f v0.2.26 && git push -f origin v0.2.26`) and re-check.

  ---
