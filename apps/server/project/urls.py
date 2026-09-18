"""
URL configuration for the project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path

from datasets.git_http import git_http_backend
from datasets.lfs_http import lfs_batch, lfs_verify
from project.api import api
from project.web import spa_entry

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', api.urls),
    path('git/<uuid:dataset_id>.git/info/lfs/objects/batch', lfs_batch, name='git-lfs-batch'),
    path('git/<uuid:dataset_id>.git/info/lfs/objects/<str:oid>/verify', lfs_verify, name='git-lfs-verify'),
    path('git/<uuid:dataset_id>.git', git_http_backend, name='git-dataset-root'),
    path('git/<uuid:dataset_id>.git/<path:git_path>', git_http_backend, name='git-dataset'),
    path('', spa_entry, name='web-app-root'),
    path('<path:route>', spa_entry, name='web-app-route'),
]
