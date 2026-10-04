from django.urls import include, path
from forum.admin import admin_site

urlpatterns = [path('admin/', admin_site.urls), path('', include('forum.urls'))]
