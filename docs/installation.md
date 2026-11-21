# Installation and compatibility

Use a source checkout from the repository you are reviewing. No public PyPI
registration is claimed for this release.

```bash
git clone https://github.com/hctu4hbs-ux/FORGE-5.git
cd FORGE-5
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
forge5 --version
python scripts/install_skill.py --dest /absolute/path/to/project/.agents/skills
```

The wheel installs the runtime and `forge5` command. The portable skill, examples,
adapter bridge and developer documentation are distributed in the source checkout
and source distribution. Install the skill from that checkout. Keep the runtime
available in the interpreter used by your agent's execution tools.

| Host capability | Integration | Verification in this release |
| --- | --- | --- |
| Agent Skills reader | Copy `skills/forge-5` to its documented discovery directory | Skill structure validated; host UIs not exhaustively tested |
| Local coding agent with subagents | Dispatch `review packet` outputs to fresh reviewers; submit responses | Used for the DurableFlow forward test |
| Single-context coding agent | Five explicit sequential passes, `--mode sequential` | Supported protocol; weaker reviewer independence |
| Codex CLI | Optional `adapters/codex_reviewer.py`, authenticated CLI, read-only sandbox | Executable protocol fixture; live model invocation unavailable here |
| Other local model/agent | JSON stdin/stdout command adapter | Protocol/error/timeout tests |
| Python integration | `Council`, `Reviewer`, `improve` SDK | Behavioral and concurrency tests |

Typical discovery destinations include `.agents/skills` and `.claude/skills`.
Use your specific host's current documentation; folder discovery alone does not
give the host delegation, filesystem access or permission to execute commands.
The installer creates one `forge-5` directory and refuses existing files, directories
and symlinks at that target. It does not modify global agent settings.

Core runtime requirements: Python 3.11+, stdlib SQLite and a writable local project.
Linux/POSIX is the locally validated platform. CI is configured for Python
3.11–3.14 on Linux; configured CI is not evidence that remote runs have passed.
Windows execution lacks POSIX process-group descendant cleanup and is not claimed
as validated. The example uses SQLite for single-host durable scheduling.

To inspect without installing:

```bash
PYTHONPATH=src python3 -m forge5 --help
python3 skills/forge-5/scripts/forge.py --version
```

To uninstall the portable skill, remove the installed `forge-5` directory from
your chosen host discovery path. Uninstall the runtime using the same Python
environment in which it was installed. Existing `.forge5/` project evidence is
independent of installation; back it up before any manual cleanup.
