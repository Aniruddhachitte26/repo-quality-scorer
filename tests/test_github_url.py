import pytest

from repo_scorer.ingest.github import parse_github_url


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/psf/requests",
        "https://github.com/psf/requests/",
        "https://github.com/psf/requests.git",
        "http://www.github.com/psf/requests",
        "github.com/psf/requests",
    ],
)
def test_parses_valid_urls(url):
    ref = parse_github_url(url)
    assert ref.owner == "psf"
    assert ref.name == "requests"
    assert ref.clone_url == "https://github.com/psf/requests.git"


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/psf/requests",
        "https://github.com/psf",
        "https://github.com/psf/requests/tree/main",
        "not a url",
    ],
)
def test_rejects_invalid_urls(url):
    with pytest.raises(ValueError):
        parse_github_url(url)
