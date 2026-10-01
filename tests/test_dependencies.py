from packaging.requirements import Requirement
from packaging.version import Version

from repo_scorer.analyzers.dependencies import (
    newest_allowed,
    parse_pyproject,
    parse_requirement_lines,
    parse_setup_cfg,
    parse_setup_py,
    pin_category,
)


def _names(reqs):
    return [r.name for r in reqs]


def test_requirements_txt_skips_comments_options_and_urls():
    text = (
        "# runtime deps\n"
        "requests>=2.0  # http\n"
        "-r base.txt\n"
        "-e .\n"
        "git+https://github.com/x/y.git\n"
        "\n"
        "click\n"
    )
    assert _names(parse_requirement_lines(text.splitlines())) == ["requests", "click"]


def test_pyproject_pep621_and_poetry():
    pep621 = '[project]\ndependencies = ["httpx>=0.27", "rich"]\n'
    assert _names(parse_pyproject(pep621)) == ["httpx", "rich"]

    poetry = (
        "[tool.poetry.dependencies]\n"
        'python = "^3.11"\n'
        'fastapi = "^0.110"\n'
        'uvicorn = { version = "0.29.0", extras = ["standard"] }\n'
    )
    reqs = parse_pyproject(poetry)
    assert _names(reqs) == ["fastapi", "uvicorn"]
    assert str(reqs[0].specifier) == ">=0.110"
    assert str(reqs[1].specifier) == "==0.29.0"


def test_setup_cfg():
    cfg = "[options]\ninstall_requires =\n    attrs>=21\n    six\n"
    assert _names(parse_setup_cfg(cfg)) == ["attrs", "six"]


def test_setup_py_literal_and_variable():
    literal = "from setuptools import setup\nsetup(name='x', install_requires=['a>=1', 'b'])\n"
    assert _names(parse_setup_py(literal)) == ["a", "b"]

    variable = "import setuptools\nREQS = ['c==2.0']\nsetuptools.setup(install_requires=REQS)\n"
    assert _names(parse_setup_py(variable)) == ["c"]


def test_pin_category():
    assert pin_category(Requirement("a")) == "unpinned"
    assert pin_category(Requirement("a==1.0")) == "exact"
    assert pin_category(Requirement("a>=1,<2")) == "bounded"
    assert pin_category(Requirement("a~=1.4")) == "bounded"
    assert pin_category(Requirement("a>=1")) == "lower_bound"


def test_newest_allowed():
    versions = [Version(v) for v in ["1.0", "1.5", "2.0", "2.3"]]
    assert newest_allowed(Requirement("a<2"), versions) == Version("1.5")
    assert newest_allowed(Requirement("a>=2"), versions) == Version("2.3")
    assert newest_allowed(Requirement("a>=9"), versions) is None
