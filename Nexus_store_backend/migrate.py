import sqlite3
import psycopg2
from app.database_postgres import get_db_connection

def run_migration():
    # 1. Connect to both databases
    sqlite_conn = sqlite3.connect("ecommerce.db")
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()
    
    pg_conn = get_db_connection()
    pg_cursor = pg_conn.cursor()

    tables = [
        {
            "name": "users",
            "columns": ["id", "email", "username", "hashed_password", "full_name", "phone", "avatar", "address", "city", "country", "postal_code", "role", "is_active", "is_verified", "created_at", "updated_at"]
        },
        {
            "name": "categories",
            "columns": ["id", "name", "description", "image_url", "is_active", "created_at"]
        },
        {
            "name": "products",
            "columns": ["id", "name", "description", "price", "discount_price", "stock_quantity", "category_id", "seller_id", "image_url", "images", "brand", "sku", "weight", "dimensions", "is_active", "is_featured", "rating", "review_count", "created_at", "updated_at"]
        },
        {
            "name": "carts",
            "columns": ["id", "user_id", "created_at", "updated_at"]
        },
        {
            "name": "cart_items",
            "columns": ["id", "cart_id", "product_id", "quantity", "added_at"]
        },
        {
            "name": "orders",
            "columns": ["id", "user_id", "order_number", "status", "total_amount", "shipping_address", "shipping_city", "shipping_country", "shipping_postal_code", "phone", "notes", "payment_method", "is_paid", "paid_at", "shipped_at", "delivered_at", "created_at", "updated_at"]
        },
        {
            "name": "order_items",
            "columns": ["id", "order_id", "product_id", "quantity", "price", "total"]
        },
        {
            "name": "refresh_tokens",
            "columns": ["id", "user_id", "token_hash", "expires_at", "is_revoked", "replaced_by_token", "ip_address", "device_info", "created_at"]
        },
        {
            "name": "reviews",
            "columns": ["id", "product_id", "user_id", "rating", "comment", "created_at"]
        }
    ]

    print("🚀 Starting Migration from SQLite to PostgreSQL...\n")

    for table in tables:
        table_name = table["name"]
        columns = table["columns"]
        
        # Read from SQLite
        sqlite_cursor.execute(f"SELECT {', '.join(columns)} FROM {table_name}")
        rows = sqlite_cursor.fetchall()
        
        if not rows:
            print(f"⏩ Table {table_name} is empty, skipping.")
            continue

        # Prepare PostgreSQL Insert Query
        placeholders = ", ".join(["%s"] * len(columns))
        insert_query = f"""
            INSERT INTO {table_name} ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO NOTHING
        """
        
        # Insert into PostgreSQL
        migrated_count = 0
        for row in rows:
            # Convert sqlite3.Row to tuple in the same order as columns
            data = tuple(row[col] for col in columns)
            pg_cursor.execute(insert_query, data)
            migrated_count += 1
        
        pg_conn.commit()
        print(f"✅ Migrated {migrated_count} {table_name}")

    # Close connections
    sqlite_conn.close()
    pg_conn.close()
    
    print("\n✨ Migration completed successfully!")

if __name__ == "__main__":
    run_migration()
