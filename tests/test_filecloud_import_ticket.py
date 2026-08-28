import base64
import json
from types import SimpleNamespace

import pytest
from flask import Flask
from routes import projects_api as routes


@pytest.fixture()
def ticket_client(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, VECTOPLAN_FILECLOUD_ACCESS_TICKET_SECRET='test-only-filecloud-ticket-key-with-32-bytes')
    app.register_blueprint(routes.bp)
    monkeypatch.setattr(routes, '_require_persistent_context', lambda: None)
    monkeypatch.setattr(routes, '_current_user_id_optional', lambda: 7)
    monkeypatch.setattr(routes, '_current_auth_user_id', lambda: 'auth-7')
    monkeypatch.setattr(routes, '_current_email', lambda: 'test@example.test')
    monkeypatch.setattr(routes, '_current_user_context_dict', lambda **kw: {'authenticated': True})
    monkeypatch.setattr(routes, 'resolve_project', lambda _: SimpleNamespace(public_id='prj_test_12345678', name='Vergabe', is_configured=False))
    monkeypatch.setattr(routes, '_require_project_permission_checked', lambda *args, **kw: None)
    monkeypatch.setattr(routes, '_project_read_only', lambda *args: False)
    monkeypatch.setattr(routes, 'serialize_project_permissions', lambda *args, **kw: dict(role='owner', can_view=True, can_edit=True, can_manage=True))
    return app.test_client()


def test_persisted_project_can_import_before_geometry_setup(ticket_client):
    response = ticket_client.get('/v1/projects/prj_test_12345678/filecloud-access', headers={'X-Vectoplan-User-Id':'attacker'})
    assert response.status_code == 200
    body = response.json
    claims = json.loads(base64.urlsafe_b64decode(body['ticket'].split('.')[0] + '==='))
    assert claims['project_id'] == body['project_id'] == 'prj_test_12345678'
    assert claims['auth_user_id'] == 'auth-7' and claims['can_edit'] is True
    assert claims['exp'] - claims['iat'] == 120
    assert 'no-store' in response.headers['Cache-Control']


def test_guest_never_receives_filecloud_ticket(ticket_client, monkeypatch):
    monkeypatch.setattr(routes, '_require_persistent_context', lambda: ({'error':'login_required'},401))
    response = ticket_client.get('/v1/projects/prj_test_12345678/filecloud-access')
    assert response.status_code == 401 and 'ticket' not in response.json


def test_readonly_project_cannot_receive_write_ticket(ticket_client, monkeypatch):
    monkeypatch.setattr(routes, '_project_read_only', lambda *args: True)
    response = ticket_client.get('/v1/projects/prj_test_12345678/filecloud-access')
    claims = json.loads(base64.urlsafe_b64decode(response.json['ticket'].split('.')[0] + '==='))
    assert claims['can_edit'] is False and claims['can_manage'] is False


def test_public_viewer_is_not_project_membership(ticket_client, monkeypatch):
    monkeypatch.setattr(routes, 'serialize_project_permissions', lambda *args, **kw: dict(role='public', can_view=True, can_edit=False))
    assert ticket_client.get('/v1/projects/prj_test_12345678/filecloud-access').status_code == 403
