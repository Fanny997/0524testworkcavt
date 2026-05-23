# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CustomOperationViewSet

router = DefaultRouter(trailing_slash=False)
router.register("definitions", CustomOperationViewSet, basename="custom_operation")

urlpatterns = [path("api/custom-operations/", include(router.urls))]

