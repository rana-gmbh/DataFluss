"""Django's SQLite backend on SQLCipher, for the desktop client.

The 32-byte key comes from the client shell (macOS Keychain, Windows Credential Manager) through
the environment and is never written to disk. Everything else is Django's own SQLite backend.
"""
import datetime
import decimal
import threading
from collections.abc import Mapping
from itertools import tee

import sqlcipher3.dbapi2 as Database
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.sqlite3 import base
from django.utils.dateparse import parse_date, parse_datetime, parse_time

Database.register_converter("bool", b"1".__eq__)
Database.register_converter("date", base.decoder(parse_date))
Database.register_converter("time", base.decoder(parse_time))
Database.register_converter("datetime", base.decoder(parse_datetime))
Database.register_converter("timestamp", base.decoder(parse_datetime))
Database.register_adapter(decimal.Decimal, str)
Database.register_adapter(datetime.date, base.adapt_date)
Database.register_adapter(datetime.datetime, base.adapt_datetime)


class CursorWrapper(Database.Cursor):
    """Django's SQLiteCursorWrapper, which must subclass this driver's Cursor."""

    convert_query = base.SQLiteCursorWrapper.convert_query

    def execute(self, query, params=None):
        if params is None:
            return super().execute(query)
        param_names = list(params) if isinstance(params, Mapping) else None
        return super().execute(self.convert_query(query, param_names=param_names), params)

    def executemany(self, query, param_list):
        peekable, param_list = tee(iter(param_list))
        params = next(peekable, None)
        param_names = list(params) if params and isinstance(params, Mapping) else None
        return super().executemany(self.convert_query(query, param_names=param_names), param_list)


# One connection opens or closes at a time. A close in one thread while another opened the same file
# deadlocked in SQLite's file locks, because pysqlite holds the GIL while it closes.
_LIFECYCLE = threading.Lock()


class DatabaseWrapper(base.DatabaseWrapper):
    Database = Database

    def get_new_connection(self, conn_params):
        with _LIFECYCLE:
            return self._open(conn_params)

    def _close(self):
        with _LIFECYCLE:
            super()._close()

    def _open(self, conn_params):
        key = self.settings_dict.get("KEY", "")
        if len(key) != 64 or not all(c in "0123456789abcdef" for c in key):
            raise ImproperlyConfigured("The SQLCipher key must be 32 bytes as 64 lowercase hex characters.")
        conn = Database.connect(**conn_params)
        # A raw key skips the password derivation, so opening stays fast. It must be the first statement.
        conn.execute(f"PRAGMA key = \"x'{key}'\"")
        conn.execute("SELECT count(*) FROM sqlite_master")  # Fails here, not later, if the key is wrong.
        base.register_functions(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA legacy_alter_table = OFF")
        for init_command in self.init_commands:
            if init_command := init_command.strip():
                conn.execute(init_command)
        return conn

    def create_cursor(self, name=None):
        return self.connection.cursor(factory=CursorWrapper)
