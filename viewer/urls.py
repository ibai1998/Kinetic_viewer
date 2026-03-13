from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('api/load-metadata/', views.load_metadata, name='load_metadata'),
    path('api/filter-options/', views.filter_options, name='filter_options'),
    path('api/kinetic-data/', views.kinetic_data, name='kinetic_data'),
    path('api/save-flag/', views.save_flag, name='save_flag'),
    path('api/save-computed/', views.save_computed, name='save_computed'),
]
