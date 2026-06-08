from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.dispatch import receiver

class PricesConfig(AppConfig):
    name = 'prices'

    def ready(self):
        @receiver(connection_created)
        def configure_sqlite(sender, connection, **kwargs):
            if connection.vendor == 'sqlite':
                cursor = connection.cursor()
                # 100MB in-memory SQLite page cache
                cursor.execute('PRAGMA cache_size = -100000;')
                # Keep temporary tables in memory
                cursor.execute('PRAGMA temp_store = MEMORY;')
                # Use memory-mapped I/O (up to 256MB) to let OS cache FUSE blocks directly
                cursor.execute('PRAGMA mmap_size = 268435456;')
