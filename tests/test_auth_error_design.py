from flask import Flask

from routes.ui import projects


def test_auth_unavailable_page_uses_platform_error_design() -> None:
    app = Flask(__name__)

    with app.test_request_context("/"):
        response = projects._auth_problem_html_response(
            title="Auth-Service nicht erreichbar",
            message="vectoplan-auth ist nicht erreichbar.",
            status_code=503,
            reason="connection_refused",
        )

    html = response.get_data(as_text=True)

    assert response.status_code == 503
    assert 'class="header-logo"' in html
    assert 'class="error-number"' in html
    assert ">503<" in html
    assert "#0d7f97" in html
    assert "#f5f9fa" in html
    assert "Erneut versuchen" in html
    assert "connection_refused" in html
