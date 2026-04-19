const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const cors = require('cors');
const bodyParser = require('body-parser');
const { ethers } = require('ethers');
const path = require('path');

const app = express();
const port = 8000;

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
    )`, (err) => {
      if (err) {
        console.error('Error creating ads table', err.message);
      } else {
        db.run(`ALTER TABLE ads ADD COLUMN expiry_date DATETIME`, (err) => {
          if (err && !err.message.includes('duplicate column name')) {
            // Ignore if column already exists
          }
        });
      }
    });

    db.run(`CREATE TABLE IF NOT EXISTS plots (
      id TEXT PRIMARY KEY,
      owner_address TEXT,
      purchased_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      is_for_sale BOOLEAN DEFAULT 0,
      price_vim INTEGER DEFAULT 0,
      is_minted BOOLEAN DEFAULT 0
    )`, (err) => {
      if (err) console.error('Error creating plots table', err.message);
      else {
        db.run(`ALTER TABLE plots ADD COLUMN is_for_sale BOOLEAN DEFAULT 0`, (err) => {});
        db.run(`ALTER TABLE plots ADD COLUMN price_vim INTEGER DEFAULT 0`, (err) => {});
        db.run(`ALTER TABLE plots ADD COLUMN is_minted BOOLEAN DEFAULT 0`, (err) => {});
      }
    });
  }
});

const OWNER_ADDRESS = (process.env.OWNER_ADDRESS || "0x5D1550A94f2330008E7fE475745AEb3098ECc210").toLowerCase();

const verifyAdminSignature = (req, res, next) => {
  const signature = req.headers['x-signature'];
  const message = req.headers['x-message'];

  if (!signature || !message) {
    return res.status(403).json({ detail: "Missing signature or message" });
  }

  try {
    const recoveredAddress = ethers.verifyMessage(message, signature).toLowerCase();
    if (recoveredAddress !== OWNER_ADDRESS) {
      return res.status(403).json({ detail: "Not authorized" });
    }
    req.admin = recoveredAddress;
    next();
  } catch (error) {
    console.error("Signature verification failed:", error);
    return res.status(403).json({ detail: "Invalid signature" });
  }
};

app.post('/ads', (req, res) => {
  const { user_address, icon, text, link, lat, lng } = req.body;
  const query = `INSERT INTO ads (user_address, icon, text, link, lat, lng, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')`;
  db.run(query, [user_address, icon, text, link, lat, lng], function(err) {
    if (err) {
      return res.status(500).json({ detail: err.message });
    }
    res.json({ message: "Ad submitted successfully, pending approval.", id: this.lastID });
  });
});

app.get('/ads', (req, res) => {
  const now = new Date().toISOString();
  const query = `SELECT * FROM ads WHERE status = 'approved' AND (expiry_date IS NULL OR expiry_date > ?)`;
  db.all(query, [now], (err, rows) => {
    if (err) {
      return res.status(500).json({ detail: err.message });
    }
    res.json(rows);
  });
});

app.get('/plots', (req, res) => {
  db.all(`SELECT * FROM plots`, [], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

app.get('/users/:address/plots', (req, res) => {
  const address = req.params.address.toLowerCase();
  db.all(`SELECT * FROM plots WHERE lower(owner_address) = ?`, [address], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

app.get('/users/:address/ads', (req, res) => {
  const address = req.params.address.toLowerCase();
  db.all(`SELECT * FROM ads WHERE lower(user_address) = ?`, [address], (err, rows) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json(rows);
  });
});

app.post('/sync-plots', (req, res) => {
  db.run(`INSERT OR IGNORE INTO plots (id, owner_address) VALUES (?, ?)`, ["41.59905_41.62325", OWNER_ADDRESS]);
  res.json({ message: "Sync complete! Check your dashboard." });
});

app.post('/plots/buy', (req, res) => {
  const { id, owner_address } = req.body;
  db.run(`INSERT OR REPLACE INTO plots (id, owner_address, is_for_sale, price_vim) VALUES (?, ?, 0, 0)`, [id, owner_address], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Plot purchased successfully" });
  });
});

app.post('/plots/fiat-purchase', (req, res) => {
  const { id, owner_address } = req.body;
  db.run(`INSERT OR REPLACE INTO plots (id, owner_address, is_for_sale, price_vim) VALUES (?, ?, 0, 0)`, [id, owner_address], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Fiat purchase recorded successfully" });
  });
});

app.post('/plots/sell', (req, res) => {
  const { id, owner_address, price_vim } = req.body;
  db.run(`UPDATE plots SET is_for_sale = 1, price_vim = ? WHERE id = ? AND owner_address = ?`, [price_vim, id, owner_address], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    if (this.changes === 0) return res.status(404).json({ detail: "Plot not found or you don't own it" });
    res.json({ message: "Plot listed for sale successfully" });
  });
});

app.post('/admin/plots/claim', verifyAdminSignature, (req, res) => {
  const { id } = req.body;
  const owner_address = req.admin;
  db.run(`INSERT OR REPLACE INTO plots (id, owner_address) VALUES (?, ?)`, [id, owner_address], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Plot claimed by admin successfully" });
  });
});

app.get('/admin/ads', verifyAdminSignature, (req, res) => {
  const query = `SELECT * FROM ads WHERE status = 'pending'`;
  db.all(query, [], (err, rows) => {
    if (err) {
      return res.status(500).json({ detail: err.message });
    }
    res.json(rows);
  });
});

app.get('/admin/ads/all', verifyAdminSignature, (req, res) => {
  const query = `SELECT * FROM ads ORDER BY id DESC`;
  db.all(query, [], (err, rows) => {
    if (err) {
      return res.status(500).json({ detail: err.message });
    }
    res.json(rows);
  });
});

app.post('/admin/plots/:id/mint', verifyAdminSignature, (req, res) => {
  const plotId = req.params.id;
  db.run(`UPDATE plots SET is_minted = 1 WHERE id = ?`, [plotId], function(err) {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Plot marked as minted" });
  });
});

app.post('/admin/plots/clear', verifyAdminSignature, (req, res) => {
  db.run(`DELETE FROM plots`, (err) => {
    if (err) return res.status(500).json({ detail: err.message });
    res.json({ message: "Plots cache cleared" });
  });
});

app.post('/admin/ads/:ad_id/approve', verifyAdminSignature, (req, res) => {
  const adId = req.params.ad_id;
  const expiryDate = new Date();
  expiryDate.setDate(expiryDate.getDate() + 30);
  const expiryDateStr = expiryDate.toISOString();

  const query = `UPDATE ads SET status = 'approved', expiry_date = ? WHERE id = ?`;
  db.run(query, [expiryDateStr, adId], function(err) {
    if (err) {
      return res.status(500).json({ detail: err.message });
    }
    res.json({ message: "Ad approved", expiry_date: expiryDateStr });
  });
});

app.post('/admin/ads/:ad_id/reject', verifyAdminSignature, (req, res) => {
  const adId = req.params.ad_id;
  const query = `UPDATE ads SET status = 'rejected' WHERE id = ?`;
  db.run(query, [adId], function(err) {
    if (err) {
      return res.status(500).json({ detail: err.message });
    }
    res.json({ message: "Ad rejected" });
  });
});

app.delete('/ads/plot/:lat/:lng', (req, res) => {
  const { lat, lng } = req.params;
  const query = `DELETE FROM ads WHERE lat = ? AND lng = ?`;
  db.run(query, [lat, lng], function(err) {
    if (err) {
      return res.status(500).json({ detail: err.message });
    }
    res.json({ message: `Deleted ${this.changes} ads for plot at ${lat}, ${lng}` });
  });
});

app.listen(port, () => {
  console.log(`VadsWorld API listening at http://localhost:${port}`);
});
