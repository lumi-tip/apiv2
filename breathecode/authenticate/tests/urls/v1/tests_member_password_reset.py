"""
Test cases for POST /v1/auth/member/<id>/password/reset
"""

import os
from unittest.mock import MagicMock

import pytest
from django.urls.base import reverse_lazy
from rest_framework import status
from rest_framework.test import APIClient

from breathecode.authenticate.models import Token
from breathecode.tests.mixins.breathecode_mixin.breathecode import Breathecode


@pytest.fixture(autouse=True)
def setup(db, monkeypatch):
    monkeypatch.setenv("API_URL", "http://localhost:8000")
    monkeypatch.setattr("breathecode.notify.actions.send_email_message", MagicMock(return_value=True))
    yield


def test_no_auth(bc: Breathecode, client: APIClient):
    url = reverse_lazy("authenticate:member_password_reset", kwargs={"profileacademy_id": 1})
    response = client.post(url)

    assert response.json() == {
        "detail": "Authentication credentials were not provided.",
        "status_code": status.HTTP_401_UNAUTHORIZED,
    }
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_no_capability(bc: Breathecode, client: APIClient):
    model = bc.database.create(user=1)
    client.force_authenticate(user=model.user)

    url = reverse_lazy("authenticate:member_password_reset", kwargs={"profileacademy_id": 1})
    response = client.post(url, HTTP_ACADEMY=1)

    assert response.json() == {
        "detail": "You (user: 1) don't have this capability: send_reset_password for academy 1",
        "status_code": 403,
    }
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_member_not_found(bc: Breathecode, client: APIClient):
    model = bc.database.create(user=1, role=1, capability="send_reset_password", profile_academy=1, academy=1)
    client.force_authenticate(user=model.user)

    url = reverse_lazy("authenticate:member_password_reset", kwargs={"profileacademy_id": 999})
    response = client.post(url, HTTP_ACADEMY=1)

    json = response.json()
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert json["detail"] == "Member not found"
    assert json["status_code"] == 400


def test_member_without_user(bc: Breathecode, client: APIClient):
    model = bc.database.create(user=1, role=1, capability="send_reset_password", profile_academy=1, academy=1)
    orphan = bc.database.create(
        profile_academy={"user": None, "email": "legacy@test.com"},
        academy=model.academy,
        role=model.role,
    )
    client.force_authenticate(user=model.user)

    url = reverse_lazy("authenticate:member_password_reset", kwargs={"profileacademy_id": orphan.profile_academy.id})
    response = client.post(url, HTTP_ACADEMY=1)

    assert response.json() == {
        "detail": "member-without-user",
        "status_code": 400,
    }
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_without_existing_temporal_token(bc: Breathecode, client: APIClient):
    """Legacy users often have no temporal token; this used to 500 in TokenSmallSerializer."""
    model = bc.database.create(user=1, role=1, capability="send_reset_password", profile_academy=1, academy=1)
    client.force_authenticate(user=model.user)

    url = reverse_lazy("authenticate:member_password_reset", kwargs={"profileacademy_id": model.profile_academy.id})
    response = client.post(url, HTTP_ACADEMY=1)
    json = response.json()

    token, _ = Token.get_or_create(user=model.user, token_type="temporal")

    assert response.status_code == status.HTTP_200_OK
    assert json["key"] == str(token)
    assert json["reset_password_url"] == os.getenv("API_URL") + f"/v1/auth/password/{token}"
    assert json["user"]["id"] == model.user.id
    assert json["user"]["email"] == model.user.email


def test_email_not_sent(bc: Breathecode, client: APIClient, monkeypatch):
    monkeypatch.setattr("breathecode.notify.actions.send_email_message", MagicMock(return_value=False))
    model = bc.database.create(user=1, role=1, capability="send_reset_password", profile_academy=1, academy=1)
    client.force_authenticate(user=model.user)

    url = reverse_lazy("authenticate:member_password_reset", kwargs={"profileacademy_id": model.profile_academy.id})
    response = client.post(url, HTTP_ACADEMY=1)

    json = response.json()
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert json["detail"] == "Reset password token could not be sent"
