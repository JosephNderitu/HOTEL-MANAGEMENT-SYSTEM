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
    path('rooms/export-pdf/', views.rooms_export_pdf, name='rooms_export_pdf'),
    path('rooms/revenue-trend/', views.rooms_revenue_trend, name='rooms_revenue_trend'),
   
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
    
    path('bar/usage-log/', views.bar_usage_log_view, name='bar_usage_log'),
    path('bar/usage-log/add/', views.bar_usage_log_add, name='bar_usage_log_add'),
    path('bar/usage-log/<int:item_id>/delete/', views.bar_usage_log_delete, name='bar_usage_log_delete'),
    path('bar/usage-log/confirm/', views.bar_usage_log_confirm, name='bar_usage_log_confirm'),
    
    path('restaurant/revenue-trend/', views.restaurant_revenue_trend, name='restaurant_revenue_trend'),
    path('bar/revenue-trend/', views.bar_revenue_trend, name='bar_revenue_trend'),
        
    path('gate/', views.gate_view, name='gate'),
    path('gate/exit/<int:log_id>/', views.gate_log_exit, name='gate_log_exit'),
    path('gate/edit/<int:log_id>/', views.gate_edit_entry, name='gate_edit_entry'),
    path('gate/export-pdf/', views.gate_export_pdf, name='gate_export_pdf'),
    
    
    path('hrm/', views.hrm_home, name='hrm'),
    path('hrm/clock/', views.hrm_clock_view, name='hrm_clock'),
    path('hrm/clock/<str:action>/', views.hrm_clock_action, name='hrm_clock_action'),
    path('hrm/my-attendance/', views.hrm_my_attendance, name='hrm_my_attendance'),
    path('hrm/my-shifts/', views.hrm_my_shifts, name='hrm_my_shifts'),
    path('hrm/request-swap/', views.hrm_request_swap, name='hrm_request_swap'),
    path('hrm/weekly-off/', views.hrm_weekly_off, name='hrm_weekly_off'),
    path('hrm/my-leave/', views.hrm_my_leave, name='hrm_my_leave'),
    path('hrm/my-payslips/', views.hrm_my_payslips, name='hrm_my_payslips'),
    path('hrm/payslip/<int:record_id>/pdf/', views.hrm_payslip_pdf, name='hrm_payslip_pdf'),
    path('hrm/my-reviews/', views.hrm_my_reviews, name='hrm_my_reviews'),
    path('hrm/staff/', views.hrm_staff_list, name='hrm_staff_list'),
    path('hrm/staff/<int:user_id>/', views.hrm_staff_detail, name='hrm_staff_detail'),
    path('hrm/attendance-report/', views.hrm_attendance_report, name='hrm_attendance_report'),
    path('hrm/shift-calendar/', views.hrm_shift_calendar, name='hrm_shift_calendar'),
    path('hrm/shift-swap/<int:swap_id>/<str:action>/', views.hrm_shift_swap_review, name='hrm_shift_swap_review'),
    path('hrm/leave-requests/', views.hrm_leave_requests, name='hrm_leave_requests'),
    path('hrm/leave/<int:leave_id>/<str:action>/', views.hrm_leave_review, name='hrm_leave_review'),
    path('hrm/payroll/', views.hrm_payroll_list, name='hrm_payroll_list'),
    path('hrm/payroll/<int:user_id>/create/', views.hrm_payroll_create, name='hrm_payroll_create'),
    path('hrm/payroll/<int:record_id>/finalize/', views.hrm_payroll_finalize, name='hrm_payroll_finalize'),
    path('hrm/performance/', views.hrm_performance_list, name='hrm_performance_list'),
    path('hrm/performance/<int:user_id>/create/', views.hrm_performance_create, name='hrm_performance_create'),
    path('hrm/performance/<int:review_id>/finalize/', views.hrm_performance_finalize, name='hrm_performance_finalize'),
    path('hrm/weekly-off/lookup/<int:user_id>/', views.hrm_weekly_off_lookup, name='hrm_weekly_off_lookup'),
    path('hrm/shift/<int:assignment_id>/edit/', views.hrm_shift_assignment_edit, name='hrm_shift_assignment_edit'),
    path('hrm/shift/<int:assignment_id>/delete/', views.hrm_shift_assignment_delete, name='hrm_shift_assignment_delete'),
    path('hrm/swap/conflict-check/', views.hrm_swap_conflict_check, name='hrm_swap_conflict_check'),
    
    # Store Management URLs
    path('store/', views.store_view, name='store'),
    path('store/purchase/<int:purchase_id>/', views.store_purchase_detail, name='store_purchase_detail'),
    path('store/expenditure/', views.store_expenditure_report, name='store_expenditure_report'),
    path('store/expenditure/pdf/', views.store_expenditure_pdf, name='store_expenditure_pdf'),
    path('store/inventory/', views.store_inventory_view, name='store_inventory'),
    path('store/usage-logs/', views.store_usage_logs_view, name='store_usage_logs'),
    path('store/stock-items-json/', views.store_stock_items_json, name='store_stock_items_json'),
    path('store/stock-item/<int:stock_item_id>/suppliers/', views.store_supplier_comparison, name='store_supplier_comparison'),
    path('store/stocktakes/', views.stocktake_list, name='stocktake_list'),
    path('store/stocktakes/start/', views.stocktake_start, name='stocktake_start'),
    path('store/stocktakes/<int:stocktake_id>/', views.stocktake_detail, name='stocktake_detail'),
    path('store/stocktakes/<int:stocktake_id>/finalize/', views.stocktake_finalize, name='stocktake_finalize'),
    path('store/spend-trend/', views.store_spend_trend, name='store_spend_trend'),
    
    ##receipt uRLs
    path('receipt/<int:order_id>/', views.order_receipt, name='order_receipt'),
    
    path('housekeeping-usage-log/', views.housekeeping_usage_log_view, name='housekeeping_usage_log'),
    path('housekeeping-usage-log/add/', views.housekeeping_usage_log_add, name='housekeeping_usage_log_add'),
    path('housekeeping-usage-log/<int:item_id>/delete/', views.housekeeping_usage_log_delete, name='housekeeping_usage_log_delete'),
    path('housekeeping-usage-log/confirm/', views.housekeeping_usage_log_confirm, name='housekeeping_usage_log_confirm'),
    
    
]