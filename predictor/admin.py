from django.contrib import admin
from .models import FoodItem, FoodRecord, Prediction


admin.site.register(FoodItem)
admin.site.register(FoodRecord)
admin.site.register(Prediction)