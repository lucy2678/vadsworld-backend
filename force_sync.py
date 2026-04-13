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
    
    # The real coordinates for Token IDs 1624772223 and 451886890
    plots_to_insert = [
        ("41.59869_41.62302", owner, now, False, 0),
        ("41.59882_41.62302", owner, now, False, 0)
    ]
    
    cursor.executemany('''
        INSERT OR REPLACE INTO plots (id, owner_address, purchased_at, is_for_sale, price_vim)
        VALUES (?, ?, ?, ?, ?)
    ''', plots_to_insert)
    
    # Delete the old invalid entries
    cursor.execute("DELETE FROM plots WHERE id IN ('151', '152', '44.78330_41.71660', '44.78340_41.71660')")
    
    conn.commit()
    conn.close()
    print("Force sync completed: Inserted plots with coordinate IDs manually.")

if __name__ == "__main__":
    run_force_sync()
