from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from eth_account.messages import encode_defunct
from web3.auto import w3
import os
import time
from datetime import datetime, timedelta

app = FastAPI(title="VadsWorld API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Database Setup
os.makedirs("/app/data", exist_ok=True)
SQLALCHEMY_DATABASE_URL = "sqlite:////app/data/vadsworld.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Ad(Base):
    __tablename__ = "ads"
    id = Column(Integer, primary_key=True, index=True)
    user_address = Column(String, index=True)
    icon = Column(String)
    text = Column(String)
    link = Column(String)
    lat = Column(String)
    lng = Column(String)
    status = Column(String, default="pending") # pending, approved, rejected
    expiry_date = Column(DateTime, nullable=True)

class Plot(Base):
    __tablename__ = "plots"
    id = Column(String, primary_key=True, index=True)
    owner_address = Column(String, index=True)
    purchased_at = Column(DateTime, default=datetime.utcnow)
    is_for_sale = Column(Boolean, default=False)
    price_vim = Column(Integer, default=0)
    is_vip = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)

# Migration: Add expiry_date column if it doesn't exist
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE ads ADD COLUMN expiry_date DATETIME"))
        conn.commit()
    except Exception:
        # Column likely already exists
        pass
    
    try:
        conn.execute(text("ALTER TABLE plots ADD COLUMN is_for_sale BOOLEAN DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

    try:
        conn.execute(text("ALTER TABLE plots ADD COLUMN price_vim INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

    try:
        conn.execute(text("ALTER TABLE plots ADD COLUMN is_vip BOOLEAN DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

from web3 import Web3

BSC_RPC_URL = "https://binance.llamarpc.com"
CONTRACT_ADDRESS = "0x509d779e25a0E93251DD775739aD0380430bc86c"

# Minimal ABI for the events we need
CONTRACT_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "from", "type": "address"},
            {"indexed": True, "internalType": "address", "name": "to", "type": "address"},
            {"indexed": True, "internalType": "uint256", "name": "tokenId", "type": "uint256"}
        ],
        "name": "Transfer",
        "type": "event"
    }
]

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/sync-plots")
def sync_plots(db: Session = Depends(get_db)):
    try:
        w3_instance = Web3(Web3.HTTPProvider(BSC_RPC_URL))
        # Ensure contract address is checksummed
        check_addr = Web3.to_checksum_address(CONTRACT_ADDRESS)
        contract = w3_instance.eth.contract(address=check_addr, abi=CONTRACT_ABI)
        
        # Start from just before the first mint block (92487530)
        START_BLOCK = 92487000 
        latest_block = w3_instance.eth.block_number
        
        # Limit sync range to avoid timeouts (search up to 200k blocks from START_BLOCK)
        MAX_BLOCKS = 200000
        sync_end = min(latest_block, START_BLOCK + MAX_BLOCKS)
        
        CHUNK_SIZE = 5000 # Smaller chunks are more likely to be accepted by public RPCs
        transfer_events = []
        
        current_block = START_BLOCK
        while current_block <= sync_end:
            end_block = min(current_block + CHUNK_SIZE - 1, sync_end)
            print(f"Syncing blocks {current_block} to {end_block}...")
            
            try:
                chunk_events = contract.events.Transfer.get_logs(from_block=current_block, to_block=end_block)
                transfer_events.extend(chunk_events)
            except Exception as e:
                print(f"Error getting logs for range {current_block}-{end_block}: {e}")
                # If a chunk fails, we could retry with smaller size or just skip/break
                # For now let's try to continue after a short sleep
                time.sleep(2)
            
            current_block = end_block + 1
            time.sleep(0.5)

        token_owners = {}
        for event in transfer_events:
            token_id = event['args']['tokenId']
            to_addr = event['args']['to']
            token_owners[token_id] = to_addr
            
        added_count = 0
        updated_count = 0
        
        for token_id, owner in token_owners.items():
            if not owner or owner == "0x0000000000000000000000000000000000000000":
                continue
                
            string_id = str(token_id)
            db_plot = db.query(Plot).filter(Plot.id == string_id).first()
            if not db_plot:
                # Set a default position or similar if it's an NFT without coords?
                # For NFT #1 it will just have ID "1"
                new_plot = Plot(id=string_id, owner_address=owner.lower())
                db.add(new_plot)
                added_count += 1
            elif db_plot.owner_address.lower() != owner.lower():
                db_plot.owner_address = owner.lower()
                updated_count += 1
                
        db.commit()
        return {"message": f"Sync complete. Found {len(transfer_events)} transfers. Added {added_count} plots, updated {updated_count} plots. Scanned up to {sync_end}."}
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Sync error: {error_details}")
        raise HTTPException(status_code=500, detail=f"Backend Sync Error: {str(e)}")

# Models
class AdCreate(BaseModel):
    user_address: str
    icon: str
    text: str
    link: str
    lat: str
    lng: str

class PlotClaim(BaseModel):
    id: str

class FiatPurchase(BaseModel):
    id: str
    owner_address: str

class PlotSell(BaseModel):
    id: str
    owner_address: str
    price_vim: int

OWNER_ADDRESS = os.getenv("OWNER_ADDRESS", "0x5D1550A94f2330008E7fE475745AEb3098ECc210").lower()

def verify_admin_signature(x_signature: str = Header(...), x_message: str = Header(...)):
    try:
        message = encode_defunct(text=x_message)
        recovered_address = w3.eth.account.recover_message(message, signature=x_signature)
        if recovered_address.lower() != OWNER_ADDRESS:
            raise HTTPException(status_code=403, detail="Not authorized")
        return recovered_address
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid signature")

@app.post("/ads")
def submit_ad(ad: AdCreate, db: Session = Depends(get_db)):
    db_ad = Ad(**ad.dict())
    db.add(db_ad)
    db.commit()
    db.refresh(db_ad)
    return {"message": "Ad submitted successfully, pending approval.", "ad": db_ad}

@app.get("/ads")
def get_approved_ads(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    return db.query(Ad).filter(
        Ad.status == "approved",
        (Ad.expiry_date == None) | (Ad.expiry_date > now)
    ).all()

@app.get("/plots")
def get_plots(db: Session = Depends(get_db)):
    return db.query(Plot).all()

@app.get("/users/{address}/plots")
def get_user_plots(address: str, db: Session = Depends(get_db)):
    return db.query(Plot).filter(Plot.owner_address.ilike(address)).all()

@app.get("/users/{address}/ads")
def get_user_ads(address: str, db: Session = Depends(get_db)):
    return db.query(Ad).filter(Ad.user_address.ilike(address)).all()

@app.post("/plots/sell")
def sell_plot(plot_sell: PlotSell, db: Session = Depends(get_db)):
    db_plot = db.query(Plot).filter(Plot.id == plot_sell.id, Plot.owner_address.ilike(plot_sell.owner_address)).first()
    if not db_plot:
        raise HTTPException(status_code=404, detail="Plot not found or you don't own it")
    
    db_plot.is_for_sale = True
    db_plot.price_vim = plot_sell.price_vim
    db.commit()
    return {"message": "Plot listed for sale successfully"}

@app.post("/plots/fiat-purchase")
def fiat_purchase(purchase: FiatPurchase, db: Session = Depends(get_db)):
    db_plot = db.query(Plot).filter(Plot.id == purchase.id).first()
    if db_plot:
        db_plot.owner_address = purchase.owner_address
        db_plot.is_for_sale = False
    else:
        db_plot = Plot(id=purchase.id, owner_address=purchase.owner_address, is_for_sale=False)
        db.add(db_plot)
    db.commit()
    return {"message": "Plot assigned successfully"}

@app.post("/admin/plots/claim")
def claim_plot(plot: PlotClaim, db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
    db_plot = db.query(Plot).filter(Plot.id == plot.id).first()
    if db_plot:
        db_plot.owner_address = admin
    else:
        db_plot = Plot(id=plot.id, owner_address=admin)
        db.add(db_plot)
    db.commit()
    return {"message": "Plot claimed by admin successfully"}

@app.get("/admin/ads")
def get_pending_ads(db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
    return db.query(Ad).filter(Ad.status == "pending").all()

@app.get("/admin/ads/all")
def get_all_ads(db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
    return db.query(Ad).all()

@app.post("/admin/ads/{ad_id}/approve")
def approve_ad(ad_id: int, db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    ad.status = "approved"
    ad.expiry_date = datetime.utcnow() + timedelta(days=30)
    db.commit()
    return {"message": "Ad approved", "expiry_date": ad.expiry_date}
    
@app.post("/admin/ads/{ad_id}/reject")
def reject_ad(ad_id: int, db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    ad.status = "rejected"
    db.commit()
    return {"message": "Ad rejected"}

@app.post("/admin/plots/clear")
def clear_all_plots(db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
    try:
        db.execute(text("DELETE FROM plots"))
        db.commit()
        return {"message": "All plots cleared from database successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/ads/plot/{lat}/{lng}")
def delete_ad_by_plot(lat: str, lng: str, db: Session = Depends(get_db)):
    # Delete ads for this plot when ownership changes
    ads = db.query(Ad).filter(Ad.lat == lat, Ad.lng == lng).all()
    for ad in ads:
        db.delete(ad)
    db.commit()
    return {"message": f"Deleted {len(ads)} ads for plot at {lat}, {lng}"}
