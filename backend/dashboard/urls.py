from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(
        template_name='registration/staff_login.html',
        redirect_authenticated_user=True,
    ), name='staff_login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='home'), name='staff_logout'),
    path('dashboard/', views.dashboard_home, name='home'),
    path('reception/', views.reception_view, name='reception'),
    
    path('reception/booking/<int:booking_id>/<str:action>/', views.reception_booking_action, name='reception_booking_action'),
    path('reception/order/<int:order_id>/<str:action>/', views.reception_order_action, name='reception_order_action'),
    path('reception/booking/new/', views.reception_new_booking, name='reception_new_booking'),
    path('reception/payment/<str:target_type>/<int:target_id>/', views.reception_record_payment, name='reception_record_payment'),

    path('rooms/', views.rooms_view, name='rooms'),
    path('restaurant-kitchen/', views.restaurant_kitchen_view, name='restaurant_kitchen'),
    path('bar/', views.bar_view, name='bar'),
    
    path('gate/', views.gate_view, name='gate'),
    path('gate/exit/<int:log_id>/', views.gate_log_exit, name='gate_log_exit'),
    path('gate/edit/<int:log_id>/', views.gate_edit_entry, name='gate_edit_entry'),
    path('gate/export-pdf/', views.gate_export_pdf, name='gate_export_pdf'),
    
    path('hrm/', views.hrm_view, name='hrm'),
    path('store/', views.store_view, name='store'),
]