from django.urls import path
from booklist import views

urlpatterns = [
    path('', views.home), path('health/', views.health),
    path('api/v1/csrf/', views.csrf_token),
    path('api/v1/identity/', views.identity), path('api/v1/identity/reset/', views.reset_identity),
    path('api/v1/admin/login/', views.admin_login), path('api/v1/admin/logout/', views.admin_logout),
    path('api/v1/admin/me/', views.admin_me), path('api/v1/admin/accounts/', views.admin_create),
    path('api/v1/admin/accounts/<int:admin_id>/disable/', views.admin_disable),
    path('api/v1/admin/password/', views.admin_password),
]
