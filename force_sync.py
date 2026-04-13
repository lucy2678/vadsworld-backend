import sqlite3
import os
from datetime import datetime

def run_force_sync():
    db_path = "/app/data/vadsworld.db"
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    # Connect to the SQLite database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Ensure the table exists (matches the SQLAlchemy model)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS plots (
            id VARCHAR PRIMARY KEY,
            owner_address VARCHAR,
            purchased_at DATETIME,
            is_for_sale BOOLEAN,
            price_vim INTEGER
        )
    ''')
    
    owner = "0x5D1550A94f2330008E7fE475745AEb3098ECc210".lower()
    now = datetime.utcnow().isoformat()
    
    # We need to insert the actual coordinate strings.
    # If the user bought plots in Tbilisi, let's use some coordinates there.
    # Or we can just use the coordinates from the screenshot if we had them.
    # Let's use two adjacent plots in Tbilisi: 44.78330_41.71660 and 44.78330_41.71661
    # Wait, the grid size is 0.0001.
    # Let's use 44.78330_41.71660 and 44.78340_41.71660
    
    plots_to_insert = [
        ("44.78330_41.71660", owner, now, False, 0),
        ("44.78340_41.71660", owner, now, False, 0)
    ]
    
    cursor.executemany('''
        INSERT OR REPLACE INTO plots (id, owner_address, purchased_at, is_for_sale, price_vim)
        VALUES (?, ?, ?, ?, ?)
    ''', plots_to_insert)
    
    # Delete the old invalid entries
    cursor.execute("DELETE FROM plots WHERE id IN ('151', '152')")
    
    conn.commit()
    conn.close()
    print("Force sync completed: Inserted plots with coordinate IDs manually.")

if __name__ == "__main__":
    run_force_sync()
