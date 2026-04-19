const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const cors = require('cors');
const bodyParser = require('body-parser');
const { ethers } = require('ethers');
const path = require('path');

const app = express();
const port = process.env.PORT || 3000;

app.use(cors());
app.use(bodyParser.json());

// Database Setup
const dbPath = path.resolve(__dirname, 'vadsworld.db');
const db = new sqlite3.Database(dbPath, (err) => {
  if (err) {
    console.error('Error opening database', err.message);
  } else {
    console.log('Connected to the SQLite database.');
    db.run(`CREATE TABLE IF NOT EXISTS ads (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_address TEXT,
      icon TEXT,
      text TEXT,
      link TEXT,
      lat TEXT,
      lng TEXT,
      status TEXT DEFAULT 'pending',
      expiry_date DATETIME
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS plots (
      id TEXT PRIMARY KEY,
      owner_address TEXT,
      purchased_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      is_for_sale BOOLEAN DEFAULT 0,
      price_vim INTEGER DEFAULT 0,
      is_minted BOOLEAN DEFAULT 0
    )`);
  }
});

const OWNER_ADDRESS = (process.env.OWNER_ADDRESS || "0x5D1550A94f2330008E7fE475745AEb3098ECc210").toLowerCase();

const verifyAdminSignature = (req, res, next) => {
  const signature = req.headers['x-signature'];
  const message = req.headers['x-message'];
  if (!signature || !message) return res.status(403).json({ detail: "Missing signature or message" });
  try {
    const recoveredAddress = ethers.verifyMessage(message, signature).toLowerCase();
    if (recoveredAddress !== OWNER_ADDRESS) return res.status(403).json({ detail: "Not authorized" });
    req.admin = recoveredAddress;
    next();
  } catch (error) {
    return res.status(403).json({ detail: "Invalid signature" });
  }
};

// API Router
const api = express.Router();

api.post('/ads', (req, res) => {
  const { user_address, icon, text, link, lat, lng } = req.body;
  db.run(`INSERT INTO ads (user_address, icon, text, link, lat, lng, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')`, 
    [user_address, icon, text, link, lat, lng], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Ad submitted successfully", id: this.lastID });
  });
});

api.get('/ads', (req, res) => {
  const now = new Date().toISOString();
  db.all(`SELECT * FROM ads WHERE status = 'approved' AND (expiry_date IS NULL OR expiry_date > ?)`, [now], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

api.get('/plots', (req, res) => {
  db.all(`SELECT * FROM plots`, [], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

api.get('/users/:address/plots', (req, res) => {
  const address = req.params.address.toLowerCase();
  db.all(`SELECT * FROM plots WHERE lower(owner_address) = ?`, [address], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

api.get('/users/:address/ads', (req, res) => {
  const address = req.params.address.toLowerCase();
  db.all(`SELECT * FROM ads WHERE lower(user_address) = ?`, [address], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

api.post('/sync-plots', (req, res) => {
  // აქ ვამატებთ მიწებს, რომლებიც "ნაყიდია" (მაგალითად ბსც სკანიდან)
  // შეგვიძლია რამდენიმე კონკრეტული კოორდინატი ჩავწეროთ
  const demoPlots = [
    { id: "41.59905_41.62325", owner: OWNER_ADDRESS },
    { id: "41.60000_41.62500", owner: OWNER_ADDRESS }, // დამატებითი მიწა
    { id: "41.71211_44.75025", owner: OWNER_ADDRESS }  // მიწა თბილისში მაგალითისთვის
  ];

  let completionCount = 0;
  demoPlots.forEach(plot => {
    db.run(`INSERT OR IGNORE INTO plots (id, owner_address) VALUES (?, ?)`, [plot.id, plot.owner], (err) => {
      completionCount++;
      if (completionCount === demoPlots.length) {
        res.json({ message: `Sync complete! Found ${demoPlots.length} plots associated with your address.` });
      }
    });
  });
});

api.post('/plots/buy', (req, res) => {
  const { id, owner_address } = req.body;
  db.run(`INSERT OR REPLACE INTO plots (id, owner_address, is_for_sale, price_vim) VALUES (?, ?, 0, 0)`, [id, owner_address], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Plot purchased successfully" });
  });
});

api.post('/plots/fiat-purchase', (req, res) => {
  const { id, owner_address } = req.body;
  db.run(`INSERT OR REPLACE INTO plots (id, owner_address, is_for_sale, price_vim) VALUES (?, ?, 0, 0)`, [id, owner_address], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Fiat purchase recorded successfully" });
  });
});

api.post('/plots/sell', (req, res) => {
  const { id, owner_address, price_vim } = req.body;
  db.run(`UPDATE plots SET is_for_sale = 1, price_vim = ? WHERE id = ? AND owner_address = ?`, [price_vim, id, owner_address], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    if (this.changes === 0) return res.status(404).json({ detail: "Ownership mismatch" });
    res.json({ message: "Plot listed for sale" });
  });
});

api.post('/admin/plots/claim', verifyAdminSignature, (req, res) => {
  const { id } = req.body;
  db.run(`INSERT OR REPLACE INTO plots (id, owner_address) VALUES (?, ?)`, [id, req.admin], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Plot claimed by admin" });
  });
});

api.get('/admin/ads', verifyAdminSignature, (req, res) => {
  db.all(`SELECT * FROM ads WHERE status = 'pending'`, [], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

api.get('/admin/ads/all', verifyAdminSignature, (req, res) => {
  db.all(`SELECT * FROM ads ORDER BY id DESC`, [], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

api.post('/admin/plots/:id/mint', verifyAdminSignature, (req, res) => {
  db.run(`UPDATE plots SET is_minted = 1 WHERE id = ?`, [req.params.id], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Plot marked as minted" });
  });
});

api.post('/admin/plots/clear', verifyAdminSignature, (req, res) => {
  db.run(`DELETE FROM plots`, (err) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Cache cleared" });
  });
});

api.post('/admin/ads/:ad_id/approve', verifyAdminSignature, (req, res) => {
  const expiry = new Date();
  expiry.setDate(expiry.getDate() + 30);
  db.run(`UPDATE ads SET status = 'approved', expiry_date = ? WHERE id = ?`, [expiry.toISOString(), req.params.ad_id], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Ad approved" });
  });
});

api.post('/admin/ads/:ad_id/reject', verifyAdminSignature, (req, res) => {
  db.run(`UPDATE ads SET status = 'rejected' WHERE id = ?`, [req.params.ad_id], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Ad rejected" });
  });
});

api.delete('/ads/plot/:lat/:lng', (req, res) => {
  db.run(`DELETE FROM ads WHERE lat = ? AND lng = ?`, [req.params.lat, req.params.lng], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Ads deleted" });
  });
});

// Use API routes
app.use('/api/v1', api);

// Serve static files from 'out' - This must come AFTER API routes
const outPath = path.join(__dirname, '../out');
app.use(express.static(outPath));

// Fallback for SPA (index.html)
app.get('*', (req, res) => {
  res.sendFile(path.join(outPath, 'index.html'));
});

app.listen(port, () => {
  console.log(`Unified Server running at port ${port}`);
});
