"""
Django settings for breathecode project.

Generated by 'django-admin startproject' using Django 3.0.7.
For more information on this file, see
https://docs.djangoproject.com/en/3.0/topics/settings/
For the full list of settings and their values, see
https://docs.djangoproject.com/en/3.0/ref/settings/
"""

import os
from pathlib import Path
from typing import Optional
# TODO: decouple file storage from django
# from time import time
import django_heroku
import dj_database_url
import json
import logging
from django.contrib.messages import constants as messages
from django.utils.log import DEFAULT_LOGGING

from breathecode.setup import configure_redis

from django_redis.client import DefaultClient
from redis import Redis
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import ConnectionError, ResponseError, TimeoutError
import socket
import itertools

redis_client_exceptions = (TimeoutError, ResponseError, ConnectionError, socket.timeout)

# TODO: decouple file storage from django
# from django.utils.http import http_date

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

DATABASE_URL = os.environ.get('DATABASE_URL')
ENVIRONMENT = os.environ.get('ENV')

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '5ar3h@ha%y*dc72z=8-ju7@4xqm0o59*@k*c2i=xacmy2r=%4a'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = (ENVIRONMENT == 'development' or ENVIRONMENT == 'test')

ALLOWED_HOSTS = []

INSTALLED_APPS = []

# TODO: decouple file storage from django
# if ENVIRONMENT == 'test':
#     INSTALLED_APPS += ['whitenoise.runserver_nostatic']

# Application definition
INSTALLED_APPS += [
    # TODO: decouple file storage from django
    'whitenoise.runserver_nostatic',
    'breathecode.admin_styles',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django.contrib.postgres',
    'django.contrib.admindocs',
    'rest_framework',
    'phonenumber_field',
    'corsheaders',
    'breathecode.notify',
    'breathecode.authenticate',
    'breathecode.monitoring',
    'breathecode.admissions',
    'breathecode.events',
    'breathecode.feedback',
    'breathecode.assignments',
    'breathecode.marketing',
    'breathecode.freelance',
    'breathecode.certificate',
    'breathecode.media',
    'breathecode.assessment',
    'breathecode.registry',
    'breathecode.mentorship',
    'breathecode.career',
    'breathecode.commons',
    'breathecode.payments',
    'breathecode.provisioning',
    'explorer',
    'graphene_django',
]

GRAPHENE = {'SCHEMA': 'breathecode.schema.schema'}

if os.getenv('ALLOW_UNSAFE_CYPRESS_APP') or ENVIRONMENT == 'test':
    INSTALLED_APPS.append('breathecode.cypress')

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS':
    'rest_framework.schemas.openapi.AutoSchema',
    'DEFAULT_VERSIONING_CLASS':
    'rest_framework.versioning.NamespaceVersioning',
    'DEFAULT_PAGINATION_CLASS':
    'breathecode.utils.HeaderLimitOffsetPagination',
    'EXCEPTION_HANDLER':
    'breathecode.utils.breathecode_exception_handler',
    'PAGE_SIZE':
    100,
    'DEFAULT_VERSION':
    'v1',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'breathecode.authenticate.authentication.ExpiringTokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
        'rest_framework_csv.renderers.CSVRenderer',
    ),
}

MIDDLEWARE = []

if ENVIRONMENT != 'production':
    import resource

    class MemoryUsageMiddleware:

        def __init__(self, get_response):
            self.get_response = get_response

        def __call__(self, request):
            start_mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            response = self.get_response(request)
            end_mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            delta_mem = end_mem - start_mem
            print(f'Memory usage for this request: {delta_mem} KB')
            response['X-Memory-Usage'] = f'{delta_mem} KB'
            return response

    MIDDLEWARE += [
        'breathecode.settings.MemoryUsageMiddleware',
    ]

MIDDLEWARE += [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',

    # Cache
    # 'django.middleware.cache.UpdateCacheMiddleware',
    'django.middleware.common.CommonMiddleware',
    # 'django.middleware.cache.FetchFromCacheMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    #'breathecode.utils.admin_timezone.TimezoneMiddleware',
    # 'django.middleware.http.ConditionalGetMiddleware',
]

DISABLE_SERVER_SIDE_CURSORS = True  # required when using pgbouncer's pool_mode=transaction

AUTHENTICATION_BACKENDS = ('django.contrib.auth.backends.ModelBackend', )

ROOT_URLCONF = 'breathecode.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'breathecode.wsgi.application'

# Password validation
# https://docs.djangoproject.com/en/3.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Disable Django's logging setup
LOGGING_CONFIG = None

IS_TEST_ENV = os.getenv('ENV') == 'test'
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

# this prevent the duplications of logs because heroku redirect the output to Coralogix
if IS_TEST_ENV:
    LOGGING_HANDLERS = ['console']

else:
    LOGGING_HANDLERS = ['coralogix', 'console']

logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            # exact format is not important, this is the minimum information
            'format': '[%(asctime)s] %(name)-12s %(levelname)-8s %(message)s',
        },
        'django.server': DEFAULT_LOGGING['formatters']['django.server'],
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
    },
    'handlers': {
        'coralogix': {
            'class': 'coralogix.handlers.CoralogixLogger',
            'formatter': 'default',
            'private_key': os.getenv('CORALOGIX_PRIVATE_KEY', ''),
            'app_name': os.getenv('CORALOGIX_APP_NAME', 'localhost'),
            'subsystem': os.getenv('CORALOGIX_SUBSYSTEM', 'logger'),
        },
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'default',
        },
        'django.server': DEFAULT_LOGGING['handlers']['django.server'],
    },
    'loggers': {
        '': {
            'level': 'WARNING',
            'handlers': LOGGING_HANDLERS,
        },
        # Our application code
        'breathecode': {
            'level': LOG_LEVEL,
            'handlers': LOGGING_HANDLERS,
            # Avoid double logging because of root logger
            'propagate': False,
        },
        # Prevent noisy modules from logging to Sentry
        'noisy_module': {
            'level': 'ERROR',
            'handlers': LOGGING_HANDLERS,
            'propagate': False,
        },
        # Default runserver request logging
        'django.server': DEFAULT_LOGGING['loggers']['django.server'],
    }
})

MESSAGE_TAGS = {
    messages.DEBUG: 'alert-info',
    messages.INFO: 'alert-info',
    messages.SUCCESS: 'alert-success',
    messages.WARNING: 'alert-warning',
    messages.ERROR: 'alert-danger',
}

# Internationalization
# https://docs.djangoproject.com/en/3.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True

# Honor the 'X-Forwarded-Proto' header for request.is_secure()
# SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Allow all host headers
ALLOWED_HOSTS = ['*']

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.0/howto/static-files/

# static generated automatically
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATIC_URL = '/static/'

STATICFILES_DIRS = [
    # static generated by us
    os.path.join(PROJECT_ROOT, 'static'),
]

CORS_ORIGIN_ALLOW_ALL = True

CORS_ALLOW_HEADERS = [
    'accept',
    'academy',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    'cache-control',
    'credentials',
    'http-access-control-request-method',
]

# production redis url
REDIS_URL = os.getenv('REDIS_COM_URL', '')
kwargs = {}
IS_REDIS_WITH_SSL_ON_HEROKU = False
IS_REDIS_WITH_SSL = False

# local or heroku redis url
if REDIS_URL == '' or REDIS_URL == 'redis://localhost:6379':
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379')

    # support for heroku redis addon
    if REDIS_URL.startswith('rediss://'):
        IS_REDIS_WITH_SSL_ON_HEROKU = True

else:
    IS_REDIS_WITH_SSL = True

CACHE_MIDDLEWARE_SECONDS = 60 * int(os.getenv('CACHE_MIDDLEWARE_MINUTES', 60 * 24))
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'TIMEOUT': CACHE_MIDDLEWARE_SECONDS,
    }
}

DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True
DJANGO_REDIS_IGNORE_EXCEPTIONS = True


class CustomRedisClient(DefaultClient):

    def delete_pattern(
        self,
        pattern: str,
        version: Optional[int] = None,
        prefix: Optional[str] = None,
        client: Optional[Redis] = None,
        itersize: Optional[int] = None,
    ) -> int:
        """
        Remove all keys matching pattern.
        """

        if isinstance(pattern, str):
            return super().delete_pattern(pattern,
                                          version=version,
                                          prefix=prefix,
                                          client=client,
                                          itersize=itersize)

        if client is None:
            client = self.get_client(write=True)

        patterns = [self.make_pattern(x, version=version, prefix=prefix) for x in pattern]

        try:
            count = 0
            pipeline = client.pipeline()

            for x in patterns:
                print('--------')
                print('--------')
                print('--------')
                print('--------')
                print('--------')
                print(type(x))
                print(x)

            for key in itertools.chain(*[client.scan_iter(match=x, count=itersize) for x in patterns]):
                pipeline.delete(key)
                count += 1
            pipeline.execute()

            return count
        except redis_client_exceptions as e:
            raise ConnectionInterrupted(connection=client) from e


if IS_REDIS_WITH_SSL_ON_HEROKU:
    CACHES['default']['OPTIONS'] = {
        'CLIENT_CLASS': 'breathecode.settings.CustomRedisClient',
        'SOCKET_CONNECT_TIMEOUT': 0.2,  # seconds
        'SOCKET_TIMEOUT': 0.2,  # seconds
        'PICKLE_VERSION': -1,
        # "IGNORE_EXCEPTIONS": True,
        'CONNECTION_POOL_KWARGS': {
            'ssl_cert_reqs': None,
            'max_connections': int(os.getenv('REDIS_MAX_CONNECTIONS', 500)),
            'retry_on_timeout': False,
        },
    }
elif IS_REDIS_WITH_SSL:
    redis_ca_cert_path, redis_user_cert_path, redis_user_private_key_path = configure_redis()
    CACHES['default']['OPTIONS'] = {
        'CLIENT_CLASS': 'breathecode.settings.CustomRedisClient',
        'SOCKET_CONNECT_TIMEOUT': 0.2,  # seconds
        'SOCKET_TIMEOUT': 0.2,  # seconds
        'PICKLE_VERSION': -1,
        # "IGNORE_EXCEPTIONS": True,
        'CONNECTION_POOL_KWARGS': {
            'ssl_cert_reqs': 'required',
            'ssl_ca_certs': redis_ca_cert_path,
            'ssl_certfile': redis_user_cert_path,
            'ssl_keyfile': redis_user_private_key_path,
            'max_connections': int(os.getenv('REDIS_MAX_CONNECTIONS', 500)),
            'retry_on_timeout': False,
        }
    }

if IS_TEST_ENV:
    from django.core.cache.backends.locmem import LocMemCache
    import fnmatch

    class CustomMemCache(LocMemCache):
        _keys = set()

        def delete_pattern(self, pattern):
            keys_to_delete = fnmatch.filter(self._keys, pattern)
            for key in keys_to_delete:
                self.delete(key)

            self._keys = {x for x in self._keys if x not in keys_to_delete}

        def delete(self, key, *args, **kwargs):
            self._keys.remove(key)
            return super().delete(key, *args, **kwargs)

        def keys(self, filter=None):
            if filter:
                return sorted(fnmatch.filter(self._keys, filter))

            return sorted(list(self._keys))

        def clear(self):
            self._keys = set()
            return super().clear()

        def set(self, key, *args, **kwargs):
            self._keys.add(key)
            return super().set(key, *args, **kwargs)

    CACHES['default'] = {
        **CACHES['default'],
        'LOCATION': 'breathecode',
        'BACKEND': 'breathecode.settings.CustomMemCache',
    }

# overwrite the redis url with the new one
os.environ['REDIS_URL'] = REDIS_URL

# TODO: decouple file storage from django
# if ENVIRONMENT != 'test':
#     DEFAULT_FILE_STORAGE = 'storages.backends.gcloud.GoogleCloudStorage'
#     STATICFILES_STORAGE = 'breathecode.utils.GCSManifestStaticFilesStorage'

#     GS_BUCKET_NAME = os.getenv('GS_BUCKET_NAME', '')
#     GS_FILE_OVERWRITE = False

#     class StaticFileCacheMiddleware:

#         def __init__(self, get_response):
#             self.get_response = get_response

#         def __call__(self, request):
#             response = self.get_response(request)

#             # Check if the response is for a static file
#             if request.path.startswith("/static/"):
#                 # Set the cache headers (1 year in this example)
#                 max_age = 365 * 24 * 60 * 60
#                 response["Cache-Control"] = f"public, max-age={max_age}"
#                 response["Expires"] = http_date(time() + max_age)

#             return response

#     MIDDLEWARE += [
#         'breathecode.settings.MemoryUsageMiddleware',
#     ]

# TODO: decouple file storage from django
# else:
#     # Simplified static file serving.
#     # https://warehouse.python.org/project/whitenoise/
#     STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Simplified static file serving.
# https://warehouse.python.org/project/whitenoise/
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

SITE_ID = 1

# Change 'default' database configuration with $DATABASE_URL.
# https://github.com/jacobian/dj-database-url#url-schema
DATABASES = {
    'default': dj_database_url.config(default=DATABASE_URL, conn_max_age=600, ssl_require=False),
}
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# SQL Explorer
EXPLORER_CONNECTIONS = {'Default': 'default'}
EXPLORER_DEFAULT_CONNECTION = 'default'

sql_keywords_path = Path(os.getcwd()) / 'breathecode' / 'sql_keywords.json'
with open(sql_keywords_path, 'r') as f:
    sql_keywords = json.load(f)

    # https://www.postgresql.org/docs/8.1/sql-keywords-appendix.html
    # scripts/update_sql_keywords_json.py
    # breathecode/sql_keywords.json

    EXPLORER_SQL_BLACKLIST = tuple(sql_keywords['blacklist'])

# Django Rest Hooks
HOOK_EVENTS = {
    # 'any.event.name': 'App.Model.Action' (created/updated/deleted)
    'form_entry.added': 'marketing.FormEntry.created+',
    'form_entry.changed': 'marketing.FormEntry.updated+',
    'profile_academy.added': 'authenticate.ProfileAcademy.created+',
    'profile_academy.changed': 'authenticate.ProfileAcademy.updated+',
    'cohort_user.added': 'admissions.CohortUser.created+',
    'cohort_user.changed': 'admissions.CohortUser.updated+',

    # and custom events, make sure to trigger them at notify.receivers.py
    'cohort_user.edu_status_updated': 'admissions.CohortUser.edu_status_updated',
    'user_invite.invite_status_updated': 'authenticate.UserInvite.invite_status_updated',
    'asset.asset_status_updated': 'registry.Asset.asset_status_updated',
    'event.event_status_updated': 'events.Event.event_status_updated',
    'event.new_event_order': 'events.EventCheckin.new_event_order',
    'event.new_event_attendee': 'events.EventCheckin.new_event_attendee',
    'form_entry.won_or_lost': 'marketing.FormEntry.won_or_lost',
    'session.mentorship_session_status': 'mentorship.MentorshipSession.mentorship_session_status',
}

# Websocket
ASGI_APPLICATION = 'breathecode.asgi.application'
REDIS_URL_PATTERN = r'^redis://(.+):(\d+)$'

heroku_redis_ssl_host = {
    'address': REDIS_URL,  # The 'rediss' schema denotes a SSL connection.
}

if IS_REDIS_WITH_SSL_ON_HEROKU:
    heroku_redis_ssl_host['address'] += '?ssl_cert_reqs=none'

# keep last part of the file
django_heroku.settings(locals(), databases=False)
