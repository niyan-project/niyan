import json
import os
from pathlib import Path


SECRET_KEY = 'niyan-git-hook-process'
INSTALLED_APPS = [
    'accounts.apps.AccountsConfig',
    'namespaces.apps.NamespacesConfig',
    'datasets.apps.DatasetsConfig',
    'django.contrib.auth',
    'django.contrib.contenttypes',
]
AUTH_USER_MODEL = 'accounts.User'
DATABASES = {'default': json.loads(os.environ['NIYAN_HOOK_DATABASE_CONFIG'])}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'UTC'
REPOSITORIES_ROOT = Path(os.environ['NIYAN_HOOK_REPOSITORIES_ROOT'])
NIYAN_GIT_PUSH_CONTEXT_LIFETIME_SECONDS = int(os.environ['NIYAN_GIT_PUSH_CONTEXT_LIFETIME_SECONDS'])
NIYAN_GIT_PUSH_LEASE_LIFETIME_SECONDS = int(os.environ['NIYAN_GIT_PUSH_LEASE_LIFETIME_SECONDS'])
