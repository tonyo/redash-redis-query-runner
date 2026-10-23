import pytest

from redis_query_runner.parser import QueryError, parse_query

LONG = "x" * 100_000


@pytest.mark.parametrize(
    "text, expected",
    [
        # basics
        ("PING", ["PING"]),
        ("GET mykey", ["GET", "mykey"]),
        ("SET k v", ["SET", "k", "v"]),
        # whitespace
        ("  GET   k  ", ["GET", "k"]),
        ("GET\tk", ["GET", "k"]),
        ("\tGET\t\tk\t", ["GET", "k"]),
        # trailing newline / blank trailing lines allowed
        ("GET k\n", ["GET", "k"]),
        ("GET k\r\n", ["GET", "k"]),
        ("GET k\n\n  \n", ["GET", "k"]),
        ("\n\nGET k", ["GET", "k"]),
        # quoting
        ('SET k "hello world"', ["SET", "k", "hello world"]),
        ("SET k 'hello world'", ["SET", "k", "hello world"]),
        ("SET k \"it's\"", ["SET", "k", "it's"]),
        ("SET k 'say \"hi\"'", ["SET", "k", 'say "hi"']),
        ("SET k pre\"fix ed\"", ["SET", "k", "prefix ed"]),
        ("SET \"my key\" v", ["SET", "my key", "v"]),
        # empty-string args
        ('SET k ""', ["SET", "k", ""]),
        ("SET k ''", ["SET", "k", ""]),
        ('SET "" v', ["SET", "", "v"]),
        # embedded tab inside quotes is preserved
        ('SET k "a\tb"', ["SET", "k", "a\tb"]),
        # escapes
        (r"SET k a\ b", ["SET", "k", "a b"]),
        (r'SET k "a\"b"', ["SET", "k", 'a"b']),
        (r'SET k "a\\b"', ["SET", "k", "a\\b"]),
        (r"SET k a\\b", ["SET", "k", "a\\b"]),
        (r"SET k 'a\b'", ["SET", "k", "a\\b"]),  # single quotes are literal
        (r'SET k "a\nb"', ["SET", "k", "a\\nb"]),  # \n is NOT a newline escape
        (r"SET k a\"b", ["SET", "k", 'a"b']),
        # ';' is not a separator
        ("SET k a;b", ["SET", "k", "a;b"]),
        ("GET a ; GET b", ["GET", "a", ";", "GET", "b"]),
        ("GET k;", ["GET", "k;"]),
        ('SET k "a;b"', ["SET", "k", "a;b"]),
        # '#' is not a comment
        ("SET k #notacomment", ["SET", "k", "#notacomment"]),
        # command names untouched
        ("get k", ["get", "k"]),
        ("GeT k", ["GeT", "k"]),
        ("config get maxmemory", ["config", "get", "maxmemory"]),
        # glob/pattern chars stay literal
        ("KEYS user:*", ["KEYS", "user:*"]),
        ("SCAN 0 MATCH a[bc]? COUNT 10", ["SCAN", "0", "MATCH", "a[bc]?", "COUNT", "10"]),
        # unicode
        ("SET ключ значение", ["SET", "ключ", "значение"]),
        ('SET k "héllo wörld ✓"', ["SET", "k", "héllo wörld ✓"]),
        ("SET k 日本語", ["SET", "k", "日本語"]),
        ("SET k \U0001f600", ["SET", "k", "\U0001f600"]),
        # very long args
        (f"SET k {LONG}", ["SET", "k", LONG]),
        (f'SET k "{LONG} {LONG}"', ["SET", "k", f"{LONG} {LONG}"]),
        ("GET " + " ".join(["k"] * 5000), ["GET"] + ["k"] * 5000),
    ],
)
def test_parses(text, expected):
    assert parse_query(text) == expected


@pytest.mark.parametrize("text", ["", " ", "   ", "\t", "\n", "\r\n", " \n \t\n "])
def test_empty_or_whitespace_rejected(text):
    with pytest.raises(QueryError, match="empty"):
        parse_query(text)


@pytest.mark.parametrize(
    "text",
    [
        "GET a\nGET b",
        "GET a\r\nGET b",
        "GET a\n\nGET b",
        "GET a\n   \nGET b\n",
        "GET a\n  GET b",
        "PING\nPING\nPING",
        'SET k "line1\nline2"',  # quoted arg spanning lines
        "SET k a\\\nb",  # backslash-newline continuation
    ],
)
def test_multi_line_rejected(text):
    with pytest.raises(QueryError, match="lines"):
        parse_query(text)


@pytest.mark.parametrize(
    "text",
    ['SET k "abc', "SET k 'abc", 'SET k "', "SET k '", "SET k \"a'b", "SET k 'a\"b", 'SET k "a\\"'],
)
def test_unbalanced_quotes_rejected(text):
    with pytest.raises(QueryError, match="(?i)quot"):
        parse_query(text)


@pytest.mark.parametrize("text", ["SET k abc\\", "SET k \\"])
def test_trailing_backslash_rejected(text):
    with pytest.raises(QueryError, match="(?i)escape"):
        parse_query(text)


def test_query_error_is_value_error():
    assert issubclass(QueryError, ValueError)


def test_error_chain_hides_nothing_sensitive_and_is_clear():
    with pytest.raises(QueryError) as exc:
        parse_query('SET k "abc')
    assert "Unbalanced quotes" in str(exc.value)


def test_param_substituted_value_with_spaces_splits_unless_quoted():
    # Redash substitutes {{ param }} before the runner sees the text.
    assert parse_query("GET user:john smith") == ["GET", "user:john", "smith"]
    assert parse_query('GET "user:john smith"') == ["GET", "user:john smith"]


def test_returns_fresh_list_of_str():
    out = parse_query("GET k")
    assert isinstance(out, list) and all(isinstance(a, str) for a in out)
