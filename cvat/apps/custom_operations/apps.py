# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.apps import AppConfig


class CustomOperationsConfig(AppConfig):
    name = "cvat.apps.custom_operations"

    def ready(self) -> None:
        from .registry_loader import setup_custom_operation_registry

        setup_custom_operation_registry(self)
