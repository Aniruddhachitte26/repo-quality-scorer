import ast

from repo_scorer.analyzers.testing import (
    asserts_per_test_function,
    function_references,
    reachable_units,
    unit_names,
)

SOURCE = '''
class Client:
    def __init__(self):
        self.session = make_session()

    def get(self, url):
        return self._send(url)

    def _send(self, url):
        return encode(url)


def make_session():
    return {}


def encode(url):
    return url


def orphan():
    return "never called"
'''

TESTS = '''
import pytest
from mylib import Client

def test_get():
    c = Client()
    assert c.get("x") == "x"

def test_raises():
    with pytest.raises(ValueError):
        pass

def test_nothing():
    Client()
'''


def _units():
    refs = function_references(ast.parse(SOURCE))
    return {q: (unit_names(q), r) for q, r in refs.items()}


def test_unit_names_maps_dunders_to_class():
    assert unit_names("Client.__init__") == {"__init__", "Client"}
    assert unit_names("Auth.__call__") == {"__call__", "Auth"}
    assert unit_names("Client.get") == {"get"}
    assert unit_names("helper") == {"helper"}


def test_implicit_call_is_followed():
    """Mirrors requests' HTTPDigestAuth: tests only construct the class."""
    from repo_scorer.analyzers.testing import identifiers

    source = (
        "class DigestAuth:\n"
        "    def __call__(self, r):\n"
        "        r.register_hook('response', self.handle_401)\n"
        "    def handle_401(self, r):\n"
        "        return self.build_header()\n"
        "    def build_header(self):\n"
        "        return 'digest'\n"
    )
    tests = "def test_auth():\n    get('url', auth=DigestAuth('u', 'p'))\n"
    refs = function_references(ast.parse(source))
    units = {q: (unit_names(q), r) for q, r in refs.items()}
    _, reachable = reachable_units(units, identifiers(ast.parse(tests)))
    assert "DigestAuth.build_header" in reachable


def test_reachability_follows_calls():
    from repo_scorer.analyzers.testing import identifiers

    direct, reachable = reachable_units(_units(), identifiers(ast.parse(TESTS)))
    assert direct == {"Client.__init__", "Client.get"}
    # __init__ -> make_session, get -> _send -> encode
    assert reachable == {
        "Client.__init__", "Client.get", "Client._send", "make_session", "encode",
    }
    assert "orphan" not in reachable


def test_assert_counting():
    counts = asserts_per_test_function(ast.parse(TESTS))
    assert counts == [1, 1, 0]  # assert, pytest.raises, nothing
