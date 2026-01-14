import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

db_name = os.getenv('DB_NAME')
host = os.getenv('DB_HOST', 'localhost')
user = os.getenv('DB_USER')
password = os.getenv('DB_PASSWORD')
port = int(os.getenv('DB_PORT', 3306))

print(f"Connecting to MySQL at {host} as {user}...")

try:
    # Connect without selecting a database
    connection = pymysql.connect(
        host=host,
        user=user,
        password=password,
        port=port
    )
    
    with connection.cursor() as cursor:
        print(f"Creating database '{db_name}' if it doesn't exist...")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        print("Database created successfully!")
        
    connection.close()
except Exception as e:
    print(f"Error: {e}")
