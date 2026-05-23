from __future__ import annotations

from django.core.management.base import BaseCommand

from cvat.apps.custom_operations.registry_loader import sync_custom_operations_from_registry


class Command(BaseCommand):
    help = "Sync file-based custom operation manifests into the database."

    def handle(self, *args, **options):
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
