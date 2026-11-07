"""
SQLite Database Connection and Migrations for Neo-D LedgerCraft
Uses WAL (Write-Ahead Logging) mode for concurrent access and ACID compliance.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional


def get_connection(db_path: str = "./data/ledger.db") -> sqlite3.Connection:
    """Returns a SQLite connection with WAL enabled and row factory configured."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Initializes tables for receipts, double-entry journal, accounts, and vendor memory."""
    with conn:
        conn.executescript(
            """
            -- Accounts Table
            CREATE TABLE IF NOT EXISTS accounts (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL, -- asset, liability, equity, revenue, expense
                currency TEXT NOT NULL DEFAULT 'AUD'
            );

            -- Receipts & Documents
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash TEXT UNIQUE,
                original_filename TEXT,
                local_path TEXT,
                gdrive_file_id TEXT,
                vendor TEXT NOT NULL,
                date TEXT NOT NULL, -- YYYY-MM-DD
                total_amount REAL NOT NULL,
                tax_amount REAL NOT NULL DEFAULT 0.0,
                currency TEXT NOT NULL DEFAULT 'AUD',
                category TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT 'business', -- business or personal
                tax_label TEXT,
                status TEXT NOT NULL DEFAULT 'recorded', -- recorded, flagged, corrected
                flag_reason TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Double-entry Journal Transactions
            CREATE TABLE IF NOT EXISTS journal_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_date TEXT NOT NULL,
                description TEXT NOT NULL,
                reference TEXT, -- e.g. receipt:12 or bank:stmt_1
                source TEXT NOT NULL, -- receipt, bank_statement, stock_move, manual
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Journal Line Items (Legs)
            CREATE TABLE IF NOT EXISTS journal_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id INTEGER NOT NULL REFERENCES journal_transactions(id) ON DELETE CASCADE,
                account_code TEXT NOT NULL REFERENCES accounts(code),
                debit REAL NOT NULL DEFAULT 0.0,
                credit REAL NOT NULL DEFAULT 0.0,
                description TEXT,
                CHECK (debit >= 0 AND credit >= 0 AND (debit > 0 OR credit > 0))
            );

            -- Vendor Memory (Learning from corrections)
            CREATE TABLE IF NOT EXISTS vendor_memory (
                vendor_pattern TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                default_category TEXT NOT NULL,
                default_purpose TEXT NOT NULL DEFAULT 'business',
                default_tax_label TEXT,
                confidence REAL NOT NULL DEFAULT 1.0,
                correction_count INTEGER NOT NULL DEFAULT 1,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Bank Statement Lines
            CREATE TABLE IF NOT EXISTS bank_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_code TEXT NOT NULL REFERENCES accounts(code),
                stmt_date TEXT NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL, -- negative for debit/spent, positive for deposit
                currency TEXT NOT NULL DEFAULT 'AUD',
                matched_receipt_id INTEGER REFERENCES receipts(id),
                status TEXT NOT NULL DEFAULT 'unmatched', -- matched, unmatched, manual
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- ADHD Desk Companion & Non-Punitive Reward Box State
            CREATE TABLE IF NOT EXISTS companion_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total_xp INTEGER NOT NULL DEFAULT 0,
                reward_name TEXT NOT NULL DEFAULT '2 Packs of Indomie Goreng 🍜',
                reward_target_xp INTEGER NOT NULL DEFAULT 100,
                boxes_unlocked INTEGER NOT NULL DEFAULT 0,
                last_anim TEXT NOT NULL DEFAULT 'idle',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            INSERT OR IGNORE INTO companion_state (id, total_xp, reward_name, reward_target_xp, boxes_unlocked, last_anim)
            VALUES (1, 0, '2 Packs of Indomie Goreng 🍜', 100, 0, 'idle');
            """
        )
