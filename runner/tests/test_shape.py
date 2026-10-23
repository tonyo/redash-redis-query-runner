"""Unit tests for shape(): pure, no Redis needed."""

import pytest
from redash.query_runner import TYPE_BOOLEAN, TYPE_FLOAT, TYPE_INTEGER, TYPE_STRING
from redis_query_runner.shape import shape

INT, F, S, B = TYPE_INTEGER, TYPE_FLOAT, TYPE_STRING, TYPE_BOOLEAN


def cols(result):
    return [(c["name"], c["type"]) for c in result["columns"]]


def values(result, name="value"):
    return [r[name] for r in result["rows"]]


def test_result_shape_has_friendly_name():
    r = shape("x")
    assert r == {
        "columns": [{"name": "value", "friendly_name": "value", "type": S}],
        "rows": [{"value": "x"}],
    }


# --- scalars, nil, bool -------------------------------------------------------
@pytest.mark.parametrize(
    "reply, args, expected, typ",
    [
        ("hello", ("GET", "k"), "hello", S),
        ("", ("GET", "k"), "", S),
        ("42", ("GET", "k"), 42, INT),
        (42, ("INCR", "k"), 42, INT),
        (-7, ("DECR", "k"), -7, INT),
        (3.5, ("ZSCORE", "z", "m"), 3.5, F),
        ("3.5", ("GET", "k"), 3.5, F),
        ("héllo ✓ 日本", ("GET", "k"), "héllo ✓ 日本", S),
        ("x" * 100_000, ("GET", "k"), "x" * 100_000, S),
        (b"raw", ("GET", "k"), "raw", S),
    ],
)
def test_scalar(reply, args, expected, typ):
    r = shape(reply, args)
    assert cols(r) == [("value", typ)]
    assert values(r) == [expected]
    assert type(values(r)[0]) is type(expected)


def test_nil_is_one_null_row_string_column():
    r = shape(None, ("GET", "missing"))
    assert cols(r) == [("value", S)]
    assert r["rows"] == [{"value": None}]


def test_true_for_ping_is_pong():
    assert values(shape(True, ("PING",))) == ["PONG"]
    assert values(shape(True, ("ping",))) == ["PONG"]


@pytest.mark.parametrize("args", [("SET", "k", "v"), ("FLUSHDB",), ("SELECT", 1), ()])
def test_true_otherwise_is_ok(args):
    r = shape(True, args)
    assert values(r) == ["OK"]
    assert cols(r) == [("value", S)]


def test_false_stays_boolean():
    r = shape(False, ("SISMEMBER", "s", "m"))
    assert cols(r) == [("value", B)]
    assert values(r) == [False]


def test_true_inside_list_is_not_rewritten():
    r = shape([True, False], ("X",))
    assert cols(r) == [("value", B)]
    assert values(r) == [True, False]


# --- strict number typing ----------------------------------------------------
@pytest.mark.parametrize(
    "text, expected, typ",
    [
        ("0", 0, INT),
        ("5", 5, INT),
        ("-5", -5, INT),
        ("-12345", -12345, INT),
        ("123456789012345678901234567890", 123456789012345678901234567890, INT),
        ("-123456789012345678901234567890", -123456789012345678901234567890, INT),
        ("3.14", 3.14, F),
        ("-0.5", -0.5, F),
        ("0.0", 0.0, F),
        ("1e5", 100000.0, F),
        ("1E5", 100000.0, F),
        ("2.5e-3", 0.0025, F),
        ("-1e+2", -100.0, F),
    ],
)
def test_numeric_strings_are_typed(text, expected, typ):
    r = shape(text, ("GET", "k"))
    assert cols(r) == [("value", typ)]
    assert values(r) == [expected]
    assert type(values(r)[0]) is type(expected)


@pytest.mark.parametrize(
    "text",
    [
        "007",
        "00",
        "-007",
        "+5",
        "+0",
        " 12 ",
        " 12",
        "12 ",
        "1_000",
        "-0",
        "-0.0x",
        "nan",
        "NaN",
        "inf",
        "-inf",
        "Infinity",
        "1e999",
        "-1e999",
        "1.",
        ".5",
        "0x10",
        "1,000",
        "12abc",
        "abc",
        "1e",
        "e5",
        "--1",
        "٣",
        "１２",  # noqa: RUF001 (fullwidth digits are the point of this case)
        "00.5",
        "1\n",
        "",
    ],
)
def test_non_canonical_numbers_stay_text(text):
    r = shape(text, ("GET", "k"))
    assert cols(r) == [("value", S)]
    assert values(r) == [text]


def test_trailing_newline_not_matched_by_dollar_anchor():
    assert values(shape(["12\n"])) == ["12\n"]


def test_column_all_ints_is_integer():
    r = shape(["1", "2", "-3"], ("LRANGE", "l", 0, -1))
    assert cols(r) == [("value", INT)]
    assert values(r) == [1, 2, -3]


def test_int_and_float_mixed_column_is_all_float():
    r = shape(["1", "2.5", "3"], ("LRANGE", "l", 0, -1))
    assert cols(r) == [("value", F)]
    assert values(r) == [1.0, 2.5, 3.0]
    assert all(type(v) is float for v in values(r))


def test_one_bad_value_keeps_whole_column_text():
    r = shape(["1", "2", "007"], ("LRANGE", "l", 0, -1))
    assert cols(r) == [("value", S)]
    assert values(r) == ["1", "2", "007"]


def test_numeric_and_word_mixed_column_stays_string():
    r = shape(["1", "two", "3"], ("LRANGE", "l", 0, -1))
    assert cols(r) == [("value", S)]
    assert values(r) == ["1", "two", "3"]


def test_nulls_do_not_block_typing():
    r = shape(["1", None, "3"], ("MGET", "a", "b", "c"))
    assert cols(r) == [("value", INT)]
    assert values(r) == [1, None, 3]


def test_all_nil_column_is_string():
    r = shape([None, None], ("MGET", "a", "b"))
    assert cols(r) == [("value", S)]
    assert values(r) == [None, None]


def test_native_int_and_text_mixed_column_is_uniform_strings():
    r = shape([1, "x"], ("X",))
    assert cols(r) == [("value", S)]
    assert values(r) == ["1", "x"]


def test_native_int_and_float_column_is_float():
    r = shape([1, 2.5], ("X",))
    assert cols(r) == [("value", F)]
    assert values(r) == [1, 2.5]


@pytest.mark.parametrize("v", [float("nan"), float("inf"), float("-inf")])
def test_native_non_finite_float_becomes_string(v):
    r = shape(v, ("ZSCORE", "z", "m"))
    assert cols(r) == [("value", S)]
    assert values(r) == [str(v)]


# --- flat lists, tuples, sets --------------------------------------------------
@pytest.mark.parametrize("reply", [[], ()])
def test_empty_list(reply):
    r = shape(reply, ("LRANGE", "l", 0, -1))
    assert cols(r) == [("value", S)]
    assert r["rows"] == []


def test_flat_list_one_value_column_in_order():
    r = shape(["b", "a", "b"], ("LRANGE", "l", 0, -1))
    assert cols(r) == [("value", S)]
    assert values(r) == ["b", "a", "b"]


def test_tuple_treated_like_list():
    assert values(shape(("a", "b"))) == ["a", "b"]


def test_list_with_nil_elements():
    r = shape(["a", None, "c"], ("MGET", "a", "b", "c"))
    assert values(r) == ["a", None, "c"]


def test_empty_set():
    r = shape(set(), ("SMEMBERS", "s"))
    assert cols(r) == [("value", S)]
    assert r["rows"] == []


def test_set_is_sorted():
    assert values(shape({"c", "a", "b"}, ("SMEMBERS", "s"))) == ["a", "b", "c"]


def test_frozenset_is_sorted():
    assert values(shape(frozenset({"z", "y"}))) == ["y", "z"]


def test_set_of_numeric_strings_sorts_as_text_then_types_int():
    r = shape({"10", "9", "100"}, ("SMEMBERS", "s"))
    assert values(r) == [10, 100, 9]  # sorted as strings, then typed
    assert cols(r) == [("value", INT)]


def test_set_with_mixed_types_does_not_raise():
    assert len(shape({1, "a"}, ("X",))["rows"]) == 2


# --- ZRANGE ... WITHSCORES ---------------------------------------------------
def test_zrange_withscores_flat_list_pairs():
    r = shape(["a", "1", "b", "2.5"], ("ZRANGE", "z", 0, -1, "WITHSCORES"))
    assert cols(r) == [("member", S), ("score", F)]
    assert r["rows"] == [{"member": "a", "score": 1.0}, {"member": "b", "score": 2.5}]


def test_zrange_withscores_lowercase_args():
    r = shape(["a", "1"], ("zrange", "z", 0, -1, "withscores"))
    assert cols(r) == [("member", S), ("score", INT)]


def test_zrange_withscores_tuple_pairs_from_redis_py():
    r = shape([("a", 1.0), ("b", 2.0)], ("ZRANGE", "z", 0, -1, "WITHSCORES"))
    assert cols(r) == [("member", S), ("score", F)]
    assert r["rows"] == [{"member": "a", "score": 1.0}, {"member": "b", "score": 2.0}]


def test_zrange_withscores_integer_scores():
    r = shape(["a", "1", "b", "2"], ("ZREVRANGEBYSCORE", "z", "+inf", "-inf", "WITHSCORES"))
    assert cols(r) == [("member", S), ("score", INT)]


def test_zrange_withscores_empty():
    r = shape([], ("ZRANGE", "z", 0, -1, "WITHSCORES"))
    assert cols(r) == [("member", S), ("score", S)]
    assert r["rows"] == []


def test_zrange_withscores_odd_length_falls_back_to_single_column():
    r = shape(["a", "1", "b"], ("ZRANGE", "z", 0, -1, "WITHSCORES"))
    assert [c["name"] for c in r["columns"]] == ["value"]
    assert values(r) == ["a", "1", "b"]
    assert len(r["rows"]) == 3


def test_zrange_without_withscores_is_plain_list():
    r = shape(["a", "b"], ("ZRANGE", "z", 0, -1))
    assert cols(r) == [("value", S)]


def test_zrange_numeric_members_still_member_column():
    r = shape(["1", "5", "2", "6"], ("ZRANGE", "z", 0, -1, "WITHSCORES"))
    assert cols(r) == [("member", INT), ("score", INT)]


def test_zpopmin_pairs():
    r = shape([("a", 1.0)], ("ZPOPMIN", "z"))
    assert cols(r) == [("member", S), ("score", F)]


def test_zrange_withscores_member_with_nil_score_pair():
    r = shape(["a", None], ("ZRANGE", "z", 0, -1, "WITHSCORES"))
    assert r["rows"] == [{"member": "a", "score": None}]


# --- CONFIG GET, INFO ------------------------------------------------------
def test_config_get_dict_is_key_value_rows():
    r = shape({"maxmemory": "0", "appendonly": "no"}, ("CONFIG", "GET", "*"))
    assert [c["name"] for c in r["columns"]] == ["key", "value"]
    assert r["rows"] == [{"key": "maxmemory", "value": "0"}, {"key": "appendonly", "value": "no"}]
    assert cols(r) == [("key", S), ("value", S)]


def test_config_get_flat_list_is_key_value_rows():
    r = shape(["maxmemory", "0", "appendonly", "no"], ("config", "get", "*"))
    assert r["rows"] == [{"key": "maxmemory", "value": "0"}, {"key": "appendonly", "value": "no"}]


def test_config_get_numeric_values_all_numeric_become_integer():
    r = shape({"a": "1", "b": "2"}, ("CONFIG", "GET", "*"))
    assert cols(r) == [("key", S), ("value", INT)]


def test_config_get_empty():
    r = shape({}, ("CONFIG", "GET", "nope"))
    assert cols(r) == [("key", S), ("value", S)]
    assert r["rows"] == []


def test_info_dict_key_value_with_native_types_and_mixed_column_text():
    info = {"redis_version": "7.2.4", "uptime_in_seconds": 10, "used_memory": 1000}
    r = shape(info, ("INFO",))
    assert [c["name"] for c in r["columns"]] == ["key", "value"]
    assert cols(r) == [("key", S), ("value", S)]
    assert values(r, "value") == ["7.2.4", "10", "1000"]
    assert values(r, "key") == list(info)


def test_info_nested_keyspace_dict_is_a_table():
    r = shape({"db0": {"keys": 3, "expires": 0}, "db1": {"keys": 5, "expires": 1}}, ("INFO",))
    assert cols(r) == [("name", S), ("keys", INT), ("expires", INT)]
    assert r["rows"] == [
        {"name": "db0", "keys": 3, "expires": 0},
        {"name": "db1", "keys": 5, "expires": 1},
    ]


def test_info_with_scalar_and_nested_values_stays_key_value():
    r = shape({"a": 1, "db0": {"keys": 3}}, ("INFO",))
    assert cols(r) == [("key", S), ("value", S)]


# --- HGETALL -----------------------------------------------------------------
def test_hgetall_one_row_typed_columns():
    r = shape({"name": "ann", "age": "30", "score": "9.5"}, ("HGETALL", "h"))
    assert cols(r) == [("name", S), ("age", INT), ("score", F)]
    assert r["rows"] == [{"name": "ann", "age": 30, "score": 9.5}]


def test_hgetall_leading_zero_field_stays_text():
    r = shape({"zip": "007"}, ("HGETALL", "h"))
    assert cols(r) == [("zip", S)]
    assert r["rows"] == [{"zip": "007"}]


def test_hgetall_empty_hash_is_empty_key_value_table():
    r = shape({}, ("HGETALL", "h"))
    assert cols(r) == [("key", S), ("value", S)]
    assert r["rows"] == []


def test_hgetall_unicode_and_spaces_in_field_names():
    r = shape({"naïve field": "1", "日本": "x"}, ("HGETALL", "h"))
    assert cols(r) == [("naïve field", INT), ("日本", S)]


def test_hgetall_flat_list_duplicate_fields_last_wins():
    r = shape(["a", "1", "a", "2", "b", "x"], ("HGETALL", "h"))
    assert cols(r) == [("a", INT), ("b", S)]
    assert r["rows"] == [{"a": 2, "b": "x"}]


def test_hgetall_flat_list_empty():
    r = shape([], ("HGETALL", "h"))
    assert r["rows"] == []


def test_hgetall_value_with_nested_container_is_repr():
    r = shape({"a": ["x"]}, ("HGETALL", "h"))
    assert r["rows"] == [{"a": "['x']"}]


def test_hgetall_only_applies_by_command_name():
    r = shape({"a": "1"}, ("CONFIG", "GET", "a"))
    assert [c["name"] for c in r["columns"]] == ["key", "value"]


# --- SCAN --------------------------------------------------------------------
def test_scan_keys_as_rows_cursor_dropped():
    r = shape((17, ["k1", "k2"]), ("SCAN", 0))
    assert cols(r) == [("value", S)]
    assert values(r) == ["k1", "k2"]


def test_scan_empty_page():
    r = shape((0, []), ("SCAN", 0, "MATCH", "zzz*"))
    assert r["rows"] == []
    assert cols(r) == [("value", S)]


def test_sscan_lowercase_and_list_reply():
    r = shape([5, ["m1", "m2"]], ("sscan", "s", 0))
    assert values(r) == ["m1", "m2"]


def test_scan_numeric_key_names_become_integer():
    assert cols(shape((0, ["1", "2"]), ("SCAN", 0))) == [("value", INT)]


# --- streams -----------------------------------------------------------------
def test_xrange_id_plus_field_columns():
    reply = [("1-0", {"a": "1", "b": "x"}), ("2-0", {"a": "2", "b": "y"})]
    r = shape(reply, ("XRANGE", "s", "-", "+"))
    assert cols(r) == [("id", S), ("a", INT), ("b", S)]
    assert r["rows"] == [{"id": "1-0", "a": 1, "b": "x"}, {"id": "2-0", "a": 2, "b": "y"}]


def test_xrevrange_works():
    r = shape([("2-0", {"a": "x"})], ("XREVRANGE", "s", "+", "-"))
    assert cols(r) == [("id", S), ("a", S)]


def test_xrange_differing_fields_union_in_first_seen_order_with_nulls():
    reply = [("1-0", {"a": "1"}), ("2-0", {"b": "x", "a": "2"}), ("3-0", {"c": "3"})]
    r = shape(reply, ("XRANGE", "s", "-", "+"))
    assert [c["name"] for c in r["columns"]] == ["id", "a", "b", "c"]
    assert cols(r) == [("id", S), ("a", INT), ("b", S), ("c", INT)]
    assert r["rows"] == [
        {"id": "1-0", "a": 1, "b": None, "c": None},
        {"id": "2-0", "a": 2, "b": "x", "c": None},
        {"id": "3-0", "a": None, "b": None, "c": 3},
    ]


def test_xrange_empty():
    r = shape([], ("XRANGE", "s", "-", "+"))
    assert cols(r) == [("id", S)]
    assert r["rows"] == []


def test_xrange_deleted_entry_with_none_fields():
    r = shape([("1-0", None), ("2-0", {"a": "1"})], ("XRANGE", "s", "-", "+"))
    assert r["rows"] == [{"id": "1-0", "a": None}, {"id": "2-0", "a": 1}]


def test_xrange_raw_flat_field_list():
    r = shape([["1-0", ["a", "1", "b", "2"]]], ("XRANGE", "s", "-", "+"))
    assert r["rows"] == [{"id": "1-0", "a": 1, "b": 2}]


def test_xrange_field_named_id_does_not_clobber_entry_id():
    r = shape([("1-0", {"id": "mine"})], ("XRANGE", "s", "-", "+"))
    assert [c["name"] for c in r["columns"]] == ["id", "id_1"]
    assert r["rows"] == [{"id": "1-0", "id_1": "mine"}]


def test_xrange_id_column_not_numeric_typed():
    assert cols(shape([("1700000000000-0", {})], ("XRANGE", "s", "-", "+")))[0] == ("id", S)


# --- nested / unknown ----------------------------------------------------------
def test_nested_lists_become_repr_strings():
    r = shape([["a", "1"], ["b", ["c"]]], ("EXEC",))
    assert cols(r) == [("value", S)]
    assert values(r) == ["['a', '1']", "['b', ['c']]"]


def test_mixed_scalar_and_nested():
    r = shape(["x", ["y"], None], ("X",))
    assert values(r) == ["x", "['y']", None]


def test_list_of_pairs_for_unknown_command_is_repr_not_columns():
    r = shape([("a", "1")], ("FOO",))
    assert cols(r) == [("value", S)]
    assert values(r) == ["('a', '1')"]


def test_nested_empty_list_element():
    assert values(shape([[], "a"])) == ["[]", "a"]


def test_unknown_dict_is_key_value():
    r = shape({"a": "1"}, ("FOO",))
    assert r["rows"] == [{"key": "a", "value": 1}]


def test_args_default_is_empty():
    assert values(shape(["a"])) == ["a"]


def test_args_may_contain_non_strings():
    r = shape(["a", "1"], ("ZRANGE", "z", 0, -1, "WITHSCORES", 5, None))
    assert [c["name"] for c in r["columns"]] == ["member", "score"]


def test_very_long_value_preserved():
    big = "y" * 1_000_000
    assert values(shape([big])) == [big]


def test_shape_does_not_mutate_reply():
    reply = {"a": "1"}
    shape(reply, ("HGETALL", "h"))
    assert reply == {"a": "1"}
    lst = ["1", "2"]
    shape(lst)
    assert lst == ["1", "2"]


def test_rows_are_json_serialisable():
    import json

    for reply, args in [
        ({"a": "1", "b": ["x"]}, ("HGETALL", "h")),
        ((0, ["k"]), ("SCAN", 0)),
        ({"b", "a"}, ()),
        (None, ()),
        (True, ("PING",)),
    ]:
        json.dumps(shape(reply, args))


# --- redis-py shapes seen from a real server -------------------------------------
def test_hscan_gives_field_value_rows_without_cursor():
    r = shape((0, {"f1": "v1", "f2": "7"}), ("HSCAN", "h", "0"))
    assert cols(r) == [("field", S), ("value", S)]
    assert [x["field"] for x in r["rows"]] == ["f1", "f2"]


def test_zscan_gives_member_score_rows():
    r = shape((0, [("a", 1.0), ("b", 2.5)]), ("ZSCAN", "z", "0"))
    assert cols(r) == [("member", S), ("score", F)]


def test_xread_has_stream_id_and_field_columns():
    reply = [["x", [("1-1", {"a": "1"})]], ["y", [("5-1", {"q": "9"})]]]
    r = shape(reply, ("XREAD", "STREAMS", "x", "y", "0", "0"))
    assert cols(r) == [("stream", S), ("id", S), ("a", INT), ("q", INT)]
    assert r["rows"][1] == {"stream": "y", "id": "5-1", "a": None, "q": 9}


def test_xread_empty_reply_is_an_empty_table():
    assert shape([], ("XREAD", "STREAMS", "x", "$"))["rows"] == []


def test_xinfo_stream_flat_pairs_become_key_value():
    r = shape(["length", 2, "last-generated-id", "2-1"], ("XINFO", "STREAM", "x"))
    assert cols(r) == [("key", S), ("value", S)]
    assert len(r["rows"]) == 2


def test_xinfo_groups_one_row_per_group():
    reply = [["name", "g1", "pending", 0], ["name", "g2", "pending", 3]]
    r = shape(reply, ("XINFO", "GROUPS", "x"))
    assert cols(r) == [("name", S), ("pending", INT)]
    assert values(r, "name") == ["g1", "g2"]


def test_memory_stats_flat_pairs_become_key_value():
    r = shape(["peak.allocated", 10, "db.0", ["a", 1]], ("MEMORY", "STATS"))
    assert cols(r) == [("key", S), ("value", S)]


def test_geopos_has_longitude_latitude_and_keeps_missing_members():
    r = shape([(13.36, 38.11), None], ("GEOPOS", "g", "a", "b"))
    assert cols(r) == [("longitude", F), ("latitude", F)]
    assert r["rows"][1] == {"longitude": None, "latitude": None}


def test_geosearch_with_flags_names_each_part():
    reply = [["Palermo", "190.1377", 3479099943497504, ["13.36", "38.11"]]]
    args = ("GEOSEARCH", "g", "WITHDIST", "WITHHASH", "WITHCOORD")
    r = shape(reply, args)
    assert [c[0] for c in cols(r)] == ["member", "distance", "hash", "longitude", "latitude"]
    assert cols(r)[1] == ("distance", F)


def test_geosearch_without_flags_is_a_plain_list():
    assert cols(shape(["Palermo"], ("GEOSEARCH", "g"))) == [("value", S)]


def test_lmpop_rows_carry_the_key():
    r = shape(["l", ["a", "b"]], ("LMPOP", "1", "l", "LEFT"))
    assert r["rows"] == [{"key": "l", "value": "a"}, {"key": "l", "value": "b"}]


def test_lmpop_nil_is_one_empty_value_row():
    assert shape(None, ("LMPOP", "1", "l", "LEFT"))["rows"] == [{"value": None}]


def test_zmpop_rows_carry_key_member_score():
    r = shape(["z", [["a", "1"]]], ("ZMPOP", "1", "z", "MIN"))
    assert cols(r) == [("key", S), ("member", S), ("score", INT)]


def test_slowlog_get_names_columns_and_joins_the_command():
    reply = [[22, 1792795527, 2, ["GEOPOS", "g", "Palermo"], "10.0.0.1:5", ""]]
    r = shape(reply, ("SLOWLOG", "GET", "1"))
    assert r["rows"][0]["command"] == "GEOPOS g Palermo"
    assert [c[0] for c in cols(r)][:3] == ["id", "start_time", "duration_us"]


def test_client_list_is_a_row_per_client():
    text = "id=3 addr=1.2.3.4:5 name= db=0\nid=4 addr=1.2.3.4:6 name=x db=15\n"
    r = shape(text, ("CLIENT", "LIST"))
    assert cols(r) == [("id", INT), ("addr", S), ("name", S), ("db", INT)]
    assert len(r["rows"]) == 2


def test_datetime_cell_is_iso_text():
    from datetime import datetime

    r = shape(datetime(2026, 10, 23, 22, 44, 40), ("LASTSAVE",))
    assert r["rows"] == [{"value": "2026-10-23T22:44:40"}]
