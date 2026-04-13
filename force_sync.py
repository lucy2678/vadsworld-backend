import sqlite3
import os
from datetime import datetime

def run_force_sync():
    # ბაზის მისამართი Railway-ზე
    db_path = "/app/data/vadsworld.db"
    
    # ვქმნით დირექტორიას თუ არ არსებობს
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    # ვუკავშირდებით ბაზას
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # ვქმნით ცხრილს (თუ ჯერ არ არსებობს)
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
    
    # მონაცემები შენი 2 მიწისთვის
    plots_to_insert = [
        ("151", owner, now, False, 0),
        ("152", owner, now, False, 0)
    ]
    
    # ვწერთ ბაზაში (თუ უკვე არსებობს, გადააწერს - REPLACE)
    cursor.executemany('''
        INSERT OR REPLACE INTO plots (id, owner_address, purchased_at, is_for_sale, price_vim)
        VALUES (?, ?, ?, ?, ?)
    ''', plots_to_insert)
    
    conn.commit()
    conn.close()
    print("Success: Plots 151 and 152 manually injected into DB!")

if __name__ == "__main__":
    run_force_sync()