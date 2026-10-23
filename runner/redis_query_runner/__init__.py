"""Redis query runner for Redash.

Importable on purpose: Redash only imports the module named in
REDASH_ADDITIONAL_QUERY_RUNNERS=redis_query_runner, so importing it registers the runner.
"""

from .runner import Redis

__all__ = ["Redis"]
