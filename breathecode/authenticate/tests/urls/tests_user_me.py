"""
Test cases for /user
"""
import pytz, datetime
from django.urls.base import reverse_lazy
from rest_framework import status
from ..mixins.new_auth_test_case import AuthTestCase

last_permission_id = 488


def get_permission_serializer(permission):
    return {
        'codename': permission.codename,
        'name': permission.name,
    }


def get_serializer(self, user, credentials_github=None, profile_academies=[], profile=None, permissions=[]):
    return {
        'id':
        user.id,
        'email':
        user.email,
        'first_name':
        user.first_name,
        'last_name':
        user.last_name,
        'permissions': [get_permission_serializer(x) for x in permissions],
        'github': {
            'avatar_url': credentials_github.avatar_url,
            'name': credentials_github.name,
            'username': credentials_github.username,
        } if credentials_github else None,
        'profile': {
            'avatar_url': profile.avatar_url,
        } if profile else None,
        'roles': [{
            'academy': {
                'id': profile_academy.academy.id,
                'name': profile_academy.academy.name,
                'slug': profile_academy.academy.slug,
                'timezone': profile_academy.academy.timezone,
            },
            'created_at': self.bc.datetime.to_iso_string(profile_academy.created_at),
            'id': profile_academy.id,
            'role': profile_academy.role.slug,
        } for profile_academy in profile_academies],
    }


class AuthenticateTestSuite(AuthTestCase):
    """Authentication test suite"""
    """
    🔽🔽🔽 Auth
    """

    def test_user_me__without_auth(self):
        """Test /user/me without auth"""
        url = reverse_lazy('authenticate:user_me')
        response = self.client.get(url)

        json = response.json()
        expected = {
            'detail': 'Authentication credentials were not provided.',
            'status_code': status.HTTP_401_UNAUTHORIZED,
        }

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    """
    🔽🔽🔽 Get
    """

    def test_user_me(self):
        """Test /user/me"""
        model = self.generate_models(authenticate=True)

        url = reverse_lazy('authenticate:user_me')
        response = self.client.get(url)

        json = response.json()
        expected = get_serializer(self, model.user)

        self.assertEqual(json, expected)

    """
    🔽🔽🔽 Get with CredentialsGithub
    """

    def test_user_me__with_github_credentials(self):
        """Test /user/me"""
        model = self.generate_models(authenticate=True, credentials_github=True)

        url = reverse_lazy('authenticate:user_me')
        response = self.client.get(url)

        json = response.json()
        expected = get_serializer(self, model.user, credentials_github=model.credentials_github)

        self.assertEqual(json, expected)

    """
    🔽🔽🔽 Get with ProfileAcademy
    """

    def test_user_me__with_profile_academy(self):
        """Test /user/me"""
        model = self.generate_models(authenticate=True, profile_academy=True)

        url = reverse_lazy('authenticate:user_me')
        response = self.client.get(url)

        json = response.json()
        expected = get_serializer(self, model.user, profile_academies=[model.profile_academy])

        self.assertEqual(json, expected)

    """
    🔽🔽🔽 Get with Profile
    """

    def test_user_me__with_profile(self):
        """Test /user/me"""
        model = self.generate_models(authenticate=True, profile=True)

        url = reverse_lazy('authenticate:user_me')
        response = self.client.get(url)

        json = response.json()
        expected = get_serializer(self, model.user, profile=model.profile)

        self.assertEqual(json, expected)

    """
    🔽🔽🔽 Get with Profile and Permission
    """

    def test_user_me__with_profile__with_permission(self):
        """Test /user/me"""
        model = self.generate_models(authenticate=True, profile=True, permission=1)

        url = reverse_lazy('authenticate:user_me')
        response = self.client.get(url)

        json = response.json()
        expected = get_serializer(self, model.user, profile=model.profile, permissions=[])

        self.assertEqual(json, expected)

    """
    🔽🔽🔽 Get with Profile and Permission
    """

    def test_user_me__with_profile__one_group_with_one_permission(self):
        """Test /user/me"""
        model = self.generate_models(authenticate=True, profile=True, permission=1, group=1)

        url = reverse_lazy('authenticate:user_me')
        response = self.client.get(url)

        json = response.json()
        expected = get_serializer(self, model.user, profile=model.profile, permissions=[model.permission])

        self.assertEqual(json, expected)

    """
    🔽🔽🔽 Get with Profile and three Group with one Permission
    """

    def test_user_me__with_profile__three_groups_with_one_permission(self):
        """Test /user/me"""
        model = self.generate_models(authenticate=True, profile=True, permission=1, group=3)

        url = reverse_lazy('authenticate:user_me')
        response = self.client.get(url)

        json = response.json()
        expected = get_serializer(self, model.user, profile=model.profile, permissions=[model.permission])

        self.assertEqual(json, expected)

    """
    🔽🔽🔽 Get with Profile and two Group with four Permission
    """

    def test_user_me__with_profile__two_groups_with_four_permissions(self):
        """Test /user/me"""

        groups = [
            {
                'permissions': [last_permission_id + 1, last_permission_id + 2],
            },
            {
                'permissions': [last_permission_id + 3, last_permission_id + 4],
            },
        ]
        model = self.generate_models(authenticate=True, profile=True, permission=4, group=groups)

        url = reverse_lazy('authenticate:user_me')
        response = self.client.get(url)

        json = response.json()
        expected = get_serializer(self, model.user, profile=model.profile, permissions=model.permission)

        self.assertEqual(json, expected)
