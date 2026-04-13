from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from eth_account.messages import encode_defunct
from web3.auto import w3
import os
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

from web3 import Web3

BSC_RPC_URL = "https://bsc-dataseed.binance.org/"
CONTRACT_ADDRESS = "0xeb85d16502bd603749fA8774d0d4717e324e0850"

# Minimal ABI for the events we need
CONTRACT_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "tokenId", "type": "uint256"},
            {"indexed": False, "internalType": "int256", "name": "x", "type": "int256"},
            {"indexed": False, "internalType": "int256", "name": "y", "type": "int256"},
            {"indexed": False, "internalType": "string", "name": "country", "type": "string"}
        ],
        "name": "LandMinted",
        "type": "event"
    },
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
        contract = w3_instance.eth.contract(address=Web3.to_checksum_address(CONTRACT_ADDRESS), abi=CONTRACT_ABI)
        
        START_BLOCK = 90744785
        CHUNK_SIZE = 40000
        latest_block = w3_instance.eth.block_number
        
        mint_events = []
        transfer_events = []
        
        current_block = START_BLOCK
        while current_block <= latest_block:
            end_block = min(current_block + CHUNK_SIZE - 1, latest_block)
            
            chunk_mint_events = contract.events.LandMinted.get_logs(from_block=current_block, to_block=end_block)
            chunk_transfer_events = contract.events.Transfer.get_logs(from_block=current_block, to_block=end_block)
            
            mint_events.extend(chunk_mint_events)
            transfer_events.extend(chunk_transfer_events)
            
            current_block = end_block + 1

        token_coords = {}
        for event in mint_events:
            token_id = event['args']['tokenId']
            x = event['args']['x']
            y = event['args']['y']
            lng = x / 100000.0
            lat = y / 100000.0
            string_id = f"{lng:.5f}_{lat:.5f}"
            token_coords[token_id] = string_id
            
        token_owners = {}
        for event in transfer_events:
            token_id = event['args']['tokenId']
            to_addr = event['args']['to']
            token_owners[token_id] = to_addr
            
        added_count = 0
        updated_count = 0
        
        for token_id, string_id in token_coords.items():
            owner = token_owners.get(token_id)
            if not owner:
                continue
                
            db_plot = db.query(Plot).filter(Plot.id == string_id).first()
            if not db_plot:
                new_plot = Plot(id=string_id, owner_address=owner.lower())
                db.add(new_plot)
                added_count += 1
            elif db_plot.owner_address.lower() != owner.lower():
                db_plot.owner_address = owner.lower()
                updated_count += 1
                
        db.commit()
        return {"message": f"Sync complete. Added {added_count} plots, updated {updated_count} plots."}
    except Exception as e:
        print(f"Sync error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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

@app.delete("/ads/plot/{lat}/{lng}")
def delete_ad_by_plot(lat: str, lng: str, db: Session = Depends(get_db)):
    # Delete ads for this plot when ownership changes
    ads = db.query(Ad).filter(Ad.lat == lat, Ad.lng == lng).all()
    for ad in ads:
        db.delete(ad)
    db.commit()
    return {"message": f"Deleted {len(ads)} ads for plot at {lat}, {lng}"}
