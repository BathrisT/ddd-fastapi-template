"""Root conftest — intentionally empty.

DB fixtures (postgres container, engine, session, clean_tables) live in
tests/integration/conftest.py so that unit tests never spin up a container.
"""
