# Remediation report: psf/requests

Score **89.5 / 100 (grade B)** | commit `611c6162cb` | 2026-10-01 07:24 UTC | model `claude-haiku-4-5-20251001`

## Summary
The requests library scores 89.5/100 (grade B), with solid documentation and dependencies but structural issues in code complexity and test coverage. The main pain points are high cyclomatic complexity in core request-handling functions, four god classes with excessive responsibilities, and 2.1% code duplication affecting maintainability.

## Top priorities

1. **Refactor RequestEncodingMixin._encode_files (complexity 21)** — src/requests/models.py:183
   - **Evidence**: Highest complexity function (21, threshold is 10), 69 lines handling multipart file encoding with nested loops and multiple conditional branches.
   - **Why it matters**: Complex encoding logic is error-prone and difficult to maintain. This function handles critical request preparation.
   - **Fix**: Extract the file tuple unpacking logic (lines 221-243) into a helper function `_extract_file_data()`, and the field iteration logic (lines 203-219) into `_encode_fields()`. This breaks the single responsibility and reduces nesting.
   - **Effort**: Medium

2. **Simplify HTTPAdapter.send error handling (complexity 20)** — src/requests/adapters.py:634
   - **Evidence**: Second-highest complexity (20, threshold 10), 115 lines with 7 nested exception handlers (lines 695-746) for urllib3 errors.
   - **Why it matters**: Exception translation is critical for error propagation; nested handlers are hard to trace and modify.
   - **Fix**: Extract the exception handling block into a private method `_handle_urllib3_errors(e, request)` that consolidates the error type checking and re-raising logic. This reduces the send() method to ~50 lines.
   - **Effort**: Medium

3. **Reduce RequestsCookieJar god class (25 methods, 286 lines)** — src/requests/cookies.py:191
   - **Evidence**: Exceeds god class threshold (≥20 methods), mixing cookie jar manipulation, domain/path queries, and state serialization.
   - **Why it matters**: God classes are harder to test, maintain, and extend. This mixes multiple concerns.
   - **Fix**: Extract domain/path listing methods (`list_domains`, `list_paths`) and find operations (`_find`, `_find_no_duplicates`) into a separate `CookieQuery` helper class. Extract state management (`__getstate__`, `__setstate__`) into a mixin.
   - **Effort**: Large

4. **Address test coverage gap** — src/requests/adapters.py:512, src/requests/utils.py:633, src/requests/__init__.py:60
   - **Evidence**: 82.5% reachability (target 90%), with HTTPAdapter.get_connection (42 lines), get_unicode_from_response (39 lines), and check_compatibility (37 lines) untested.
   - **Why it matters**: Untested public APIs hide latent bugs and don't guarantee correctness of edge cases.
   - **Fix**: Add test cases for `HTTPAdapter.get_connection` covering connection pool reuse scenarios, `get_unicode_from_response` with various response encodings, and `check_compatibility` with edge-case version strings.
   - **Effort**: Medium

5. **Eliminate code duplication (94 redundant lines, 2.1%)** — src/requests/models.py
   - **Evidence**: RequestEncodingMixin._encode_files (similarity 0.658 with PreparedRequest.prepare_body) shares file/data iteration patterns; _encode_params (0.539 similarity) duplicates parameter encoding logic.
   - **Why it matters**: Duplication means bug fixes and enhancements must be applied in multiple places.
   - **Fix**: Extract shared type-checking and encoding patterns (bytes vs. string handling) into utility functions like `_encode_value(val)` and `_normalize_key(key)` in utils.py, reused by both methods.
   - **Effort**: Small

## Quick wins

- **Extract timeout resolution logic from HTTPAdapter.send** (lines 681-693): Move the 3-way timeout parsing into a helper `_resolve_timeout(timeout)` function. Reduces send() complexity by 1-2 points with zero functional risk.
- **Add docstrings to HTTPAdapter exception translation**: Document which urllib3 exceptions map to which requests exceptions in HTTPAdapter.send. Improves maintainability without code changes.
- **Add @lru_cache to repeated utility calls**: Functions like `unicode_is_ascii()` and `to_native_string()` called in hot paths (prepare_url) could benefit from caching, improving performance with minimal code change.

## Caveats

Test reachability is determined statically by name mention, not execution. Indirect framework invocation (e.g., WSGI adapters, callback hooks) may be missed, understating actual coverage. Complexity scores reflect McCabe cycles in the AST; they don't account for cognitive load from nested data structures or implicit dependencies, which may make some functions harder than the score suggests.
