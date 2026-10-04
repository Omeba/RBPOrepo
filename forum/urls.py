from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('health/', views.health, name='health'),
    path('register/', views.register, name='register'),
    path('login/', views.sign_in, name='login'),
    path('logout/', views.sign_out, name='logout'),
    path('account/credentials/', views.credentials, name='credentials'),
    path('topics/', views.search, name='search'),
    path('topics/new/', views.topic_create, name='topic_create'),
    path('topics/<int:pk>/', views.topic_detail, name='topic'),
    path('topics/<int:pk>/messages/', views.message_create, name='message_create'),
    path('topics/<int:pk>/delete/', views.delete, {'kind': 'topic'}, name='topic_delete'),
    path('messages/<int:pk>/delete/', views.delete, {'kind': 'message'}, name='message_delete'),
    path('tags/', views.tags, name='tags'),
    path('tags/<int:pk>/delete/', views.delete, {'kind': 'tag'}, name='tag_delete'),
]
