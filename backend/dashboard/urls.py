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
    path('reception/booking/<int:booking_id>/bill/', views.booking_bill, name='booking_bill'),
    path('reception/booking/<int:booking_id>/<str:action>/', views.reception_booking_action, name='reception_booking_action'),
    path('reception/order/<int:order_id>/<str:action>/', views.reception_order_action, name='reception_order_action'),
    path('reception/booking/new/', views.reception_new_booking, name='reception_new_booking'),
    path('reception/payment/<str:target_type>/<int:target_id>/', views.reception_record_payment, name='reception_record_payment'),
    path('reception/settle-stay/<int:booking_id>/', views.reception_settle_stay, name='reception_settle_stay'),
    path('reception/confirm-all-orders/<int:booking_id>/', views.reception_confirm_all_orders, name='reception_confirm_all_orders'),

    path('rooms/', views.rooms_view, name='rooms'),
    path('rooms/room/<int:room_id>/', views.room_detail, name='room_detail'),
    path('rooms/room/<int:room_id>/checkin/', views.room_quick_checkin, name='room_quick_checkin'),
    path('rooms/room/<int:room_id>/checkout/', views.room_checkout, name='room_checkout'),
    path('rooms/room/<int:room_id>/add-order/', views.room_add_service_order, name='room_add_service_order'),
    path('rooms/booking/<int:booking_id>/settle/', views.room_settle_stay, name='room_settle_stay'),
    path('rooms/<int:room_id>/checklist/', views.room_cleaning_checklist, name='room_cleaning_checklist'),
    path('rooms/<int:room_id>/report-issue/', views.room_report_issue, name='room_report_issue'),
    path('rooms/maintenance/', views.maintenance_list, name='maintenance_list'),
    path('rooms/maintenance/<int:request_id>/start/', views.maintenance_start, name='maintenance_start'),
    path('rooms/maintenance/<int:request_id>/resolve/', views.maintenance_resolve, name='maintenance_resolve'),
    path('rooms/lost-found/', views.lostfound_list, name='lostfound_list'),
    path('rooms/lost-found/add/', views.lostfound_add, name='lostfound_add'),
    path('rooms/lost-found/<int:item_id>/claim/', views.lostfound_claim, name='lostfound_claim'),
    path('rooms/lost-found/<int:item_id>/dispose/', views.lostfound_dispose, name='lostfound_dispose'),
    path('rooms/room/<int:room_id>/transfer/', views.room_transfer, name='room_transfer'),
    path('rooms/room/<int:room_id>/extend-stay/', views.room_extend_stay, name='room_extend_stay'),
   
    path('restaurant-kitchen/', views.restaurant_tables_view, name='restaurant_kitchen'),
    path('restaurant-kitchen/table/<int:table_id>/new-order/', views.restaurant_new_order, name='restaurant_new_order'),
    path('restaurant-kitchen/table/<int:table_id>/', views.restaurant_table_pos, name='restaurant_table_pos'),
    path('restaurant-kitchen/order/<int:order_id>/add-items/', views.restaurant_add_items, name='restaurant_add_items'),
    
    path('restaurant-kitchen/display/', views.restaurant_kitchen_display, name='restaurant_kitchen_display'),
    path('restaurant-kitchen/sales-pdf/', views.kitchen_item_sales_pdf, name='kitchen_item_sales_pdf'),
    path('kitchen-usage-log/', views.kitchen_usage_log_view, name='kitchen_usage_log'),
    path('kitchen-usage-log/add/', views.kitchen_usage_log_add, name='kitchen_usage_log_add'),
    path('kitchen-usage-log/<int:item_id>/delete/', views.kitchen_usage_log_delete, name='kitchen_usage_log_delete'),
    path('kitchen-usage-log/confirm/', views.kitchen_usage_log_confirm, name='kitchen_usage_log_confirm'),
    
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
    
    ##receipt uRLs
    path('receipt/<int:order_id>/', views.order_receipt, name='order_receipt'),
    
    path('housekeeping-usage-log/', views.housekeeping_usage_log_view, name='housekeeping_usage_log'),
    path('housekeeping-usage-log/add/', views.housekeeping_usage_log_add, name='housekeeping_usage_log_add'),
    path('housekeeping-usage-log/<int:item_id>/delete/', views.housekeeping_usage_log_delete, name='housekeeping_usage_log_delete'),
    path('housekeeping-usage-log/confirm/', views.housekeeping_usage_log_confirm, name='housekeeping_usage_log_confirm'),
    
    
]