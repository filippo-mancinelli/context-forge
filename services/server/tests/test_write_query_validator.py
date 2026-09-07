import pytest

from src.datasources.validator import QueryValidationError, validate_write_query


def test_insert_is_allowed():
    sql = "INSERT INTO users (name) VALUES ('anna')"
    assert validate_write_query(sql) == sql


def test_update_with_where_is_allowed():
    sql = "UPDATE users SET name = 'anna' WHERE id = 3"
    assert validate_write_query(sql) == sql


def test_delete_with_where_is_allowed():
    sql = "DELETE FROM t WHERE id = 3"
    assert validate_write_query(sql) == sql


def test_update_with_real_where_containing_subquery_is_allowed():
    sql = "UPDATE t SET x=1 WHERE id IN (SELECT id FROM s WHERE active=true)"
    assert validate_write_query(sql) == sql


@pytest.mark.parametrize("sql", [
    "UPDATE users SET name = 'anna'",
    "DELETE FROM users",
])
def test_update_delete_require_where(sql):
    with pytest.raises(QueryValidationError, match="WHERE"):
        validate_write_query(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE users SET bio = 'not sure where I put it'",
    "UPDATE users SET score = (SELECT max(v) FROM logs WHERE active=true)",
    'UPDATE users SET "where" = true',
    "UPDATE users SET `where` = 1",
    "UPDATE users SET note = 'it\\'s WHERE it counts'",
    "UPDATE tenant_users SET plan = $$ platinum WHERE $$",
    "UPDATE t SET note = $tag$ has WHERE inside $tag$",
])
def test_update_delete_reject_fake_where_decoys(sql):
    with pytest.raises(QueryValidationError, match="WHERE"):
        validate_write_query(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE t SET x=1 WHERE `id` = 3",
    "UPDATE t SET note='it\\'s fine' WHERE id=3",
    "UPDATE t SET note = $$plain body$$ WHERE id = 3",
])
def test_update_delete_allow_real_where_alongside_dialect_quoting(sql):
    assert validate_write_query(sql) == sql


@pytest.mark.parametrize("sql", [
    "DROP TABLE users",
    "TRUNCATE users",
    "CREATE TABLE t (id int)",
    "ALTER TABLE users ADD COLUMN x int",
    "GRANT ALL ON users TO public",
    "SELECT * FROM users",
    "INSERT INTO a VALUES (1); DELETE FROM b WHERE 1=1",
    "SET search_path = public",
])
def test_everything_else_is_rejected(sql):
    with pytest.raises(QueryValidationError):
        validate_write_query(sql)


def test_comments_cannot_smuggle_keywords():
    with pytest.raises(QueryValidationError):
        validate_write_query("INSERT INTO a VALUES (1) /* ; DROP TABLE b */; DROP TABLE b")
