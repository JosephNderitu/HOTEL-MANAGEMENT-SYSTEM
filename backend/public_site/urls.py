from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
    path('api/check-availability/', views.check_availability, name='check_availability'),
    path('api/submit-booking/', views.submit_booking, name='submit_booking'),
    path('api/submit-order/', views.submit_order, name='submit_order'),
    
    #conversation and chat message endpoints
    path('api/contact/send-code/', views.send_verification_code, name='send_verification_code'),
    path('api/contact/verify-code/', views.verify_code, name='verify_code'),
    path('api/contact/messages/', views.get_messages, name='get_messages'),
    path('api/contact/send-message/', views.send_message, name='send_message'),
    path('api/contact/logout/', views.contact_logout, name='contact_logout'),
]