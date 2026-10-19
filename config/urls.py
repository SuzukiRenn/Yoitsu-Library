from django.urls import path
from booklist import views, catalog_views, public_views

urlpatterns = [
    path('', public_views.home), path('catalog/', catalog_views.home), path('health/', views.health),
    path('api/v1/public/works/', public_views.listing),
    path('api/v1/public/recommendations/', public_views.submit),
    path('api/v1/public/works/<int:work_id>/recommend/', public_views.recommend),
    path('api/v1/public/works/<int:work_id>/like/', public_views.like),
    path('api/v1/public/works/<int:work_id>/reason/', public_views.reason),
    path('api/v1/public/works/<int:work_id>/reasons/', public_views.reasons),
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
