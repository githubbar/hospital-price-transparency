import csv
import os
import pymysql
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
CSV_FILE = '351720796_indiana-university-health-bloomington-inc._standardcharges.csv'
TABLE_NAME = 'hospital_prices'

# Database connection details
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'root')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME', 'hospital_db')
DB_PORT = int(os.getenv('DB_PORT', 3306))

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        cursorclass=pymysql.cursors.DictCursor
    )

def clean_column_name(col_name):
    # Replace | with _ and invalid chars
    return col_name.strip().replace('|', '_').replace(' ', '_').lower()

def run_import():
    print(f"Connecting to database '{DB_NAME}'...")
    
    try:
        conn = get_db_connection()
    except pymysql.err.OperationalError as e:
        print(f"Error connecting to database: {e}")
        return

    try:
        with conn.cursor() as cursor:
            print(f"Reading CSV file: {CSV_FILE}...")
            
            with open(CSV_FILE, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                
                # Skip the first 2 metadata lines
                next(reader) # Line 1: Metadata headers
                next(reader) # Line 2: Metadata values
                
                # Line 3: Actual Column Headers
                headers = next(reader)
                
                # Sanitize headers for SQL
                cleaned_headers = [clean_column_name(h) for h in headers]
                print(f"Found {len(cleaned_headers)} columns.")
                
                # Identify numeric columns for cleaning later
                is_numeric_col = []
                for col in cleaned_headers:
                     if any(x in col for x in ['charge', 'amount', 'fee', 'price', 'negotiated_dollar', 'min', 'max']):
                         is_numeric_col.append(True)
                     else:
                         is_numeric_col.append(False)

                # Clear table (Truncate)
                print(f"Clearing table '{TABLE_NAME}'...")
                # We use DELETE because TRUNCATE might restart ID buffering or have permission issues, but TRUNCATE is faster. 
                # Let's use TRUNCATE.
                cursor.execute(f"TRUNCATE TABLE {TABLE_NAME}")
                
                # Prepare INSERT statement
                # We need to skip the ID column in the INSERT part
                placeholders = ', '.join(['%s'] * len(cleaned_headers))
                # Explicitly list columns for insert to match the values, excluding the new 'id' column
                cols_list = ', '.join([f"`{c}`" for c in cleaned_headers])
                insert_sql = f"INSERT INTO {TABLE_NAME} ({cols_list}) values ({placeholders})"
                
                print("Importing data...")
                inserted_count = 0
                batch = []
                BATCH_SIZE = 1000

                for row in reader:
                    # Clean data: Replace empty strings with None (NULL in SQL)
                    cleaned_row = []
                    for i, val in enumerate(row):
                        val = val.strip()
                        if val == '':
                            cleaned_row.append(None)
                        else:
                            # If this is a numeric column, try to remove currencies/commas
                            if is_numeric_col[i]:
                                try:
                                    # remove $ and ,
                                    clean_val = val.replace('$', '').replace(',', '')
                                    cleaned_row.append(float(clean_val))
                                except ValueError:
                                    # Fallback: keep as 0 or None if parsing fails? Let's use None
                                    cleaned_row.append(None)
                            else:
                                cleaned_row.append(val)
                    
                    batch.append(cleaned_row)
                    
                    if len(batch) >= BATCH_SIZE:
                        cursor.executemany(insert_sql, batch)
                        conn.commit()
                        inserted_count += len(batch)
                        batch = []
                
                # Insert remaining
                if batch:
                    cursor.executemany(insert_sql, batch)
                    conn.commit()
                    inserted_count += len(batch)

                print(f"Successfully imported {inserted_count} rows into '{TABLE_NAME}'!")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_import()
