from django.urls import path
from booklist import views, catalog_views

urlpatterns = [
    path('', catalog_views.home), path('health/', views.health),
    path('works/<int:work_id>/', catalog_views.work_page),
    path('api/v1/catalog/search/', catalog_views.search),
    path('api/v1/catalog/works/<int:work_id>/', catalog_views.detail),
    path('api/v1/csrf/', views.csrf_token),
    path('api/v1/identity/', views.identity), path('api/v1/identity/reset/', views.reset_identity),
    path('api/v1/admin/login/', views.admin_login), path('api/v1/admin/logout/', views.admin_logout),
    path('api/v1/admin/me/', views.admin_me), path('api/v1/admin/accounts/', views.admin_create),
    path('api/v1/admin/accounts/<int:admin_id>/disable/', views.admin_disable),
    path('api/v1/admin/password/', views.admin_password),
]
