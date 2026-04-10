import sqlite3
import psycopg2
import os
from app.database_postgres import get_db_connection

DATABASE_NAME = "ecommerce.db"

def get_sqlite_connection():
    """Create connection to source SQLite database"""
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def convert_sqlite_to_pg_type(sqlite_type, col_name, is_pk):
    """Convert SQLite types to PostgreSQL types dynamically"""
    sqlite_type = sqlite_type.upper()
    col_name = col_name.lower()
    
    if is_pk and "INT" in sqlite_type:
        return "SERIAL PRIMARY KEY"
    
    if col_name.startswith("is_") or col_name.startswith("has_") or col_name in ["is_active", "is_verified", "is_paid", "is_revoked", "is_featured"]:
        return "BOOLEAN"
    
    if "INT" in sqlite_type:
        return "INTEGER"
    if "REAL" in sqlite_type or "FLOAT" in sqlite_type:
        return "FLOAT"
    if "TEXT" in sqlite_type or "CHAR" in sqlite_type:
        if any(ts in col_name for ts in ["created_at", "updated_at", "expires_at", "added_at", "paid_at", "shipped_at", "delivered_at"]):
            return "TIMESTAMP"
        return "TEXT"
    
    return "TEXT"

def check_ref_exists(cursor, table, record_id):
    """Check if a referenced record exists in PostgreSQL"""
    if record_id is None:
        return False
    cursor.execute(f"SELECT 1 FROM {table} WHERE id = %s", (record_id,))
    return cursor.fetchone() is not None

def init_full_database():
    """Create all tables in PostgreSQL based on SQLite schema dynamically"""
    print("📡 Dynamically Initializing PostgreSQL Schema...")
    sqlite_conn = get_sqlite_connection()
    sqlite_cursor = sqlite_conn.cursor()
    
    pg_conn = get_db_connection()
    pg_cursor = pg_conn.cursor()
    
    tables = [
        "users", "categories", "products", "carts", "cart_items", 
        "orders", "order_items", "refresh_tokens", "reviews"
    ]
    
    try:
        for table_name in tables:
            sqlite_cursor.execute(f"PRAGMA table_info({table_name})")
            columns_info = sqlite_cursor.fetchall()
            
            if not columns_info:
                print(f"⚠️ Warning: Table {table_name} not found in SQLite.")
                continue
                
            pg_columns = []
            for col in columns_info:
                name = col[1]
                sqlite_type = col[2]
                not_null = "NOT NULL" if col[3] else ""
                pk = col[5]
                
                pg_type = convert_sqlite_to_pg_type(sqlite_type, name, pk)
                
                if "SERIAL PRIMARY KEY" in pg_type:
                    pg_columns.append(f"{name} {pg_type}")
                else:
                    pg_columns.append(f"{name} {pg_type} {not_null}")
            
            create_query = f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(pg_columns)})"
            pg_cursor.execute(create_query)
            print(f"✅ Schema verified for: {table_name}")
            
        # Preserving Indexes
        pg_cursor.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_token_hash ON refresh_tokens(token_hash)")
        pg_cursor.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id)")
        pg_cursor.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires_at ON refresh_tokens(expires_at)")
        pg_cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_product_id ON reviews(product_id)")
        pg_cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_user_id ON reviews(user_id)")

        pg_conn.commit()
        print("🎉 Full PostgreSQL schema created successfully!")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"❌ Error during schema creation: {e}")
        raise e
    finally:
        sqlite_conn.close()
        pg_cursor.close()
        pg_conn.close()

def run_migration():
    """Migrate data dynamically from SQLite to PostgreSQL with row-level validation"""
    print("🚀 Starting Robust Migration...")
    
    sqlite_conn = get_sqlite_connection()
    sqlite_cursor = sqlite_conn.cursor()
    
    pg_conn = get_db_connection()
    pg_cursor = pg_conn.cursor()
    
    tables = [
        "users", "categories", "products", "carts", "cart_items", 
        "orders", "order_items", "refresh_tokens", "reviews"
    ]
    
    for table_name in tables:
        try:
            sqlite_cursor.execute(f"PRAGMA table_info({table_name})")
            columns_info = sqlite_cursor.fetchall()
            if not columns_info:
                continue
                
            cols = [col[1] for col in columns_info]
            bool_cols = [
                col[1] for col in columns_info 
                if convert_sqlite_to_pg_type(col[2], col[1], col[5]) == "BOOLEAN"
            ]
            
            sqlite_cursor.execute(f"SELECT {', '.join(cols)} FROM {table_name}")
            rows = sqlite_cursor.fetchall()
            
            if not rows:
                print(f"⏩ Table {table_name} is empty, skipping.")
                continue
                
            placeholders = ", ".join(["%s"] * len(cols))
            insert_query = f"""
                INSERT INTO {table_name} ({', '.join(cols)})
                VALUES ({placeholders})
                ON CONFLICT (id) DO NOTHING
            """
            
            migrated_count = 0
            skipped_count = 0
            
            for row in rows:
                data_dict = dict(row)
                
                # 1. Validation Logic for Foreign Keys
                is_valid = True
                
                if table_name == "products":
                    if data_dict.get("category_id") and not check_ref_exists(pg_cursor, "categories", data_dict["category_id"]):
                        is_valid = False
                    if data_dict.get("seller_id") and not check_ref_exists(pg_cursor, "users", data_dict["seller_id"]):
                        is_valid = False
                        
                elif table_name == "carts":
                    if data_dict.get("user_id") and not check_ref_exists(pg_cursor, "users", data_dict["user_id"]):
                        is_valid = False
                        
                elif table_name == "cart_items":
                    if not check_ref_exists(pg_cursor, "carts", data_dict.get("cart_id")) or \
                       not check_ref_exists(pg_cursor, "products", data_dict.get("product_id")):
                        is_valid = False
                        
                elif table_name == "orders":
                    if data_dict.get("user_id") and not check_ref_exists(pg_cursor, "users", data_dict["user_id"]):
                        is_valid = False
                        
                elif table_name == "order_items":
                    if not check_ref_exists(pg_cursor, "orders", data_dict.get("order_id")) or \
                       not check_ref_exists(pg_cursor, "products", data_dict.get("product_id")):
                        is_valid = False
                        
                elif table_name == "refresh_tokens":
                    if data_dict.get("user_id") and not check_ref_exists(pg_cursor, "users", data_dict["user_id"]):
                        is_valid = False
                        
                elif table_name == "reviews":
                    if not check_ref_exists(pg_cursor, "products", data_dict.get("product_id")) or \
                       not check_ref_exists(pg_cursor, "users", data_dict.get("user_id")):
                        is_valid = False

                if not is_valid:
                    skipped_count += 1
                    continue

                # 2. Boolean Fix
                for b_col in bool_cols:
                    if b_col in data_dict:
                        data_dict[b_col] = bool(data_dict[b_col])
                
                # 3. Safe Row Insert
                try:
                    values = tuple(data_dict[col] for col in cols)
                    pg_cursor.execute(insert_query, values)
                    migrated_count += 1
                except Exception:
                    pg_conn.rollback() # Important: Rollback the failed record
                    skipped_count += 1
                    continue
            
            pg_conn.commit()
            print(f"✅ Migrated {migrated_count} rows from {table_name}")
            if skipped_count > 0:
                print(f"⚠️ Skipped {skipped_count} invalid rows from {table_name}")
            
        except Exception as e:
            print(f"❌ Critical error in table {table_name}: {e}")
            pg_conn.rollback()
            
    sqlite_conn.close()
    pg_cursor.close()
    pg_conn.close()
    print("\n✨ Robust Migration Finished!")

if __name__ == "__main__":
    init_full_database()
    run_migration()