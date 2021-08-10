"""
Test /certificate
"""
from django.urls.base import reverse_lazy
from rest_framework import status
from ..mixins import AdmissionsTestCase


class CertificateTestSuite(AdmissionsTestCase):
    """Test /certificate"""
    def test_certificate_slug_academy_id_syllabus_without_auth(self):
        """Test /certificate without auth"""
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': 'they-killed-kenny',
                               'academy_id': 1
                           })
        response = self.client.get(url)
        json = response.json()

        self.assertEqual(
            json, {
                'detail': 'Authentication credentials were not provided.',
                'status_code': status.HTTP_401_UNAUTHORIZED
            })
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.all_specialty_mode_dict(), [])

    def test_certificate_slug_academy_id_syllabus_without_capability(self):
        """Test /certificate without auth"""
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': 'they-killed-kenny',
                               'academy_id': 1
                           })
        self.generate_models(authenticate=True)
        response = self.client.get(url)
        json = response.json()
        expected = {
            'status_code': 403,
            'detail': "You (user: 1) don't have this capability: read_syllabus for academy 1"
        }

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.all_specialty_mode_dict(), [])

    def test_certificate_slug_academy_id_syllabus_without_data(self):
        """Test /certificate without auth"""
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': 'they-killed-kenny',
                               'academy_id': 1
                           })
        model = self.generate_models(authenticate=True,
                                     profile_academy=True,
                                     capability='read_syllabus',
                                     role='potato')
        response = self.client.get(url)
        json = response.json()
        expected = {'status_code': 404, 'detail': 'specialty-mode-not-found'}

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.all_specialty_mode_dict(), [])

    def test_certificate_slug_academy_id_syllabus_without_syllabus(self):
        """Test /certificate without auth"""
        model = self.generate_models(authenticate=True,
                                     specialty_mode=True,
                                     profile_academy=True,
                                     capability='read_syllabus',
                                     role='potato')
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': model['specialty_mode'].slug,
                               'academy_id': 1
                           })
        response = self.client.get(url)
        json = response.json()
        expected = {'detail': 'syllabus-not-found', 'status_code': 404}

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.assertEqual(self.all_syllabus_version_dict(), [])

    def test_certificate_slug_academy_id_syllabus(self):
        """Test /certificate without auth"""
        model = self.generate_models(authenticate=True,
                                     specialty_mode=True,
                                     profile_academy=True,
                                     capability='read_syllabus',
                                     role='potato',
                                     syllabus_version=True,
                                     syllabus=True)
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': model['specialty_mode'].slug,
                               'academy_id': 1
                           })
        response = self.client.get(url)
        json = response.json()
        expected = [{
            'json': model.syllabus_version.json,
            'version': model.syllabus_version.version,
            'created_at': self.datetime_to_iso(model.syllabus_version.created_at),
            'updated_at': self.datetime_to_iso(model.syllabus_version.updated_at),
            'syllabus': model.syllabus.id,
        }]

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(self.all_syllabus_version_dict(), [{
            **self.model_to_dict(model, 'syllabus_version')
        }])

    def test_certificate_slug_academy_id_syllabus_post_without_capabilities(self):
        """Test /certificate without auth"""
        model = self.generate_models(authenticate=True)
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': 'they-killed-kenny',
                               'academy_id': 1
                           })
        data = {}
        response = self.client.post(url, data)
        json = response.json()
        expected = {
            'detail': "You (user: 1) don't have this capability: crud_syllabus "
            'for academy 1',
            'status_code': 403
        }

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.all_syllabus_version_dict(), [])

    def test_certificate_slug_academy_id_syllabus_post_with_bad_slug(self):
        """Test /certificate without auth"""
        self.headers(academy=1)
        model = self.generate_models(authenticate=True,
                                     profile_academy=True,
                                     capability='crud_syllabus',
                                     role='potato')
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': 'they-killed-kenny',
                               'academy_id': 1
                           })
        data = {}
        response = self.client.post(url, data)
        json = response.json()
        expected = {'detail': 'specialty-mode-not-found', 'status_code': 404}

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.all_syllabus_version_dict(), [])

    def test_certificate_slug_academy_id_syllabus_post_without_syllabus(self):
        """Test /certificate without auth"""
        self.headers(academy=1)
        model = self.generate_models(authenticate=True,
                                     profile_academy=True,
                                     capability='crud_syllabus',
                                     role='potato',
                                     specialty_mode=True,
                                     specialty_mode_time_slot=True)
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': model['specialty_mode'].slug,
                               'academy_id': 1
                           })
        data = {}
        response = self.client.post(url, data)
        json = response.json()
        expected = {'detail': 'missing-syllabus-in-request', 'status_code': 400}

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.all_syllabus_version_dict(), [])

    def test_certificate_slug_academy_id_syllabus_post_without_required_fields(self):
        """Test /certificate without auth"""
        self.headers(academy=1)
        model = self.generate_models(authenticate=True,
                                     profile_academy=True,
                                     capability='crud_syllabus',
                                     role='potato',
                                     syllabus=True,
                                     specialty_mode=True,
                                     specialty_mode_time_slot=True)
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': model['specialty_mode'].slug,
                               'academy_id': 1
                           })
        data = {'syllabus': 1}
        response = self.client.post(url, data)
        json = response.json()
        expected = {'json': ['This field is required.']}

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.all_syllabus_version_dict(), [])

    def test_certificate_slug_academy_id_syllabus__post__without_syllabus(self):
        """Test /certificate without auth"""
        self.headers(academy=1)
        model = self.generate_models(authenticate=True,
                                     profile_academy=True,
                                     capability='crud_syllabus',
                                     role='potato',
                                     specialty_mode=True,
                                     specialty_mode_time_slot=True)
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': model['specialty_mode'].slug,
                               'academy_id': 1
                           })
        data = {
            'certificate': model['specialty_mode'].id,
            'json': {
                'slug': 'they-killed-kenny'
            },
            'syllabus': 1,
        }
        response = self.client.post(url, data, format='json')
        json = response.json()
        expected = {'detail': 'syllabus-field-missing', 'status_code': 400}

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.all_syllabus_version_dict(), [])

    def test_certificate_slug_academy_id_syllabus_post(self):
        """Test /certificate without auth"""
        self.headers(academy=1)
        model = self.generate_models(authenticate=True,
                                     profile_academy=True,
                                     capability='crud_syllabus',
                                     role='potato',
                                     syllabus=True,
                                     specialty_mode=True,
                                     specialty_mode_time_slot=True)
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': model['specialty_mode'].slug,
                               'academy_id': 1
                           })
        data = {
            'certificate': model['specialty_mode'].id,
            'json': {
                'slug': 'they-killed-kenny'
            },
            'syllabus': 1,
        }
        response = self.client.post(url, data, format='json')
        json = response.json()

        expected = {
            'json': data['json'],
            'syllabus': 1,
            'version': 1,
        }

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.all_syllabus_version_dict(), [{
            'id': 1,
            'json': {
                'slug': 'they-killed-kenny'
            },
            'version': 1,
            'syllabus_id': 1,
        }])

    def test_certificate_slug_academy_id_syllabus__post__with_cohort_and_syllabus(self):
        """Test /certificate without auth"""
        self.headers(academy=1)
        model = self.generate_models(authenticate=True,
                                     profile_academy=True,
                                     capability='crud_syllabus',
                                     role='potato',
                                     specialty_mode=True,
                                     specialty_mode_time_slot=True,
                                     cohort=True,
                                     syllabus=True,
                                     syllabus_version=True)
        url = reverse_lazy('admissions:certificate_slug_academy_id_syllabus',
                           kwargs={
                               'certificate_slug': model['specialty_mode'].slug,
                               'academy_id': 1
                           })
        data = {
            'certificate': model['specialty_mode'].id,
            'json': {
                'slug': 'they-killed-kenny'
            },
            'syllabus': 1,
        }
        response = self.client.post(url, data, format='json')
        json = response.json()

        expected = {
            'json': data['json'],
            'syllabus': 1,
            'version': model.syllabus_version.version + 1,
        }

        self.assertEqual(json, expected)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.all_syllabus_version_dict(), [{
            **self.model_to_dict(model, 'syllabus_version')
        }, {
            'id': 2,
            'json': {
                'slug': 'they-killed-kenny'
            },
            'syllabus_id': 1,
            'version': model.syllabus_version.version + 1,
        }])
