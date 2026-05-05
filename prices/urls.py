from django.urls import path
from . import views

urlpatterns = [
    path('', views.search, name='search'),
    path('explain/', views.explain_code, name='explain_code'),
]
