# Basic tests

## Running

```bash
pytest tests/basic -q
```

If your environment auto-loads unrelated pytest plugins (e.g. `langchain_tests`)
that are not part of Z, disable them:

```bash
pytest -p no:langchain_tests tests/basic -q
```

Or set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` when running locally.

P0 control-flow transcript scenarios live in `test_z_p0_control_flow.py`.
