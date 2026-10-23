"""Parse the text of a Redash query into the argument list of one Redis command."""
import shlex


class QueryError(ValueError):
    """The query text is not a single, well-formed Redis command line."""


def parse_query(text: str) -> list[str]:
    """Split query text into Redis command args, e.g. ``GET "my key"`` -> ``["GET", "my key"]``.

    Rules (``shlex.split`` in POSIX mode, no comments):

    - Exactly one command. Leading/trailing whitespace and a trailing newline
      (also blank trailing lines) are ignored. More than one non-blank line is
      rejected, including a quoted argument that spans lines.
    - Empty or whitespace-only text is rejected.
    - Unbalanced quotes and a trailing lone backslash are rejected.
    - Single quotes are literal; double quotes allow ``\\"`` and ``\\\\``; outside
      quotes a backslash escapes the next character.
    - ``""`` / ``''`` yield an empty-string argument.
    - ``;`` is NOT a separator: it is an ordinary character (``SET k a;b`` has
      args ``["SET", "k", "a;b"]``). ``#`` does not start a comment.
    - Command names are passed through unchanged (``get`` stays ``get``);
      whether the command exists is the runner's concern.
    - Tabs separate args like spaces. Unicode and very long args pass through.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise QueryError("Query is empty: expected one Redis command, e.g. GET mykey")
    if len(lines) > 1:
        raise QueryError(
            f"Query has {len(lines)} lines: only one Redis command per query is supported"
        )
    try:
        args = shlex.split(lines[0], comments=False, posix=True)
    except ValueError as e:
        if "quotation" in str(e):
            raise QueryError("Unbalanced quotes in query: missing closing quotation mark") from e
        raise QueryError(f"Invalid escape in query: {e}") from e
    if not args:  # unreachable given the non-blank check; defensive
        raise QueryError("Query is empty: expected one Redis command, e.g. GET mykey")
    return args
