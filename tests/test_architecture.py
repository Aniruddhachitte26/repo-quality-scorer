import ast

from repo_scorer.analyzers.architecture import (
    build_import_graph,
    find_cycles,
    module_level_imports,
    module_name,
    resolve_imports,
)


def test_module_name():
    assert module_name("src/requests/models.py") == "requests.models"
    assert module_name("requests/__init__.py") == "requests"
    assert module_name("pkg/sub/mod.py") == "pkg.sub.mod"
    assert module_name("lib/pkg/__init__.py") == "pkg"


def test_skips_type_checking_and_function_level_imports():
    src = (
        "import os\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from pkg import hidden\n"
        "try:\n"
        "    import fast\n"
        "except ImportError:\n"
        "    import slow\n"
        "def f():\n"
        "    import lazy\n"
    )
    mods = {m for m, _, _ in module_level_imports(ast.parse(src))}
    assert mods == {"os", "typing", "fast", "slow"}


def test_resolves_relative_and_absolute_imports():
    modules = {"pkg", "pkg.models", "pkg.utils", "pkg.sub", "pkg.sub.deep"}
    imports = [
        (None, ["utils"], 1),          # from . import utils
        ("models", ["Model"], 1),      # from .models import Model
        ("pkg.sub.deep", ["x"], 0),    # from pkg.sub.deep import x
        ("os", [], 0),                 # import os  (external, ignored)
    ]
    assert resolve_imports("pkg.api", False, imports, modules) == {
        "pkg.utils", "pkg.models", "pkg.sub.deep",
    }
    # from .. import models, from inside pkg.sub.deep
    assert resolve_imports("pkg.sub.deep", False, [(None, ["models"], 2)], modules) == {
        "pkg.models"
    }


def test_detects_cycle_but_not_type_checking_cycle():
    sources = {
        "pkg/__init__.py": "",
        "pkg/a.py": "from . import b\n",
        "pkg/b.py": "from . import a\n",                       # real cycle a <-> b
        "pkg/c.py": "from . import d\n",
        "pkg/d.py": "from typing import TYPE_CHECKING\n"
                    "if TYPE_CHECKING:\n    from . import c\n",  # not a runtime cycle
    }
    cycles = find_cycles(build_import_graph(sources))
    assert cycles == [["pkg.a", "pkg.b"]]
