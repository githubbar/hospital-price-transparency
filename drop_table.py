import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME', 'hospital_db')
DB_PORT = int(os.getenv('DB_PORT', 3306))

try:
    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, port=DB_PORT)
    with conn.cursor() as cursor:
        print("Dropping table hospital_prices...")
        cursor.execute("DROP TABLE IF EXISTS hospital_prices")
    conn.close()
    print("Done.")
except Exception as e:
    print(e)
