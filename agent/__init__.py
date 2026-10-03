"""Not importable code: this file only names the test package `agent.tests`.

pytest's importlib mode names a test package after its first folder without an `__init__.py`.
`apps/api/tests` is `tests`, so without this file `agent/tests` would be `tests` too and the two
`conftest.py` files would collide when the whole suite runs. The agent itself lives in
`src/regista_agent`.
"""
