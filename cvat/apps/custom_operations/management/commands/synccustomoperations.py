from __future__ import annotations

from django.core.management.base import BaseCommand

from cvat.apps.custom_operations.registry_loader import sync_custom_operations_from_registry


class Command(BaseCommand):
    """同步自定义操作注册目录到数据库的 Django 管理命令。

    Django 对 management/commands 目录有固定约定：
    文件名即命令名。因此本文件对应的命令为：

        python manage.py synccustomoperations

    本命令不直接执行模型推理，也不部署 Nuclio 函数。它只负责把
    custom_operations_registry 目录中的 manifest.json 转换成数据库中的
    CustomOperationDefinition 记录。Workflows 页面读取的操作列表正是这些
    数据库记录。
    """

    help = "Sync file-based custom operation manifests into the database."

    def handle(self, *args, **options):
        """命令执行入口。

        BaseCommand 会在解析命令行参数后调用 handle()。本命令没有自定义参数，
        因此 args 和 options 未被使用。

        sync_custom_operations_from_registry() 完成实际同步工作：
        1. 扫描注册目录中的 manifest.json。
        2. 校验并整理 manifest 字段。
        3. 创建或更新 CustomOperationDefinition。
        4. 返回本次同步成功的操作对象列表。
        """

        operations = sync_custom_operations_from_registry()
        if operations:
            self.stdout.write(
                self.style.SUCCESS(
                    "Synced custom operations: "
                    + ", ".join(operation.nuclio_function for operation in operations)
                )
            )
        else:
            self.stdout.write("No custom operation manifests found.")
