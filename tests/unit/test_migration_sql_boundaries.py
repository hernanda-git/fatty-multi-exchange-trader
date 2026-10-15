"""Migration bodies must remain intact across semicolons and quoted text."""

import pytest

from fatty_trader.storage.migrations import _iter_statements


def test_dollar_quoted_trigger_body_remains_one_statement() -> None:
    function = (
        "CREATE FUNCTION f() RETURNS trigger AS $body$ BEGIN NEW.x := ';'; RETURN NEW; END; "
        "$body$ LANGUAGE plpgsql"
    )
    assert _iter_statements(function + "; CREATE TABLE next_table(id int);") == [
        function,
        "CREATE TABLE next_table(id int)",
    ]


def test_nested_comments_and_quoted_identifiers_do_not_split() -> None:
    first = (
        "/* outer; /* nested; */ done */ CREATE TABLE \"semi;colon\"(x text DEFAULT 'it''s;valid')"
    )
    assert _iter_statements(first + "; SELECT 1;") == [first, "SELECT 1"]


def test_standard_backslash_and_escape_string_rules_differ() -> None:
    assert _iter_statements("SELECT 'C:\\'; SELECT 2;") == ["SELECT 'C:\\'", "SELECT 2"]
    escaped = "SELECT E'can\\'t;split'"
    assert _iter_statements(escaped + "; SELECT 3;") == [escaped, "SELECT 3"]


@pytest.mark.parametrize("sql", ["SELECT 'unfinished", "DO $$ BEGIN;", "/* missing end"])
def test_unterminated_sql_refuses_migration(sql: str) -> None:
    with pytest.raises(ValueError, match="unterminated migration"):
        _iter_statements(sql)
