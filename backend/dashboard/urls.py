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
    path('reception/settle-stay/<int:booking_id>/', views.reception_settle_stay, name='reception_settle_stay'),
    path('reception/confirm-all-orders/<int:booking_id>/', views.reception_confirm_all_orders, name='reception_confirm_all_orders'),

    path('rooms/', views.rooms_view, name='rooms'),
   
    path('restaurant-kitchen/', views.restaurant_tables_view, name='restaurant_kitchen'),
    path('restaurant-kitchen/table/<int:table_id>/new-order/', views.restaurant_new_order, name='restaurant_new_order'),
    path('restaurant-kitchen/table/<int:table_id>/', views.restaurant_table_pos, name='restaurant_table_pos'),
    path('restaurant-kitchen/order/<int:order_id>/add-items/', views.restaurant_add_items, name='restaurant_add_items'),
    path('restaurant-kitchen/display/', views.restaurant_kitchen_display, name='restaurant_kitchen_display'),
    path('restaurant-kitchen/item/<int:item_id>/advance/<str:new_status>/', views.restaurant_advance_item, name='restaurant_advance_item'),
    
    path('restaurant-kitchen/order/<int:order_id>/payment/', views.restaurant_record_payment, name='restaurant_record_payment'),
    path('restaurant-kitchen/new-offsite-order/', views.restaurant_new_offsite_order, name='restaurant_new_offsite_order'),
    path('restaurant-kitchen/create-offsite-order/', views.restaurant_create_offsite_order, name='restaurant_create_offsite_order'),
    path('restaurant-kitchen/export-pdf/', views.restaurant_export_pdf, name='restaurant_export_pdf'),
    
    path('restaurant-kitchen/item/<int:item_id>/cancel/', views.restaurant_cancel_item, name='restaurant_cancel_item'),
    path('restaurant-kitchen/order/<int:order_id>/cancel/', views.restaurant_cancel_order, name='restaurant_cancel_order'),
    
    path('restaurant-kitchen/table/<int:table_id>/status/<str:new_status>/', views.restaurant_set_table_status, name='restaurant_set_table_status'),
    path('restaurant-kitchen/order/<int:order_id>/split/', views.restaurant_split_order, name='restaurant_split_order'),
    path('restaurant-kitchen/waitlist/', views.restaurant_waitlist_view, name='restaurant_waitlist'),
    path('restaurant-kitchen/waitlist/<int:entry_id>/seat/', views.restaurant_waitlist_seat, name='restaurant_waitlist_seat'),
    path('restaurant-kitchen/waitlist/<int:entry_id>/cancel/', views.restaurant_waitlist_cancel, name='restaurant_waitlist_cancel'),
        
    
    path('bar/', views.bar_tabs_view, name='bar'),
    path('bar/tab/new/', views.bar_tabs_view, name='bar_new_tab'),
    path('bar/tab/<int:tab_id>/', views.bar_tab_pos, name='bar_tab_pos'),
    path('bar/tab/<int:tab_id>/add-items/', views.bar_add_items, name='bar_add_items'),
    path('bar/item/<int:item_id>/advance/<str:new_status>/', views.bar_advance_item, name='bar_advance_item'),
    path('bar/item/<int:item_id>/cancel/', views.bar_cancel_item, name='bar_cancel_item'),
    path('bar/order/<int:order_id>/cancel/', views.bar_cancel_order, name='bar_cancel_order'),
    path('bar/order/<int:order_id>/payment/', views.bar_record_payment, name='bar_record_payment'),
    path('bar/wastage/', views.bar_wastage_log, name='bar_wastage'),
    path('bar/export-pdf/', views.bar_export_pdf, name='bar_export_pdf'),
        
    path('gate/', views.gate_view, name='gate'),
    path('gate/exit/<int:log_id>/', views.gate_log_exit, name='gate_log_exit'),
    path('gate/edit/<int:log_id>/', views.gate_edit_entry, name='gate_edit_entry'),
    path('gate/export-pdf/', views.gate_export_pdf, name='gate_export_pdf'),
    
    path('hrm/', views.hrm_view, name='hrm'),
    path('store/', views.store_view, name='store'),
    
    # Store Management URLs
    path('store/disbursement/<int:disbursement_id>/', views.store_disbursement_detail, name='store_disbursement_detail'),
    path('store/disbursement/<int:disbursement_id>/approve/', views.store_disbursement_approve, name='store_disbursement_approve'),
    path('store/disbursement/<int:disbursement_id>/reject/', views.store_disbursement_reject, name='store_disbursement_reject'),
    path('store/disbursement/<int:disbursement_id>/disburse/', views.store_disbursement_disburse, name='store_disbursement_disburse'),
    path('store/disbursement/<int:disbursement_id>/reconcile/', views.store_disbursement_reconcile, name='store_disbursement_reconcile'),
]