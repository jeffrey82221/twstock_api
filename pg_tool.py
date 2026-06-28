import psycopg  # Or import psycopg2 as psycopg

class PostgreSQLTool:
    """A custom database helper tool based on psycopg."""
    
    def __init__(self):
        """Initialize the tool with a Data Source Name (DSN) connection string."""
        self._dsn = "postgresql://postgres:postgres@localhost:5432/app_db"

    def get_conn(self):
        """Property to create or retrieve an active database connection."""
        # Check if connection doesn't exist, is closed, or has broken status
        return psycopg.connect(self._dsn)

    def execute_query(self, query: str, params: tuple = None):
        """Execute a query (INSERT, UPDATE, DELETE) and commit changes."""
        try:
            conn = self.get_conn()
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                conn.commit()
        except BaseException as e:
            print(f"Database operation failed: {e}")
            raise e
        finally:
            conn.close()

    def fetch_all(self, query: str, params: tuple = None) -> list:
        """Execute a query and fetch all resulting records."""
        try:
            conn = self.get_conn()
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchall()
        except BaseException as e:
            print(f"Database fetch operation failed: {e}")
            raise e
        finally:
            conn.close()

    def setup(self):
        """Set up the database with necessary extensions and schemas."""
        try:
            conn = self.get_conn()
            with conn.cursor() as cursor:
                with open('db/setting.sql', 'r') as f:
                    sql = f.read()
                    cursor.execute(sql)
            conn.commit()
        except BaseException as e:
            print(f"Database setup failed: {e}")
            raise e
        finally:
            conn.close()