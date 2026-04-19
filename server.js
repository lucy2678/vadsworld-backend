const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const cors = require('cors');
const bodyParser = require('body-parser');
const { ethers } = require('ethers');
const path = require('path');

const app = express();
const port = 8000; // ვაბრუნებ 8000-ზე, რომ Next.js-ის rewrites-მა იპოვოს

app.use(cors());
app.use(bodyParser.json());

// Database Setup
const dbPath = path.resolve(__dirname, 'vadsworld.db');
const db = new sqlite3.Database(dbPath, (err) => {
  if (err) console.error('Error opening database', err.message);
  else {
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

const OWNER_ADDRESS = "0x5D1550A94f2330008E7fE475745AEb3098ECc210".toLowerCase();

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

app.post('/api/v1/ads', (req, res) => {
  const { user_address, icon, text, link, lat, lng } = req.body;
  db.run(`INSERT INTO ads (user_address, icon, text, link, lat, lng, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')`, 
    [user_address, icon, text, link, lat, lng], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Ad submitted", id: this.lastID });
  });
});

app.get('/api/v1/ads', (req, res) => {
  const now = new Date().toISOString();
  db.all(`SELECT * FROM ads WHERE status = 'approved' AND (expiry_date IS NULL OR expiry_date > ?)`, [now], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

app.get('/api/v1/plots', (req, res) => {
  db.all(`SELECT * FROM plots`, [], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

app.get('/api/v1/users/:address/plots', (req, res) => {
  const address = req.params.address.toLowerCase();
  db.all(`SELECT * FROM plots WHERE lower(owner_address) = ?`, [address], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

app.get('/api/v1/users/:address/ads', (req, res) => {
  const address = req.params.address.toLowerCase();
  db.all(`SELECT * FROM ads WHERE lower(user_address) = ?`, [address], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

app.post('/api/v1/sync-plots', (req, res) => {
  // მხოლოდ შენი ნაყიდი მიწა
  const demoPlots = [
    { id: "41.59905_41.62325", owner: OWNER_ADDRESS }
  ];
  let count = 0;
  demoPlots.forEach(p => {
    db.run(`INSERT OR IGNORE INTO plots (id, owner_address) VALUES (?, ?)`, [p.id, p.owner], () => {
      count++;
      if (count === demoPlots.length) res.json({ message: "Sync complete! Check your dashboard." });
    });
  });
});

app.post('/api/v1/plots/buy', (req, res) => {
  const { id, owner_address } = req.body;
  db.run(`INSERT OR REPLACE INTO plots (id, owner_address) VALUES (?, ?)`, [id, owner_address], (err) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Success" });
  });
});

app.get('/api/v1/admin/ads', verifyAdminSignature, (req, res) => {
  db.all(`SELECT * FROM ads WHERE status = 'pending'`, (err, rows) => {
    res.json(rows);
  });
});

app.post('/api/v1/admin/plots/clear', verifyAdminSignature, (req, res) => {
  db.run(`DELETE FROM plots`, () => res.json({ message: "Cleared" }));
});

app.listen(port, () => {
  console.log(`Backend listening at http://localhost:${port}`);
});
