from repo_scorer.pipeline import read_repo_list


def test_read_repo_list_skips_comments_blanks_and_duplicates():
    text = (
        "# header comment\n"
        "https://github.com/psf/requests\n"
        "\n"
        "https://github.com/pallets/flask   # inline comment\n"
        "https://github.com/psf/requests\n"
        "   \n"
    )
    assert read_repo_list(text) == [
        "https://github.com/psf/requests",
        "https://github.com/pallets/flask",
    ]
