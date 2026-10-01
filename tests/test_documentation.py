from repo_scorer.analyzers.base import is_auxiliary_path, is_public, library_paths
from repo_scorer.analyzers.documentation import ReadmeStats, analyze_readme, readme_score

GOOD_README = """# MyLib

A small library that does useful things. """ + "word " * 300 + """

## Installation

    pip install mylib

## Usage

```python
import mylib
mylib.run()
```

## License

MIT
"""


def test_good_readme_scores_full_marks():
    stats = analyze_readme(GOOD_README, "README.md")
    assert stats.sections >= 3
    assert stats.has_code_example
    assert stats.mentions_install
    assert stats.mentions_usage
    assert readme_score(stats) == 100.0


def test_missing_readme_scores_zero():
    assert readme_score(ReadmeStats(filename=None)) == 0.0


def test_bare_readme_scores_low():
    stats = analyze_readme("mylib\n\nIt does things.", "README")
    assert readme_score(stats) < 30


def test_rst_sections_are_counted():
    rst = "Title\n=====\n\nIntro\n\nInstall\n-------\n\nUsage\n-----\n"
    assert analyze_readme(rst, "README.rst").sections == 3


def test_is_public():
    assert is_public("Session.request")
    assert not is_public("_helper")
    assert not is_public("Session._internal")
    assert not is_public("_Private.method")
    assert not is_public("Session.__init__")


def test_is_auxiliary_path():
    assert is_auxiliary_path("docs/conf.py")
    assert is_auxiliary_path("setup.py")
    assert is_auxiliary_path("examples/demo.py")
    assert not is_auxiliary_path("src/requests/api.py")


def test_library_paths_excludes_non_package_folders():
    """Mirrors gruns/icecream: bug-repro scripts in a non-package folder."""
    paths = [
        "icecream/__init__.py",
        "icecream/icecream.py",
        "failures-to-investigate/freshsales.py",
        "failures-to-investigate/freshsales2.py",
        "setup.py",
    ]
    assert library_paths(paths) == {"icecream/__init__.py", "icecream/icecream.py"}


def test_library_paths_src_layout_and_nested_packages():
    paths = [
        "src/pkg/__init__.py",
        "src/pkg/core.py",
        "src/pkg/sub/__init__.py",
        "src/pkg/sub/deep.py",
        "src/pkg/notes/scratch.py",   # notes/ has no __init__.py
        "benchmarks/run.py",
    ]
    assert library_paths(paths) == {
        "src/pkg/__init__.py", "src/pkg/core.py",
        "src/pkg/sub/__init__.py", "src/pkg/sub/deep.py",
    }


def test_library_paths_single_module_repo_falls_back():
    """Repos like kennethreitz/crayons are one top-level file, no packages."""
    assert library_paths(["crayons.py", "setup.py", "docs/conf.py"]) == {"crayons.py"}
