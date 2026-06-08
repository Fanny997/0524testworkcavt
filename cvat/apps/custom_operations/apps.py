# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT
#独立的微服务，作为django应用主程序，用作模型推理与数据增强操作的注册与流程运行
from django.apps import AppConfig

#CustomOperationsConfig是Django识别这个微服务的配置类
class CustomOperationsConfig(AppConfig):
    name = "cvat.apps.custom_operations"
    #当Django完成应用加载时会自动调用ready方法，用于注册自定义操作
    def ready(self) -> None:
        from .registry_loader import setup_custom_operation_registry

        setup_custom_operation_registry(self)
