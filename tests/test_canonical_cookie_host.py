from app import _canonical_local_cookie_url


def test_localhost_app_url_is_canonicalized_to_the_auth_cookie_host() -> None:
    assert _canonical_local_cookie_url(
        "localhost:5103",
        "http://localhost:5103/ui/editor?allow_embed=1",
    ) == "http://127.0.0.1:5103/ui/editor?allow_embed=1"


def test_authenticated_localhost_app_url_keeps_the_cookie_host() -> None:
    assert _canonical_local_cookie_url(
        "localhost:5103",
        "http://localhost:5103/ui/editor?allow_embed=1",
        auth_session_present=True,
    ) is None


def test_non_local_and_non_app_hosts_are_not_rewritten() -> None:
    assert _canonical_local_cookie_url(
        "app.vectoplan.com",
        "https://app.vectoplan.com/ui/editor",
    ) is None
    assert _canonical_local_cookie_url(
        "localhost:5100",
        "http://localhost:5100/editor",
    ) is None
