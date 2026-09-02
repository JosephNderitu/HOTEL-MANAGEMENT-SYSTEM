from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
    path('api/check-availability/', views.check_availability, name='check_availability'),
    path('api/submit-booking/', views.submit_booking, name='submit_booking'),
    path('api/submit-order/', views.submit_order, name='submit_order'),
]