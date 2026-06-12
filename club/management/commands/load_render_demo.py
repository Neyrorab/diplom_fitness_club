from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.apps import apps
from django.db import connection


class Command(BaseCommand):
    help = "Load demo data for the hosted Render instance and reset database sequences."

    def handle(self, *args, **options):
        call_command("loaddata", "fixtures/render_demo_data.json")
        sequence_sql = connection.ops.sequence_reset_sql(self.style, apps.get_models())
        if sequence_sql:
            with connection.cursor() as cursor:
                for sql in sequence_sql:
                    cursor.execute(sql)
        self.stdout.write(self.style.SUCCESS("Render demo data loaded."))
