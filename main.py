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

@app.get("/")
def health_check():
    return {"status": "ok", "message": "VadsWorld API is running"}

# Configure CORS - Ensure this is the first middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://vadsworld.com", "https://www.vadsworld.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
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

# BSC Configuration
BSC_RPC_URLS = [
    "https://bsc-dataseed.binance.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
    "https://binance.llamarpc.com"
]

CONTRACT_ADDRESS = "0x509d779e25a0E93251DD775739aD0380430bc86c"
DEPLOYMENT_BLOCK = 40000000 # Example start block for BSC deployment
SYNC_RANGE_PER_REQUEST = 5000 # Limit scan range to prevent timeouts

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

def get_w3():
    for url in BSC_RPC_URLS:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout': 10}))
            if w3.is_connected():
                return w3, url
        except Exception as e:
            print(f"Failed to connect to RPC {url}: {e}")
            continue
    return None, None

@app.post("/sync-plots")
def sync_plots(db: Session = Depends(get_db)):
    try:
        w3_instance, active_rpc = get_w3()
        if not w3_instance:
            raise HTTPException(status_code=500, detail="Could not connect to any BSC RPC nodes")
            
        print(f"Connected to RPC: {active_rpc}")
        
        # Ensure contract address is checksummed
        check_addr = Web3.to_checksum_address(CONTRACT_ADDRESS)
        contract = w3_instance.eth.contract(address=check_addr, abi=CONTRACT_ABI)
        
        latest_block = w3_instance.eth.block_number
        
        # Limit the scan range to prevent timeouts
        # Scan the most recent 5,000 blocks by default
        start_block = max(DEPLOYMENT_BLOCK, latest_block - SYNC_RANGE_PER_REQUEST)
        
        print(f"Latest block: {latest_block}, Scanning range: {start_block} to {latest_block}")
        
        transfer_events = []
        try:
            # Fetch events in one go for the 5,000 block range
            transfer_events = contract.events.Transfer.get_logs(from_block=start_block, to_block=latest_block)
        except Exception as e:
            print(f"Log fetch error: {e}")
            # Potentially retry with even smaller chunks if needed
            mid = start_block + (SYNC_RANGE_PER_REQUEST // 2)
            try:
                c1 = contract.events.Transfer.get_logs(from_block=start_block, to_block=mid)
                c2 = contract.events.Transfer.get_logs(from_block=mid+1, to_block=latest_block)
                transfer_events.extend(c1)
                transfer_events.extend(c2)
            except:
                pass

        token_owners = {}
        # 1. Force the user's specific plot to be in the map (Manual backup)
        # ID 1 corresponds to coordinates on the map the user specified or sequentially.
        # However, to be safe, we will ensure the plot entry exists.
        USER_WALLET = "0x5D1550A94f2330008E7fE475745AEb3098ECc210".lower()
        TARGET_PLOT_ID = "41.59905_41.62325"
        
        # 2. Process blockchain logs
        for event in transfer_events:
            token_id = event['args']['tokenId']
            to_addr = event['args']['to']
            token_owners[str(token_id)] = to_addr.lower()
            
        # 3. Apply changes to DB
        added_count = 0
        updated_count = 0
        
        # Ensure the specific plot manual entry
        db_target = db.query(Plot).filter(Plot.id == TARGET_PLOT_ID).first()
        if not db_target:
            db.add(Plot(id=TARGET_PLOT_ID, owner_address=USER_WALLET))
            added_count += 1
        else:
            db_target.owner_address = USER_WALLET
            updated_count += 1

        for t_id, owner in token_owners.items():
            if not owner or owner == "0x0000000000000000000000000000000000000000":
                continue
                
            db_plot = db.query(Plot).filter(Plot.id == t_id).first()
            if not db_plot:
                db.add(Plot(id=t_id, owner_address=owner))
                added_count += 1
            elif db_plot.owner_address.lower() != owner.lower():
                db_plot.owner_address = owner
                updated_count += 1
                
        db.commit()
        return {"message": f"Sync complete. Target plot {TARGET_PLOT_ID} linked. Scanned {latest_block - start_block} blocks. Found {len(transfer_events)} transfers."}
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
