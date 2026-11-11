"""The suite refuses to run on the Postgres server the app itself uses, unless told to."""
import pytest

from server.tests import dbutil

APP = "postgresql://natural_reader:natural_reader@127.0.0.1:5433/natural_reader"
SCRATCH = "postgresql://natural_reader:natural_reader@127.0.0.1:5434/"


def test_a_bare_pytest_is_refused_because_its_defaults_are_the_apps_server():
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL"):
        dbutil.refuse_app_server({}, test_url=dbutil.DEFAULT_TEST_URL, admin_url=dbutil.DEFAULT_ADMIN_URL)


def test_a_scratch_server_is_accepted():
    dbutil.refuse_app_server({"DATABASE_URL": APP}, test_url=SCRATCH + "natural_reader_test",
                             admin_url=SCRATCH + "postgres")


def test_the_admin_url_alone_on_the_apps_server_is_refused():
    # Scratch databases are created and dropped through the admin URL.
    with pytest.raises(RuntimeError):
        dbutil.refuse_app_server({"DATABASE_URL": APP}, test_url=SCRATCH + "natural_reader_test",
                                 admin_url=APP.rsplit("/", 1)[0] + "/postgres")


def test_localhost_and_127_0_0_1_are_the_same_server():
    with pytest.raises(RuntimeError):
        dbutil.refuse_app_server({"DATABASE_URL": APP}, admin_url=SCRATCH + "postgres",
                                 test_url="postgresql://u:p@localhost:5433/natural_reader_test")


def test_a_contributor_with_one_postgres_can_opt_in():
    dbutil.refuse_app_server({"TEST_DB_ALLOW_APP_SERVER": "1"}, test_url=dbutil.DEFAULT_TEST_URL,
                             admin_url=dbutil.DEFAULT_ADMIN_URL)
