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
db_path = os.path.join(os.path.dirname(__file__), "vadsworld.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{db_path}"
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
    is_minted = Column(Boolean, default=False)
    status = Column(String, default="purchased")

class Referral(Base):
    __tablename__ = "referrals"
    id = Column(Integer, primary_key=True, index=True)
    referrer_address = Column(String, index=True)
    referee_address = Column(String, index=True, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

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

    try:
        conn.execute(text("ALTER TABLE plots ADD COLUMN is_minted BOOLEAN DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

    try:
        conn.execute(text("ALTER TABLE plots ADD COLUMN status TEXT DEFAULT 'purchased'"))
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
        USER_WALLET = "0x5D1550A94f2330008E7fE475745AEb3098ECc210".lower()
        TARGET_PLOT_ID = "41.599100_41.623300"
        
        # 2. Process blockchain logs
        for event in transfer_events:
            token_id = str(event['args']['tokenId'])
            to_addr = event['args']['to']
            token_owners[token_id] = to_addr.lower()
            
        # 3. Apply changes to DB
        added_count = 0
        updated_count = 0
        
        # Build a map of existing coordinate plots and their hashes to match with numeric tokenIds
        coord_plots = db.query(Plot).filter(Plot.id.contains('_')).all()
        hash_to_coord_id = {}
        for p in coord_plots:
            # Replicate the JS hash logic: hash = ((hash << 5) - hash) + charCode; hash |= 0;
            h = 0
            for char in p.id:
                h = ((h << 5) - h) + ord(char)
                h &= 0xFFFFFFFF  # Keep it 32-bit
            # Handle signed/unsigned mismatch by using the same logic as JS Math.abs(hash | 0)
            # In Python, we need to mimic the 32-bit signed int behavior
            signed_h = h
            if signed_h > 0x7FFFFFFF:
                signed_h -= 0x100000000
            final_hash = str(abs(signed_h))
            hash_to_coord_id[final_hash] = p.id

        # Ensure the specific plot manual entry
        db_target = db.query(Plot).filter(Plot.id == TARGET_PLOT_ID).first()
        if not db_target:
            db.add(Plot(id=TARGET_PLOT_ID, owner_address=USER_WALLET, is_minted=False))
            added_count += 1
        else:
            db_target.owner_address = USER_WALLET
            updated_count += 1

        for t_id, owner in token_owners.items():
            if not owner or owner == "0x0000000000000000000000000000000000000000":
                continue
                
            # Check if this tokenId belongs to a coordinate plot
            matched_coord_id = hash_to_coord_id.get(t_id)
            if matched_coord_id:
                db_plot = db.query(Plot).filter(Plot.id == matched_coord_id).first()
                if db_plot:
                    db_plot.is_minted = True
                    db_plot.owner_address = owner.lower()
                    updated_count += 1
                    continue

            db_plot = db.query(Plot).filter(Plot.id == t_id).first()
            if not db_plot:
                db.add(Plot(id=t_id, owner_address=owner, is_minted=True))
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
@app.post("/ads/create")
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

@app.get("/admin/plots")
def get_admin_plots(db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
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
    plot_id = purchase.id
    if "_" in plot_id:
        lng, lat = plot_id.split("_")
        edge_lng = round(float(lng) / 0.0002) * 0.0002
        edge_lat = round(float(lat) / 0.0002) * 0.0002
        plot_id = f"{(edge_lng + 0.0001):.6f}_{(edge_lat + 0.0001):.6f}"

    db_plot = db.query(Plot).filter(Plot.id == plot_id).first()
    if db_plot:
        db_plot.owner_address = purchase.owner_address
        db_plot.is_for_sale = False
        db_plot.is_minted = False
        db_plot.status = "purchased"
    else:
        db_plot = Plot(id=plot_id, owner_address=purchase.owner_address, is_for_sale=False, is_minted=False, status="purchased")
        db.add(db_plot)
    db.commit()
    return {"message": "Plot assigned successfully", "id": plot_id}

@app.post("/admin/plots/{plot_id}/mint")
def mint_plot(plot_id: str, db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
    db_plot = db.query(Plot).filter(Plot.id == plot_id).first()
    if not db_plot:
        raise HTTPException(status_code=404, detail="Plot not found")
    db_plot.is_minted = True
    db_plot.status = "minted"
    db.commit()
    return {"message": "Plot marked as minted"}

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

class ReferralCreate(BaseModel):
    referrer_address: str
    referee_address: str

@app.post("/referrals")
def record_referral(ref: ReferralCreate, db: Session = Depends(get_db)):
    # Check if referee already has a referrer
    existing = db.query(Referral).filter(Referral.referee_address.ilike(ref.referee_address)).first()
    if existing:
        return {"message": "Referral already recorded"}
    
    # Don't refer yourself
    if ref.referrer_address.lower() == ref.referee_address.lower():
        return {"message": "Cannot refer yourself"}

    new_ref = Referral(
        referrer_address=ref.referrer_address,
        referee_address=ref.referee_address
    )
    db.add(new_ref)
    db.commit()
    return {"message": "Referral recorded successfully"}

@app.get("/users/{address}/referrals")
def get_referral_stats(address: str, db: Session = Depends(get_db)):
    refs = db.query(Referral).filter(Referral.referrer_address.ilike(address)).all()
    
    output = []
    purchased_count = 0
    no_purchase_count = 0
    
    for r in refs:
        # Check if this referee has any plots
        has_plot = db.query(Plot).filter(Plot.owner_address.ilike(r.referee_address)).first() is not None
        status = "Purchased" if has_plot else "No Purchase yet"
        if has_plot:
            purchased_count += 1
        else:
            no_purchase_count += 1
            
        output.append({
            "address": r.referee_address,
            "status": status,
            "created_at": r.created_at
        })
        
    return {
        "total_referrals": len(refs),
        "purchased_count": purchased_count,
        "no_purchase_count": no_purchase_count,
        "referrals": output
    }

@app.get("/debug/delete-ads/{address}")
def delete_user_ads(address: str, db: Session = Depends(get_db)):
    db.query(Ad).filter(Ad.user_address.ilike(address)).delete(synchronize_session=False)
    db.commit()
    return {"message": f"Deleted all ads for {address}"}

@app.get("/debug/db-dump/{address}")
def dump_db_for_user(address: str, db: Session = Depends(get_db)):
    plots = db.query(Plot).filter(Plot.owner_address.ilike(address)).all()
    ads = db.query(Ad).filter(Ad.user_address.ilike(address)).all()
    return {
        "address": address,
        "plots": [{"id": p.id, "status": p.status, "is_minted": p.is_minted} for p in plots],
        "ads": [{"id": a.id, "status": a.status, "text": a.text} for a in ads]
    }

@app.get("/debug/full-fix/{address}")
def full_fix_user(address: str, db: Session = Depends(get_db)):
    # 1. Delete all ads for this user
    deleted_ads = db.query(Ad).filter(Ad.user_address.ilike(address)).delete(synchronize_session=False)
    
    # 2. Mark all their plots as minted/owned properly
    plots = db.query(Plot).filter(Plot.owner_address.ilike(address)).all()
    for p in plots:
        p.is_minted = True
        p.status = "minted"
    
    db.commit()
    return {
        "message": "Cleanup and Fix performed",
        "deleted_ads": deleted_ads,
        "updated_plots_count": len(plots)
    }

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

@app.delete("/admin/ads/{ad_id}")
def delete_ad(ad_id: int, db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    db.delete(ad)
    db.commit()
    return {"message": "Ad deleted successfully"}

@app.delete("/admin/plots/{plot_id}")
def delete_plot(plot_id: str, db: Session = Depends(get_db), admin: str = Depends(verify_admin_signature)):
    plot = db.query(Plot).filter(Plot.id == plot_id).first()
    if not plot:
        raise HTTPException(status_code=404, detail="Plot not found")
    
    # Also delete associated ads
    db.query(Ad).filter(Ad.lat == plot.id.split('_')[1], Ad.lng == plot.id.split('_')[0]).delete(synchronize_session=False)
    
    db.delete(plot)
    db.commit()
    return {"message": "Plot deleted successfully"}

@app.delete("/ads/plot/{lat}/{lng}")
def delete_ad_by_plot(lat: str, lng: str, db: Session = Depends(get_db)):
    # Delete ads for this plot when ownership changes
    ads = db.query(Ad).filter(Ad.lat == lat, Ad.lng == lng).all()
    for ad in ads:
        db.delete(ad)
    db.commit()
    return {"message": f"Deleted {len(ads)} ads for plot at {lat}, {lng}"}
