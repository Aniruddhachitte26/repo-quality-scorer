# Remediation report: psf/requests

Score **89.5 / 100 (grade B)** | commit `611c6162cb` | 2026-10-01 06:18 UTC | model `claude-haiku-4-5-20251001`

## Summary

The requests repository scores 89.5/100 (Grade B), with solid performance in dependencies and documentation but trailing in architecture and duplication. The biggest quality drains are high cyclomatic complexity (5.2% of functions exceed threshold), four god classes with 15–25 methods each, 2.1% duplicated lines, and 17.5% of public functions unreachable from tests. Addressing the top 3–4 complexity hotspots and improving test coverage of critical utility functions will yield the highest impact.

## Top priorities

1. **Refactor `HTTPDigestAuth.build_digest_header` (src/requests/auth.py:157)** — Complexity 19, 110 LOC. This method contains 4 nearly-identical hash function definitions (lines 176–205) that could be consolidated into a single hash factory function. Extract the hash algorithm selection into a helper that returns the appropriate hash function, eliminating the repetitive `if/elif` blocks. This will reduce complexity by ~5–6 points and improve maintainability. *Effort: Small*

2. **Extract timeout parsing logic from `HTTPAdapter.send` (src/requests/adapters.py:634)** — Complexity 20, 115 LOC. Lines 681–693 contain a self-contained timeout tuple parsing block that is separate from the core HTTP logic. Extract this into a private method `_resolve_timeout(timeout)` to reduce send's complexity and improve reusability. The exception handling hierarchy (lines 695–746) can also be simplified by consolidating similar error mappings. *Effort: Medium*

3. **Simplify `RequestEncodingMixin._encode_files` (src/requests/models.py:183)** — Complexity 21, 69 LOC (worst complexity in codebase). Lines 221–234 handle file tuple unpacking with repetitive variable assignments (`fn`, `fp`, `ft`, `fh`). Extract file tuple parsing into a helper function `_parse_file_tuple(v, k)` to return `(fn, fp, ft, fh)` cleanly. Additionally, consolidate the data conversion logic (lines 236–243) into a helper like `_read_file_data(fp)` to separate concerns. *Effort: Medium*

4. **Add test coverage for `HTTPAdapter.get_connection` (src/requests/adapters.py:512)** — 42 LOC, currently unreachable. This critical method is untested despite being used during request sending. Add integration tests that exercise TLS context handling with various proxy/cert configurations. Also add tests for `get_unicode_from_response` (src/requests/utils.py:633, 39 LOC) which handles charset detection. *Effort: Medium*

5. **Consolidate `super_len` (src/requests/utils.py:160)** — Complexity 16, 69 LOC. The nested try/except blocks (lines 176–184, 201–223) have redundant logic for file length detection. Extract file-specific length detection into a helper `_get_file_length(o)` to improve readability and reduce nesting depth from 4 to 2 levels. *Effort: Small*

## Quick wins

- **Unify hash function helpers in `HTTPDigestAuth.build_digest_header`** (lines 174–205): Replace the four identical-structure hash function defs with a single factory `_get_hash_func(algorithm)` that returns the appropriate callable. Reduces lines by ~20 and cuts complexity by ~4.

- **Extract file tuple unpacking in `_encode_files`** (lines 221–234): Create a named function `_parse_file_tuple(v, k)` to return a 4-tuple `(filename, fileobj, content_type, headers)`. Improves readability without changing logic.

- **Add docstring tests or unit tests for `parse_list_header` and `from_key_val_list`** (src/requests/utils.py): These are header/list parsing utilities (29 and 27 LOC respectively) that appear untested. A few parametrized pytest cases per function would close coverage gaps with minimal effort.

## Caveats

Test reachability is static—functions invoked indirectly via callbacks, property decorators, or framework mechanisms (e.g., urllib3 connection pooling) may be marked unreachable even if exercised at runtime. The 17.5% untested gap likely includes some framework-driven code. Cyclomatic complexity counts decision branches; extracting helper functions reduces CC but does not reduce logical complexity, so refactoring must also consolidate control flow logic, not just move code.
