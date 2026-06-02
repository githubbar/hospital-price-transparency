from django.urls import path
from . import views

urlpatterns = [
    path('', views.search, name='search'),
    path('faq/', views.faq, name='faq'),
    path('explain/', views.explain_code, name='explain_code'),
    path('related/', views.related_procedures, name='related_procedures'),
    path('prices/', views.prices_details, name='prices_details'),
]
